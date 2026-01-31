"""
Incident Resolution Requests (Redpanda-first)

This module provides a single, shared, idempotent entrypoint for requesting
incident resolution work, plus worker-side helpers to claim and update status.

Source of truth: Incident.action_result["resolution"] (durable state).
Redpanda message: trigger only (not a system-of-record).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Literal

from sqlalchemy.orm import Session

from src.database.models import Incident, LogEntry
from src.database.database import SessionLocal
from src.services.redpanda_service import redpanda_service
from src.services.rca_cursor_slack import rca_cursor_slack_flow

ResolutionStatus = Literal["queued", "running", "completed", "failed"]


@dataclass(frozen=True)
class ResolutionRequestResult:
    enqueued: bool
    resolution_status: ResolutionStatus
    reason: str


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _get_action_result_dict(incident: Incident) -> Dict[str, Any]:
    ar = incident.action_result
    if isinstance(ar, dict):
        return ar
    return {}


def _get_resolution_meta(incident: Incident) -> Dict[str, Any]:
    ar = _get_action_result_dict(incident)
    meta = ar.get("resolution")
    if isinstance(meta, dict):
        return meta
    return {}


def _set_resolution_meta(incident: Incident, meta: Dict[str, Any]) -> None:
    ar = _get_action_result_dict(incident)
    ar["resolution"] = meta
    incident.action_result = ar


def ensure_incident_resolution_requested(
    *,
    db: Session,
    incident_id: int,
    requested_by_user_id: int,
    requested_by_trigger: str,
) -> ResolutionRequestResult:
    """
    Idempotently ensure a resolve_incident task is queued for an incident.

    This function is safe to call from multiple places (FE open, incident creation).
    It performs a durable status update before publishing the Redpanda task.
    """
    incident = (
        db.query(Incident)
        .filter(Incident.id == incident_id, Incident.user_id == requested_by_user_id)
        .first()
    )
    if not incident:
        return ResolutionRequestResult(
            enqueued=False, resolution_status="failed", reason="incident_not_found"
        )

    meta = _get_resolution_meta(incident)
    status: Optional[str] = meta.get("status")

    # If already queued/running/completed, do not enqueue again.
    if status in ("queued", "running", "completed"):
        return ResolutionRequestResult(
            enqueued=False,
            resolution_status=status,  # type: ignore[return-value]
            reason="already_" + status,
        )

    # Transition to queued (durable) before publish
    queued_meta = {
        "status": "queued",
        "requested_at": _now_iso(),
        "requested_by_user_id": requested_by_user_id,
        "requested_by_trigger": requested_by_trigger,
        "started_at": meta.get("started_at"),
        "completed_at": meta.get("completed_at"),
        "last_error": None,
    }
    _set_resolution_meta(incident, queued_meta)
    db.commit()

    task_data = {
        "task_type": "resolve_incident",
        "incident_id": incident_id,
        "requested_by_user_id": requested_by_user_id,
        "requested_by_trigger": requested_by_trigger,
        "requested_at": queued_meta["requested_at"],
    }

    published = redpanda_service.producer.publish_incident_task(
        task_data, key=str(incident_id)
    )
    if not published:
        # Fallback: run resolution inline so we still get analysis + RCA + Cursor + Slack
        import logging
        logging.getLogger(__name__).info(
            "Redpanda publish failed for incident %s, running resolution inline",
            incident_id,
        )
        try:
            run_incident_resolution_job(
                incident_id=incident_id,
                requested_by_user_id=requested_by_user_id,
            )
        except Exception as e:
            failed_meta = dict(queued_meta)
            failed_meta["status"] = "failed"
            failed_meta["last_error"] = f"inline_fallback_error: {str(e)[:200]}"
            _set_resolution_meta(incident, failed_meta)
            db.commit()
            return ResolutionRequestResult(
                enqueued=False, resolution_status="failed", reason="publish_failed_and_inline_failed"
            )
        return ResolutionRequestResult(
            enqueued=False, resolution_status="completed", reason="ran_inline_after_publish_failed"
        )

    return ResolutionRequestResult(
        enqueued=True, resolution_status="queued", reason="enqueued"
    )


def try_claim_incident_resolution(*, db: Session, incident_id: int) -> bool:
    """
    Worker-side idempotent claim.

    Only one worker should be able to transition queued -> running.
    """
    # Attempt to lock row to avoid multiple concurrent claims.
    q = db.query(Incident).filter(Incident.id == incident_id)
    try:
        q = q.with_for_update()  # type: ignore[attr-defined]
    except Exception:
        # SQLite or unsupported dialect; best-effort without row lock.
        pass

    incident = q.first()
    if not incident:
        return False

    meta = _get_resolution_meta(incident)
    if meta.get("status") != "queued":
        return False

    running_meta = dict(meta)
    running_meta["status"] = "running"
    running_meta["started_at"] = _now_iso()
    running_meta["last_error"] = None
    _set_resolution_meta(incident, running_meta)
    db.commit()
    return True


def mark_incident_resolution_completed(*, db: Session, incident_id: int) -> None:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        return
    meta = _get_resolution_meta(incident)
    new_meta = dict(meta)
    new_meta["status"] = "completed"
    new_meta["completed_at"] = _now_iso()
    new_meta["last_error"] = None
    _set_resolution_meta(incident, new_meta)
    db.commit()


def mark_incident_resolution_failed(*, db: Session, incident_id: int, error: str) -> None:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        return
    meta = _get_resolution_meta(incident)
    new_meta = dict(meta)
    new_meta["status"] = "failed"
    new_meta["completed_at"] = _now_iso()
    new_meta["last_error"] = error[:500]
    _set_resolution_meta(incident, new_meta)
    db.commit()


def run_incident_resolution_job(*, incident_id: int, requested_by_user_id: int) -> Dict[str, Any]:
    """
    Heavy worker job: run analysis + resolution pipeline and persist results.

    This is executed in a threadpool from the Redpanda consumer handler.
    """
    db = SessionLocal()
    try:
        incident = (
            db.query(Incident)
            .filter(Incident.id == incident_id, Incident.user_id == requested_by_user_id)
            .first()
        )
        if not incident:
            return {"success": False, "error": "incident_not_found"}

        logs = []
        if incident.log_ids:
            log_id_list = incident.log_ids if isinstance(incident.log_ids, list) else []
            if log_id_list:
                logs = (
                    db.query(LogEntry)
                    .filter(LogEntry.id.in_(log_id_list), LogEntry.user_id == requested_by_user_id)
                    .order_by(LogEntry.timestamp.desc())
                    .all()
                )

        # Run RCA + Cursor prompt + Slack only (no analyze_incident_with_openrouter).
        result = None
        try:
            rca_cursor_slack_flow(incident_id, requested_by_user_id)
            result = True
        except Exception as rca_err:
            import logging
            logging.getLogger(__name__).warning(
                "rca_cursor_slack_flow failed for incident %s (Redpanda path): %s",
                incident_id,
                rca_err,
            )
        return {"success": True, "result": result}
    finally:
        db.close()

