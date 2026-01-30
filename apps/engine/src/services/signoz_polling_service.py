"""
SigNoz Polling Service - Fetch error logs and error traces from SigNoz and create LogEntries.
Only ERROR/CRITICAL logs and error spans are stored; no other log levels.
"""
import logging
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.auth.crypto_utils import decrypt_token
from src.database.database import SessionLocal
from src.database.models import Integration, LogEntry
from src.integrations.signoz.client import fetch_error_events
from src.memory import ensure_partition_exists_for_timestamp

logger = logging.getLogger("signoz_polling_service")


@dataclass
class SigNozPollingConfig:
    """Configuration for SigNoz polling."""
    polling_interval_minutes: int = 10
    max_consecutive_errors: int = 5
    error_backoff_minutes: int = 30
    first_run_lookback_minutes: int = 15


def _get_signoz_integrations(db: Session) -> List[Integration]:
    """Return list of Integration where provider=SIGNOZ and status=ACTIVE."""
    return (
        db.query(Integration)
        .filter(
            Integration.provider == "SIGNOZ",
            Integration.status == "ACTIVE",
        )
        .all()
    )


def _get_config(integration: Integration) -> tuple[Optional[str], Optional[str], int, int]:
    """Get signoz_url, decrypted api_key, start_ts_ms, end_ts_ms from integration config."""
    config = integration.config or {}
    if not isinstance(config, dict):
        return None, None, 0, 0
    url = config.get("signoz_url") or (config.get("url") or "").strip()
    enc_key = config.get("signoz_api_key") or config.get("api_key")
    if not url or not enc_key:
        return None, None, 0, 0
    try:
        api_key = decrypt_token(enc_key)
    except Exception as e:
        logger.warning(f"Failed to decrypt SigNoz API key for integration {integration.id}: {e}")
        return url, None, 0, 0
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_ts = config.get("last_processed_end_ts")
    if last_ts and isinstance(last_ts, (int, float)):
        start_ms = int(last_ts)
    else:
        start_ms = end_ms - (SigNozPollingConfig().first_run_lookback_minutes * 60 * 1000)
    return url, api_key, start_ms, end_ms


def _update_last_processed(db: Session, integration: Integration, end_ts_ms: int) -> None:
    """Update integration config last_processed_end_ts to end_ts_ms."""
    config = dict(integration.config or {})
    config["last_processed_end_ts"] = end_ts_ms
    integration.config = config
    flag_modified(integration, "config")
    db.commit()


def _run_one_cycle() -> None:
    """Load active SigNoz integrations, fetch error events, create LogEntries, publish tasks."""
    from src.services.redpanda_task_processor import publish_log_processing_task

    db = SessionLocal()
    try:
        integrations = _get_signoz_integrations(db)
        if not integrations:
            return
        for integration in integrations:
            url, api_key, start_ms, end_ms = _get_config(integration)
            if not url or not api_key:
                continue
            try:
                events = fetch_error_events(url, api_key, start_ms, end_ms)
            except Exception as e:
                logger.warning(f"SigNoz fetch failed for integration {integration.id}: {e}")
                continue
            for ev in events:
                try:
                    log_ts = datetime.fromtimestamp(ev["timestamp_ms"] / 1000.0, tz=timezone.utc)
                except Exception:
                    log_ts = datetime.now(timezone.utc)
                ensure_partition_exists_for_timestamp(log_ts)
                log_entry = LogEntry(
                    service_name=ev.get("service_name") or "unknown",
                    level=ev.get("severity") or "ERROR",
                    severity=ev.get("severity") or "ERROR",
                    message=ev.get("message") or "Error from SigNoz",
                    source="signoz",
                    integration_id=integration.id,
                    user_id=integration.user_id,
                    metadata_json=ev.get("metadata"),
                    timestamp=log_ts,
                )
                db.add(log_entry)
                db.commit()
                db.refresh(log_entry)
                publish_log_processing_task(log_entry.id)
            # Update cursor after each successful run (plan: update last_processed_end_ts after each successful run)
            _update_last_processed(db, integration, end_ms)
    finally:
        db.close()


def run_polling_loop(
    config: Optional[SigNozPollingConfig] = None,
    shutdown_event: Optional[threading.Event] = None,
) -> None:
    """Run polling loop with sleep(interval); support graceful shutdown via shutdown_event."""
    cfg = config or SigNozPollingConfig()
    stop = shutdown_event or threading.Event()
    consecutive_errors = 0
    while not stop.is_set():
        try:
            _run_one_cycle()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            logger.exception("SigNoz polling cycle failed: %s", e)
            if consecutive_errors >= cfg.max_consecutive_errors:
                wait_sec = cfg.error_backoff_minutes * 60
                logger.warning("Backing off for %s minutes", cfg.error_backoff_minutes)
                stop.wait(timeout=wait_sec)
                continue
        wait_sec = cfg.polling_interval_minutes * 60
        stop.wait(timeout=wait_sec)


def start_signoz_polling_background(config: Optional[SigNozPollingConfig] = None) -> threading.Event:
    """Start SigNoz polling in a daemon thread; return Event to signal shutdown."""
    shutdown = threading.Event()
    thread = threading.Thread(
        target=run_polling_loop,
        kwargs={"config": config, "shutdown_event": shutdown},
        name="signoz-polling",
        daemon=True,
    )
    thread.start()
    logger.info("SigNoz polling service started (background thread)")
    return shutdown
