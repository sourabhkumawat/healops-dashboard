"""
Redpanda Task Processor - Sequential log processing via Redpanda consumer.
Replaces Celery background tasks for better reliability and sequential processing.
"""
import sys
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import asyncio
import time
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

from src.services.redpanda_service import redpanda_service
from src.services.linear_ticket_resolver import process_ticket_task_from_redpanda
from src.services.incident_resolution_requests import (
    ensure_incident_resolution_requested,
    try_claim_incident_resolution,
    mark_incident_resolution_completed,
    mark_incident_resolution_failed,
    run_incident_resolution_job,
)
from src.services.rca_cursor_slack import rca_cursor_slack_flow
from src.database.database import SessionLocal
from src.database.models import LogEntry, Incident, IncidentSeverity, IntegrationStatus, IntegrationStatusEnum, Integration
from src.core.ai_analysis import generate_incident_title_and_description, build_enhanced_linear_description
from src.utils.integrations import get_github_integration_for_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError as FuturesTimeoutError

# Global event loop reference for async operations
_main_event_loop = None

# Performance monitoring
class PerformanceMetrics:
    """Track performance metrics for optimization monitoring."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset all metrics."""
        self.total_messages_processed = 0
        self.total_processing_time = 0.0
        self.database_query_count = 0
        self.database_commit_count = 0
        self.linear_issue_creation_time = 0.0
        self.linear_issues_created_async = 0
        self.linear_issues_created_sync = 0
        self.integration_cache_hits = 0
        self.integration_cache_misses = 0

    def record_message_processing(self, processing_time: float):
        """Record message processing time."""
        self.total_messages_processed += 1
        self.total_processing_time += processing_time

    def record_database_query(self):
        """Record a database query."""
        self.database_query_count += 1

    def record_database_commit(self):
        """Record a database commit."""
        self.database_commit_count += 1

    def record_linear_issue_creation(self, creation_time: float, is_async: bool = True):
        """Record Linear issue creation time and method."""
        self.linear_issue_creation_time += creation_time
        if is_async:
            self.linear_issues_created_async += 1
        else:
            self.linear_issues_created_sync += 1

    def record_integration_cache_hit(self):
        """Record integration cache hit."""
        self.integration_cache_hits += 1

    def record_integration_cache_miss(self):
        """Record integration cache miss."""
        self.integration_cache_misses += 1

    def get_average_processing_time(self) -> float:
        """Get average message processing time in milliseconds."""
        if self.total_messages_processed == 0:
            return 0.0
        return (self.total_processing_time / self.total_messages_processed) * 1000

    def get_database_queries_per_message(self) -> float:
        """Get average database queries per message."""
        if self.total_messages_processed == 0:
            return 0.0
        return self.database_query_count / self.total_messages_processed

    def get_cache_hit_rate(self) -> float:
        """Get integration cache hit rate percentage."""
        total_cache_attempts = self.integration_cache_hits + self.integration_cache_misses
        if total_cache_attempts == 0:
            return 0.0
        return (self.integration_cache_hits / total_cache_attempts) * 100

    def get_summary(self) -> Dict[str, Any]:
        """Get performance metrics summary."""
        return {
            "total_messages_processed": self.total_messages_processed,
            "average_processing_time_ms": round(self.get_average_processing_time(), 2),
            "database_queries_per_message": round(self.get_database_queries_per_message(), 2),
            "database_commits_total": self.database_commit_count,
            "linear_issues_created_async": self.linear_issues_created_async,
            "linear_issues_created_sync": self.linear_issues_created_sync,
            "linear_async_percentage": round(
                (self.linear_issues_created_async / max(1, self.linear_issues_created_async + self.linear_issues_created_sync)) * 100, 2
            ),
            "integration_cache_hit_rate": round(self.get_cache_hit_rate(), 2),
            "total_linear_creation_time": round(self.linear_issue_creation_time, 3)
        }

# Global performance metrics instance
performance_metrics = PerformanceMetrics()

# Global executor for incident resolution jobs (keep bounded)
_incident_resolution_executor = ThreadPoolExecutor(
    max_workers=3,
    thread_name_prefix="incident-resolver",
)

@contextmanager
def performance_timer(metric_name: str = "operation"):
    """Context manager for timing operations."""
    start_time = time.time()
    try:
        yield
    finally:
        end_time = time.time()
        duration = end_time - start_time
        print(f"⏱️  {metric_name} took {duration * 1000:.2f}ms")


def trigger_incident_analysis_async(incident_id: int, user_id: int):
    """
    Legacy helper (deprecated).

    Do not use for incident resolution. All resolution work should be scheduled
    via Redpanda tasks (e.g., `resolve_incident`).
    """
    print(
        "⚠️  trigger_incident_analysis_async is deprecated; "
        "use ensure_incident_resolution_requested(...) instead."
    )


def route_incident_task(task_data: Dict[str, Any]):
    """
    Route incident-topic tasks by task_type.

    - process_log_entry: existing log->incident processing
    - resolve_incident: heavy incident resolution (analysis + RCA + Cursor + Slack)
    - rca_cursor_slack: deep RCA + Cursor prompt + Slack only (incident must have root_cause)
    """
    task_type = task_data.get("task_type")
    if task_type == "process_log_entry":
        return process_log_entry_from_redpanda(task_data)
    if task_type == "resolve_incident":
        return handle_resolve_incident_task(task_data)
    if task_type == "rca_cursor_slack":
        return handle_rca_cursor_slack_task(task_data)
    print(f"⚠️  Unknown incident task type: {task_type}")
    return None


def handle_rca_cursor_slack_task(task_data: Dict[str, Any]):
    """
    Consume from Redpanda: run deep RCA, create Cursor prompt, persist to action_result, send to Slack.
    Incident should already have root_cause (e.g. from prior analysis).
    """
    incident_id = task_data.get("incident_id")
    user_id = task_data.get("user_id")
    if not incident_id:
        print("⚠️  rca_cursor_slack task missing incident_id")
        return None
    uid = user_id
    if uid is None:
        db = SessionLocal()
        try:
            inc = db.query(Incident).filter(Incident.id == incident_id).first()
            if inc:
                uid = inc.user_id
        finally:
            db.close()
    print(f"🔄 [Redpanda Consumer] Running RCA + Cursor + Slack for incident {incident_id}")
    try:
        rca_cursor_slack_flow(int(incident_id), uid)
        print(f"✓ RCA + Cursor + Slack completed for incident {incident_id}")
        return f"rca_cursor_slack done: {incident_id}"
    except Exception as e:
        print(f"✗ RCA + Cursor + Slack failed for incident {incident_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


def handle_resolve_incident_task(task_data: Dict[str, Any]):
    """Handle resolve_incident tasks from Redpanda without blocking the consumer thread."""
    incident_id = task_data.get("incident_id")
    requested_by_user_id = task_data.get("requested_by_user_id")
    if not incident_id or not requested_by_user_id:
        print(
            f"⚠️  Invalid resolve_incident task payload: incident_id={incident_id}, requested_by_user_id={requested_by_user_id}"
        )
        return

    print(f"🔄 [Redpanda Consumer] Received resolve_incident for incident {incident_id}")

    def _run_with_claim_and_timeout():
        db = SessionLocal()
        try:
            # Idempotent claim: only one worker should proceed
            claimed = try_claim_incident_resolution(db=db, incident_id=int(incident_id))
            if not claimed:
                return {"success": True, "skipped": True, "reason": "not_queued_or_already_claimed"}

            result = run_incident_resolution_job(
                incident_id=int(incident_id),
                requested_by_user_id=int(requested_by_user_id),
            )
            if result.get("success"):
                mark_incident_resolution_completed(db=db, incident_id=int(incident_id))
            else:
                mark_incident_resolution_failed(
                    db=db,
                    incident_id=int(incident_id),
                    error=str(result.get("error") or "resolution_failed"),
                )
            return result
        except Exception as e:
            try:
                mark_incident_resolution_failed(
                    db=db, incident_id=int(incident_id), error=f"exception: {str(e)[:500]}"
                )
            except Exception:
                pass
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    try:
        future: Future = _incident_resolution_executor.submit(_run_with_claim_and_timeout)

        def _on_done(fut: Future):
            try:
                _ = fut.result(timeout=0.1)
            except FuturesTimeoutError:
                pass
            except Exception as e:
                print(f"⚠️  resolve_incident job finished with error: {e}")

        future.add_done_callback(_on_done)
        print(f"✓ Submitted incident {incident_id} resolution job to threadpool")
    except Exception as e:
        print(f"✗ Failed to submit incident resolution job: {e}")


def get_available_integration_for_user(db, user_id: int, service_name: str = None):
    """
    Find an available integration for a user.
    If service_name is provided, try to match based on service mappings.
    Otherwise, return the first active integration for the user.

    Returns:
        Tuple of (Integration ID (int), Integration object) or (None, None) if no integration is available
    """
    # Find active integrations for this user
    integrations = db.query(Integration).filter(
        Integration.user_id == user_id,
        Integration.status == "ACTIVE"
    ).all()

    if not integrations:
        return None, None

    # If service_name is provided, try to match based on service mappings
    if service_name:
        for integration in integrations:
            config = integration.config or {}
            service_mappings = config.get("service_mappings", {})

            # Check if service_name matches any mapping
            if service_name in service_mappings:
                return integration.id, integration

        # If no specific mapping found, still check if there's a single integration
        # that could be used (prefer integrations without strict mappings)
        for integration in integrations:
            config = integration.config or {}
            service_mappings = config.get("service_mappings", {})

            # Prefer integrations without service mappings (more flexible)
            if not service_mappings:
                return integration.id, integration

    # Return the first available integration
    return integrations[0].id if integrations else None, integrations[0] if integrations else None


def get_repo_name_from_integration(integration: Integration, service_name: str = None) -> str:
    """
    Get repository name from integration config based on service mappings.

    Args:
        integration: Integration object
        service_name: Service name to match against service mappings

    Returns:
        Repository name in format "owner/repo" or None
    """
    if not integration or not integration.config:
        return None

    config = integration.config if isinstance(integration.config, dict) else {}

    # If service_name is provided, check service mappings first
    if service_name:
        service_mappings = config.get("service_mappings", {})
        if isinstance(service_mappings, dict) and service_name in service_mappings:
            repo_name = service_mappings[service_name]
            if repo_name:
                return repo_name

    # Fallback to default repo_name or repository
    repo_name = config.get("repo_name") or config.get("repository")
    if repo_name:
        return repo_name

    # Check project_id as fallback
    if integration.project_id:
        return integration.project_id

    return None


def get_cached_integration(db, integration_id, integration_cache):
    """
    Get integration object from cache or database.
    Reduces redundant queries during log processing.
    """
    if integration_id is None:
        return None

    if integration_id not in integration_cache:
        performance_metrics.record_integration_cache_miss()
        performance_metrics.record_database_query()
        integration_cache[integration_id] = db.query(Integration).filter(
            Integration.id == integration_id
        ).first()
    else:
        performance_metrics.record_integration_cache_hit()

    return integration_cache[integration_id]


def batch_load_integrations(db, integration_ids, integration_cache):
    """
    Batch load multiple integrations to optimize database access.

    Args:
        db: Database session
        integration_ids: List of integration IDs to load
        integration_cache: Cache dictionary to populate
    """
    # Filter out IDs that are already cached
    uncached_ids = [id for id in integration_ids if id not in integration_cache and id is not None]

    if uncached_ids:
        # Single query to fetch all needed integrations
        integrations = db.query(Integration).filter(
            Integration.id.in_(uncached_ids)
        ).all()

        # Populate cache
        for integration in integrations:
            integration_cache[integration.id] = integration

        print(f"✅ Batch loaded {len(integrations)} integrations")


def get_related_logs_optimized(db, log_ids, limit=50):
    """
    Get related logs with optimized query.

    Args:
        db: Database session
        log_ids: List of log IDs
        limit: Maximum number of logs to return

    Returns:
        List of LogEntry objects
    """
    if not log_ids:
        return []

    return db.query(LogEntry).filter(
        LogEntry.id.in_(log_ids)
    ).order_by(LogEntry.timestamp.desc()).limit(limit).all()


def set_main_event_loop(loop):
    """Set the main event loop for async operations."""
    global _main_event_loop
    _main_event_loop = loop


def get_main_event_loop():
    """Get the main event loop for async operations."""
    return _main_event_loop


async def create_linear_issue_async(
    incident_id: int,
    user_id: int,
    linear_integration_id: int,
    incident_data: Dict[str, Any],
    team_id: str = None
) -> Dict[str, Any]:
    """
    Create Linear issue asynchronously.

    Args:
        incident_id: ID of the incident
        user_id: User ID
        linear_integration_id: Linear integration ID
        incident_data: Incident data including title, description, severity
        team_id: Optional team ID

    Returns:
        Linear issue data or None if failed
    """
    try:
        from src.integrations.linear.async_integration import AsyncLinearIntegration

        async_linear = AsyncLinearIntegration(linear_integration_id)

        # Map incident severity to Linear priority
        priority_map = {
            "CRITICAL": 0,  # Urgent
            "HIGH": 1,      # High
            "MEDIUM": 2,    # Medium
            "LOW": 3        # Low
        }
        priority = priority_map.get(incident_data.get('severity', 'MEDIUM'), 2)

        # Create the Linear issue
        linear_issue = await async_linear.create_issue_async(
            title=f"Incident: {incident_data['title']}",
            description=incident_data.get('description', ''),
            team_id=team_id,
            priority=priority
        )

        # Update incident metadata with Linear issue info
        await update_incident_with_linear_issue_async(incident_id, linear_issue)

        print(f"✅ Created Linear issue {linear_issue['identifier']} for incident {incident_id} (async)")
        return linear_issue

    except Exception as e:
        print(f"⚠️ Failed to create Linear issue for incident {incident_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def update_incident_with_linear_issue_async(incident_id: int, linear_issue: Dict[str, Any]):
    """
    Update incident with Linear issue metadata (async-friendly).

    Note: SQLAlchemy operations are still sync, but this is a quick DB operation.
    """
    db = SessionLocal()
    try:
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if incident:
            if not incident.metadata_json:
                incident.metadata_json = {}
            incident.metadata_json["linear_issue"] = {
                "id": linear_issue["id"],
                "identifier": linear_issue["identifier"],
                "url": linear_issue["url"],
                "title": linear_issue["title"]
            }
            db.commit()
            print(f"✅ Updated incident {incident_id} with Linear issue metadata")
    except Exception as e:
        print(f"⚠️ Error updating incident {incident_id} with Linear metadata: {e}")
        db.rollback()
    finally:
        db.close()


def trigger_linear_issue_creation_async(
    incident_id: int,
    user_id: int,
    linear_integration_id: int,
    incident_data: Dict[str, Any],
    team_id: str = None
):
    """
    Trigger async Linear issue creation from sync context.

    Uses asyncio.run_coroutine_threadsafe() for cross-thread async coordination.
    """
    try:
        loop = get_main_event_loop()
        if loop and loop.is_running():
            # Schedule the async operation in the main event loop
            future = asyncio.run_coroutine_threadsafe(
                create_linear_issue_async(
                    incident_id, user_id, linear_integration_id, incident_data, team_id
                ),
                loop
            )
            print(f"🚀 Scheduled async Linear issue creation for incident {incident_id}")
            return future
        else:
            print("⚠️ No main event loop available, falling back to sync Linear creation")
            return None
    except Exception as e:
        print(f"⚠️ Error scheduling async Linear issue creation: {e}")
        return None


def process_log_entry_from_redpanda(task_data: Dict[str, Any]):
    """
    Process log entry from Redpanda message.
    Replaces the original Celery task with sequential processing.

    Performance optimizations:
    - Cache integration objects to avoid N+1 queries
    - Batch database operations to reduce commits
    - Track performance metrics for optimization monitoring
    """
    log_id = task_data.get('log_id')
    if not log_id:
        print("Error: No log_id in task data")
        return

    start_time = time.time()
    print(f"🔄 [Redpanda Consumer] Processing log entry {log_id} from Redpanda...")

    db = SessionLocal()
    integration_cache = {}  # Cache for integration objects during this processing cycle
    query_count_start = performance_metrics.database_query_count
    commit_count_start = performance_metrics.database_commit_count

    try:
        # Fetch log entry with eager loading to reduce round trips
        # Note: We don't have explicit relationships defined, so we cache integration lookup
        performance_metrics.record_database_query()
        log = db.query(LogEntry).filter(LogEntry.id == log_id).first()
        if not log:
            print(f"Warning: Log {log_id} not found")
            return "Log not found"

        # Pre-cache integration if available (eager loading simulation)
        if log.integration_id and log.integration_id not in integration_cache:
            integration_cache[log.integration_id] = db.query(Integration).filter(
                Integration.id == log.integration_id
            ).first()

        # 1. Prepare Integration Status Update (defer commit)
        status_entry = None
        integration_to_update = None

        if log.integration_id:
            # Update granular status
            status_entry = db.query(IntegrationStatus).filter(
                IntegrationStatus.integration_id == log.integration_id
            ).first()

            if not status_entry:
                status_entry = IntegrationStatus(
                    integration_id=log.integration_id,
                    status=IntegrationStatusEnum.ACTIVE
                )
                db.add(status_entry)

            status_entry.last_log_time = datetime.utcnow()
            status_entry.status = IntegrationStatusEnum.ACTIVE

            # Use cached integration to avoid redundant query
            integration_to_update = get_cached_integration(db, log.integration_id, integration_cache)
            if integration_to_update and integration_to_update.status != "ACTIVE":
                integration_to_update.status = "ACTIVE"
                integration_to_update.last_verified = datetime.utcnow()

        # 2. Incident Logic
        # Only care about ERROR or CRITICAL
        severity_upper = (log.severity or "").upper()
        if severity_upper in ["ERROR", "CRITICAL"]:
            print(f"Processing critical log: {log.message[:50]}... Checking for existing incidents...")

            # Deduplication: Look for OPEN incidents for same service & source in last 3 mins
            three_mins_ago = datetime.utcnow() - timedelta(minutes=3)

            # Optimized query with index hints (new composite indexes will optimize this)
            existing_incident = db.query(Incident).filter(
                Incident.status == "OPEN",
                Incident.service_name == log.service_name,
                Incident.source == log.source,
                Incident.user_id == log.user_id,  # Match by user_id as well
                Incident.last_seen_at >= three_mins_ago
            ).first()

            # Pre-cache integration for existing incident if needed
            if existing_incident and existing_incident.integration_id:
                if existing_incident.integration_id not in integration_cache:
                    integration_cache[existing_incident.integration_id] = db.query(Integration).filter(
                        Integration.id == existing_incident.integration_id
                    ).first()

            if existing_incident:
                print(f"✓ Updating existing incident {existing_incident.id}")
                existing_incident.last_seen_at = datetime.utcnow()

                # Append log ID to list
                current_logs = existing_incident.log_ids or []
                if log.id not in current_logs:
                    current_logs.append(log.id)
                    existing_incident.log_ids = current_logs

                # Auto-assign integration_id if missing
                if not existing_incident.integration_id:
                    integration_obj = None
                    # Try to get from log first
                    if log.integration_id:
                        existing_incident.integration_id = log.integration_id
                        integration_obj = get_cached_integration(db, log.integration_id, integration_cache)
                    else:
                        # Auto-assign from available integration
                        integration_id, integration_obj = get_available_integration_for_user(db, log.user_id, log.service_name)
                        if integration_id:
                            existing_incident.integration_id = integration_id
                            print(f"Auto-assigned integration_id {integration_id} to incident {existing_incident.id}")

                    # Get repo_name if integration is available
                    if existing_incident.integration_id and not existing_incident.repo_name:
                        repo_name_existing = None
                        if not integration_obj:
                            integration_obj = get_cached_integration(db, existing_incident.integration_id, integration_cache)
                        if integration_obj:
                            repo_name_existing = get_repo_name_from_integration(integration_obj, existing_incident.service_name)
                        if not repo_name_existing:
                            github_integration = get_github_integration_for_user(db, log.user_id)
                            if github_integration:
                                repo_name_existing = get_repo_name_from_integration(github_integration, existing_incident.service_name)
                        if repo_name_existing:
                            existing_incident.repo_name = repo_name_existing
                            print(f"Auto-assigned repo_name {repo_name_existing} to incident {existing_incident.id}")

                # Update metadata_json if log has it and incident doesn't, or merge it
                if log.metadata_json:
                    if existing_incident.metadata_json:
                        # Merge metadata, keeping existing but updating with new values
                        merged_metadata = existing_incident.metadata_json.copy() if isinstance(existing_incident.metadata_json, dict) else {}
                        if isinstance(log.metadata_json, dict):
                            merged_metadata.update(log.metadata_json)
                        existing_incident.metadata_json = merged_metadata
                    else:
                        # Copy metadata_json from log
                        existing_incident.metadata_json = log.metadata_json

                # Escalate severity if needed
                if log.severity == "CRITICAL" and existing_incident.severity != "CRITICAL":
                    existing_incident.severity = "CRITICAL"

                # Batch commit: integration status + incident update (commit #1 of 2)
                try:
                    db.commit()
                    performance_metrics.record_database_commit()
                    print(f"✓ Committed integration status and incident update for incident {existing_incident.id}")
                except Exception as e:
                    print(f"Error in incident update transaction: {e}")
                    db.rollback()
                    raise

                # Queue resolution via Redpanda so consumer runs analysis + deep RCA + Cursor prompt + Slack
                if not existing_incident.root_cause:
                    print(f"🤖 Queuing resolution (analysis + RCA + Cursor + Slack) for incident {existing_incident.id}")
                    ensure_incident_resolution_requested(
                        db=db,
                        incident_id=existing_incident.id,
                        requested_by_user_id=existing_incident.user_id,
                        requested_by_trigger="incident_updated_from_log",
                    )

                return f"Updated incident: {existing_incident.id}"

            else:
                print("✓ Creating new incident...")

                # Determine integration_id - use log's integration_id or auto-assign
                integration_id = log.integration_id
                integration_obj = None
                repo_name = None

                if not integration_id:
                    # Auto-assign integration if available
                    integration_id, integration_obj = get_available_integration_for_user(db, log.user_id, log.service_name)
                    if integration_id:
                        print(f"Auto-assigned integration_id {integration_id} to new incident")

                # Get repo_name from integration config if integration is available
                if integration_id:
                    if not integration_obj:
                        integration_obj = get_cached_integration(db, integration_id, integration_cache)
                    if integration_obj:
                        repo_name = get_repo_name_from_integration(integration_obj, log.service_name)
                    if not repo_name:
                        # SigNoz (and other non-GitHub) integrations don't have repo_name; use GitHub if available
                        github_integration = get_github_integration_for_user(db, log.user_id)
                        if github_integration:
                            repo_name = get_repo_name_from_integration(github_integration, log.service_name)
                    if repo_name:
                        print(f"Auto-assigned repo_name {repo_name} to new incident")

                # Generate meaningful title and description from error logs
                title, description = generate_incident_title_and_description(log, log.service_name)

                incident = Incident(
                    title=title,
                    description=description,
                    severity=IncidentSeverity.HIGH if log.severity == "CRITICAL" else IncidentSeverity.MEDIUM,
                    service_name=log.service_name,
                    source=log.source,
                    integration_id=integration_id,
                    user_id=log.user_id,  # Store user_id from log
                    repo_name=repo_name,  # Store repo_name for PR creation
                    log_ids=[log.id],
                    trigger_event=json.loads(json.dumps({
                        "log_id": log.id,
                        "message": log.message,
                        "level": log.severity
                    })),
                    metadata_json=log.metadata_json,  # Copy all metadata_json information
                    status="OPEN"
                )
                db.add(incident)

                # Batch commit: integration status + incident creation (commit #1 of 2)
                try:
                    db.commit()
                    performance_metrics.record_database_commit()
                    print(f"✓ Committed integration status and incident creation for incident {incident.id}")
                except Exception as e:
                    print(f"Error in incident creation transaction: {e}")
                    db.rollback()
                    raise

                # Create Linear issue asynchronously if Linear integration exists
                try:
                    from src.utils.integrations import get_linear_integration_for_user

                    linear_integration_obj = get_linear_integration_for_user(db, log.user_id)
                    if linear_integration_obj:
                        try:
                            # Get team_id from integration config
                            team_id = None
                            if linear_integration_obj.config:
                                team_id = linear_integration_obj.config.get("team_id")

                            # Get related logs for enhanced description (optimized query)
                            related_logs = get_related_logs_optimized(db, incident.log_ids, limit=50)

                            # Build enhanced description with trace/span info
                            enhanced_description = build_enhanced_linear_description(
                                incident=incident,
                                logs=related_logs,
                                db=db,
                                include_trace=True
                            )

                            # Prepare incident data for async Linear issue creation
                            incident_data = {
                                "title": incident.title,
                                "description": enhanced_description,
                                "severity": incident.severity
                            }

                            # Trigger async Linear issue creation (non-blocking)
                            linear_start_time = time.time()
                            future = trigger_linear_issue_creation_async(
                                incident.id,
                                incident.user_id,
                                linear_integration_obj.id,
                                incident_data,
                                team_id
                            )

                            if future:
                                linear_creation_time = time.time() - linear_start_time
                                performance_metrics.record_linear_issue_creation(linear_creation_time, is_async=True)
                                print(f"🚀 Async Linear issue creation scheduled for incident {incident.id}")
                            else:
                                print(f"⚠️ Could not schedule async Linear issue creation, will create synchronously")

                                # Fallback to sync creation if async fails
                                sync_linear_start_time = time.time()
                                from src.integrations.linear.integration import LinearIntegration
                                linear_integration = LinearIntegration(integration_id=linear_integration_obj.id)

                                linear_issue = linear_integration.create_issue(
                                    title=f"Incident: {incident.title}",
                                    description=enhanced_description,
                                    team_id=team_id,
                                    priority=0 if incident.severity == "CRITICAL" else (1 if incident.severity == "HIGH" else 2)
                                )

                                sync_linear_creation_time = time.time() - sync_linear_start_time
                                performance_metrics.record_linear_issue_creation(sync_linear_creation_time, is_async=False)

                                # Store Linear issue info in incident metadata
                                if not incident.metadata_json:
                                    incident.metadata_json = {}
                                incident.metadata_json["linear_issue"] = {
                                    "id": linear_issue["id"],
                                    "identifier": linear_issue["identifier"],
                                    "url": linear_issue["url"],
                                    "title": linear_issue["title"]
                                }

                                # Commit Linear issue metadata (commit #2 of 2)
                                try:
                                    db.commit()
                                    print(f"✅ Created Linear issue {linear_issue['identifier']} for incident {incident.id} (sync fallback)")
                                except Exception as e:
                                    print(f"Error committing Linear issue metadata: {e}")
                                    db.rollback()
                                    print(f"⚠️ Continued processing despite Linear metadata error")

                        except Exception as e:
                            print(f"⚠️  Failed to initiate Linear issue creation for incident {incident.id}: {e}")
                            import traceback
                            traceback.print_exc()
                except Exception as e:
                    print(f"⚠️  Error checking for Linear integration: {e}")

                # Automatically trigger analysis for new incidents
                print(f"🤖 Ensuring resolution is queued for new incident {incident.id}")
                ensure_incident_resolution_requested(
                    db=db,
                    incident_id=incident.id,
                    requested_by_user_id=incident.user_id,
                    requested_by_trigger="incident_created_from_log",
                )

                return f"Incident created: {incident.id}"

    except Exception as e:
        print(f"✗ Error processing log from Redpanda: {e}")
        return f"Error: {e}"
    finally:
        # Record performance metrics
        end_time = time.time()
        processing_time = end_time - start_time
        performance_metrics.record_message_processing(processing_time)

        # Calculate query and commit counts for this message
        queries_this_message = performance_metrics.database_query_count - query_count_start
        commits_this_message = performance_metrics.database_commit_count - commit_count_start

        print(f"📊 Processing completed in {processing_time * 1000:.2f}ms | Queries: {queries_this_message} | Commits: {commits_this_message}")

        # Log performance summary every 10 messages
        if performance_metrics.total_messages_processed % 10 == 0:
            summary = performance_metrics.get_summary()
            print(f"📈 Performance Summary (last {performance_metrics.total_messages_processed} messages):")
            print(f"   Average processing time: {summary['average_processing_time_ms']}ms")
            print(f"   Database queries per message: {summary['database_queries_per_message']}")
            print(f"   Cache hit rate: {summary['integration_cache_hit_rate']}%")
            print(f"   Linear async percentage: {summary['linear_async_percentage']}%")

        db.close()


def setup_redpanda_task_processor():
    """Initialize Redpanda consumer for processing incident tasks."""
    print("Setting up Redpanda task processor...")

    # Setup incident consumer
    redpanda_service.setup_incident_consumer(route_incident_task)
    
    # Setup ticket consumer
    redpanda_service.setup_ticket_consumer(process_ticket_task_from_redpanda)
    
    # Start the incident consumer (if not already started)
    # Note: start_consumers() will start all configured consumers, so it's safe to call multiple times
    redpanda_service.start_consumers()

    print("✓ Redpanda task processor configured and consumer started")


def publish_log_processing_task(log_id: int):
    """
    Publish a log processing task to Redpanda.
    Replaces background_tasks.add_task(process_log_entry, log_id).
    """
    task_data = {
        'task_type': 'process_log_entry',
        'log_id': log_id,
        'created_at': datetime.utcnow().isoformat()
    }

    success = redpanda_service.producer.publish_incident_task(task_data, key=str(log_id))
    if success:
        print(f"✓ Published log processing task for log {log_id}")
    else:
        print(f"✗ Failed to publish log processing task for log {log_id}")
        # Fallback: run directly (not recommended for production)
        print("Running task directly as fallback...")
        process_log_entry_from_redpanda(task_data)

    return success


def publish_rca_cursor_slack_task(incident_id: int, user_id: Optional[int] = None) -> bool:
    """
    Publish an rca_cursor_slack task to Redpanda so the consumer runs deep RCA,
    creates Cursor prompt, persists to action_result, and sends to Slack.
    Incident should already have root_cause. If publish fails, runs inline as fallback.
    """
    task_data = {
        "task_type": "rca_cursor_slack",
        "incident_id": incident_id,
        "user_id": user_id,
        "created_at": datetime.utcnow().isoformat(),
    }
    success = redpanda_service.producer.publish_incident_task(task_data, key=str(incident_id))
    if success:
        print(f"✓ Published rca_cursor_slack task for incident {incident_id}")
    else:
        print(f"✗ Failed to publish rca_cursor_slack task for incident {incident_id}, running inline")
        handle_rca_cursor_slack_task(task_data)
    return success


def get_performance_metrics() -> Dict[str, Any]:
    """
    Get current performance metrics for monitoring.

    Returns:
        Dictionary containing performance metrics
    """
    return performance_metrics.get_summary()


def reset_performance_metrics():
    """Reset performance metrics (useful for testing)."""
    performance_metrics.reset()


def set_main_event_loop_for_async():
    """
    Initialize the main event loop for async Linear issue creation.
    Should be called from main.py startup.
    """
    try:
        loop = asyncio.get_event_loop()
        set_main_event_loop(loop)
        print("✅ Main event loop initialized for async Linear operations")
    except Exception as e:
        print(f"⚠️ Could not initialize main event loop: {e}")


# Performance test function
def test_performance_optimization():
    """
    Test function to verify performance optimizations are working.
    Can be called from unit tests or manually for verification.
    """
    print("🧪 Testing performance optimizations...")

    # Reset metrics for clean test
    reset_performance_metrics()

    # Sample test data
    test_task_data = {
        'task_type': 'process_log_entry',
        'log_id': 1,  # Assuming log ID 1 exists for testing
        'created_at': datetime.utcnow().isoformat()
    }

    # Process a test message
    start_time = time.time()
    try:
        result = process_log_entry_from_redpanda(test_task_data)
        end_time = time.time()

        processing_time_ms = (end_time - start_time) * 1000
        metrics = get_performance_metrics()

        print(f"✅ Test completed in {processing_time_ms:.2f}ms")
        print(f"   Database queries: {metrics['database_queries_per_message']}")
        print(f"   Database commits: {metrics['database_commits_total']}")
        print(f"   Cache hit rate: {metrics['integration_cache_hit_rate']}%")

        # Performance expectations (based on optimization plan)
        if processing_time_ms < 100:  # Target: 50-100ms per message
            print("✅ Processing time meets optimization target (<100ms)")
        else:
            print(f"⚠️ Processing time ({processing_time_ms:.2f}ms) exceeds target")

        if metrics['database_queries_per_message'] <= 4:  # Target: 2-4 queries
            print("✅ Database query count meets optimization target (<=4)")
        else:
            print(f"⚠️ Database query count ({metrics['database_queries_per_message']}) exceeds target")

        return {
            "success": True,
            "processing_time_ms": processing_time_ms,
            "metrics": metrics,
            "result": result
        }

    except Exception as e:
        print(f"❌ Performance test failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }