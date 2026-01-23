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
from typing import Dict, Any
from datetime import datetime

from src.services.redpanda_service import redpanda_service
from src.database.database import SessionLocal
from src.database.models import LogEntry, Incident, IncidentSeverity, IntegrationStatus, IntegrationStatusEnum, Integration
from sqlalchemy import func
from datetime import datetime, timedelta
import threading


def trigger_incident_analysis_async(incident_id: int, user_id: int):
    """
    Helper function to trigger incident analysis in a thread-safe way.
    This avoids circular import issues and handles event loop properly.
    """
    def run_analysis():
        try:
            # Import here to avoid circular dependencies
            from main import analyze_incident_async
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(analyze_incident_async(incident_id, user_id))
            finally:
                loop.close()
        except Exception as e:
            print(f"⚠️  Error in background analysis thread for incident {incident_id}: {e}")

    # Run in a separate thread to avoid blocking
    thread = threading.Thread(target=run_analysis, daemon=True)
    thread.start()


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


def process_log_entry_from_redpanda(task_data: Dict[str, Any]):
    """
    Process log entry from Redpanda message.
    Replaces the original Celery task with sequential processing.
    """
    log_id = task_data.get('log_id')
    if not log_id:
        print("Error: No log_id in task data")
        return

    db = SessionLocal()
    try:
        log = db.query(LogEntry).filter(LogEntry.id == log_id).first()
        if not log:
            print(f"Warning: Log {log_id} not found")
            return "Log not found"

        # 1. Update Integration Status
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

            # Also update main Integration record to ACTIVE if it's not
            integration = db.query(Integration).filter(Integration.id == log.integration_id).first()
            if integration and integration.status != "ACTIVE":
                integration.status = "ACTIVE"
                integration.last_verified = datetime.utcnow()

            db.commit()

        # 2. Incident Logic
        # Only care about ERROR or CRITICAL
        if log.severity.upper() in ["ERROR", "CRITICAL"]:
            print(f"Processing critical log: {log.message[:50]}... Checking for existing incidents...")

            # Deduplication: Look for OPEN incidents for same service & source in last 3 mins
            three_mins_ago = datetime.utcnow() - timedelta(minutes=3)

            existing_incident = db.query(Incident).filter(
                Incident.status == "OPEN",
                Incident.service_name == log.service_name,
                Incident.source == log.source,
                Incident.user_id == log.user_id,  # Match by user_id as well
                Incident.last_seen_at >= three_mins_ago
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
                        integration_obj = db.query(Integration).filter(Integration.id == log.integration_id).first()
                    else:
                        # Auto-assign from available integration
                        integration_id, integration_obj = get_available_integration_for_user(db, log.user_id, log.service_name)
                        if integration_id:
                            existing_incident.integration_id = integration_id
                            print(f"Auto-assigned integration_id {integration_id} to incident {existing_incident.id}")

                    # Get repo_name if integration is available
                    if existing_incident.integration_id and not existing_incident.repo_name:
                        if not integration_obj:
                            integration_obj = db.query(Integration).filter(Integration.id == existing_incident.integration_id).first()
                        if integration_obj:
                            repo_name = get_repo_name_from_integration(integration_obj, existing_incident.service_name)
                            if repo_name:
                                existing_incident.repo_name = repo_name
                                print(f"Auto-assigned repo_name {repo_name} to incident {existing_incident.id}")

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

                db.commit()

                # Automatically trigger analysis if root_cause is not set
                if not existing_incident.root_cause:
                    print(f"🤖 Auto-triggering analysis for incident {existing_incident.id}")
                    trigger_incident_analysis_async(existing_incident.id, existing_incident.user_id)

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
                        integration_obj = db.query(Integration).filter(Integration.id == integration_id).first()
                    if integration_obj:
                        repo_name = get_repo_name_from_integration(integration_obj, log.service_name)
                        if repo_name:
                            print(f"Auto-assigned repo_name {repo_name} to new incident")

                incident = Incident(
                    title=f"Detected {log.severity} in {log.service_name}",
                    description=log.message[:200],  # Summary
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
                db.commit()

                print(f"✓ Incident created: {incident.id}")

                # Automatically trigger analysis for new incidents
                print(f"🤖 Auto-triggering analysis for new incident {incident.id}")
                trigger_incident_analysis_async(incident.id, incident.user_id)

                return f"Incident created: {incident.id}"

    except Exception as e:
        print(f"✗ Error processing log from Redpanda: {e}")
        return f"Error: {e}"
    finally:
        db.close()


def setup_redpanda_task_processor():
    """Initialize Redpanda consumer for processing incident tasks."""
    print("Setting up Redpanda task processor...")

    # Setup incident consumer
    redpanda_service.setup_incident_consumer(process_log_entry_from_redpanda)

    print("✓ Redpanda task processor configured")


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