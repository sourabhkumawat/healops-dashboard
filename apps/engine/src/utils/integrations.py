"""
Integration utility functions.
"""
from sqlalchemy.orm import Session
from typing import Optional, Tuple, Dict, Any
from src.database.models import Incident, Integration


def backfill_integration_to_incidents(db: Session, integration_id: int, user_id: int, config: dict):
    """
    Backfill integration_id and repo_name to existing incidents for a user.
    
    Args:
        db: Database session
        integration_id: ID of the integration to assign
        user_id: User ID to filter incidents
        config: Integration config containing service_mappings
    """
    # Get the integration object
    integration = db.query(Integration).filter(Integration.id == integration_id).first()
    if not integration:
        print(f"⚠️  Integration {integration_id} not found for backfill")
        return
    
    # Get service mappings
    service_mappings = {}
    if config and isinstance(config, dict):
        service_mappings = config.get("service_mappings", {})
    
    # Find incidents without integration_id for this user
    incidents = db.query(Incident).filter(
        Incident.user_id == user_id,
        Incident.integration_id == None
    ).all()
    
    updated_count = 0
    for incident in incidents:
        should_update = False
        
        # If service mappings exist, only assign if service matches
        if service_mappings:
            if incident.service_name in service_mappings:
                incident.integration_id = integration_id
                should_update = True
        else:
            # No service mappings, assign to all incidents
            incident.integration_id = integration_id
            should_update = True
        
        # If we're updating the integration, also get repo_name
        if should_update:
            # Get repo_name from integration config based on service_name
            from src.core.ai_analysis import get_repo_name_from_integration
            if not incident.repo_name:
                repo_name = get_repo_name_from_integration(integration, incident.service_name)
                if repo_name:
                    incident.repo_name = repo_name
            
            updated_count += 1
    
    if updated_count > 0:
        db.commit()
        print(f"✅ Backfilled integration {integration_id} to {updated_count} incident(s)")


def get_linear_integration_for_user(db: Session, user_id: int) -> Optional[Integration]:
    """
    Get active Linear integration for a user.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        Integration object or None if not found
    """
    integration = db.query(Integration).filter(
        Integration.user_id == user_id,
        Integration.provider == "LINEAR",
        Integration.status == "ACTIVE"
    ).first()
    
    return integration


def _validate_and_get_linear_context(
    incident: Incident,
    db: Session
) -> Optional[Dict[str, Any]]:
    """
    Validate Linear integration availability and get context for Linear operations.
    
    This is a shared helper function to avoid code duplication.
    Checks database FIRST, then validates all prerequisites before returning context.
    
    Args:
        incident: Incident object
        db: Database session
        
    Returns:
        Dictionary with Linear context if all checks pass:
        {
            "linear_integration_obj": Integration,
            "linear_integration": LinearIntegration,
            "linear_issue_id": str,
            "linear_issue_data": dict,
            "team_id": str or None
        }
        Returns None if any validation fails (silently - expected behavior)
    """
    # STEP 1: Check database FIRST - verify Linear integration is available for this user
    linear_integration_obj = get_linear_integration_for_user(db, incident.user_id)
    if not linear_integration_obj:
        return None
    
    # STEP 2: Verify integration is ACTIVE (double-check status)
    if linear_integration_obj.status != "ACTIVE":
        return None
    
    # STEP 3: Check if incident has a Linear ticket
    if not incident.metadata_json or not incident.metadata_json.get("linear_issue"):
        return None
    
    linear_issue_data = incident.metadata_json.get("linear_issue", {})
    linear_issue_id = linear_issue_data.get("id")
    
    # STEP 4: Verify Linear issue ID exists
    if not linear_issue_id:
        return None
    
    # STEP 5: Initialize Linear integration (may fail if tokens are invalid)
    try:
        from src.integrations.linear.integration import LinearIntegration
        linear_integration = LinearIntegration(integration_id=linear_integration_obj.id)
    except Exception as init_error:
        print(f"⚠️  Failed to initialize Linear integration for incident {incident.id}: {init_error}")
        return None
    
    # STEP 6: Get team_id from integration config
    team_id = None
    if linear_integration_obj.config:
        team_id = linear_integration_obj.config.get("team_id")
    
    return {
        "linear_integration_obj": linear_integration_obj,
        "linear_integration": linear_integration,
        "linear_issue_id": linear_issue_id,
        "linear_issue_data": linear_issue_data,
        "team_id": team_id
    }


def sync_linear_ticket_status(
    incident: Incident,
    old_status: str,
    new_status: str,
    db: Session
) -> None:
    """
    Sync Linear ticket status when incident status changes.
    
    Maps incident statuses to Linear states:
    - OPEN -> "Todo" or initial state
    - INVESTIGATING/HEALING -> "In Progress"
    - RESOLVED -> "Done"
    - FAILED -> "In Progress" (or stays in current state)
    
    This function gracefully handles cases where Linear integration is not available.
    It checks the database FIRST to verify Linear integration exists before processing.
    It silently returns if Linear is not configured, and only logs errors when
    Linear IS configured but something goes wrong.
    
    Args:
        incident: Incident object
        old_status: Previous incident status
        new_status: New incident status
        db: Database session
    """
    # Only sync if status actually changed
    if old_status == new_status:
        return
    
    # Validate and get Linear context (shared validation logic)
    context = _validate_and_get_linear_context(incident, db)
    if not context:
        return
    
    linear_integration = context["linear_integration"]
    linear_issue_id = context["linear_issue_id"]
    linear_issue_data = context["linear_issue_data"]
    team_id = context["team_id"]
    
    # Map incident status to Linear state
    status_mapping = {
        "OPEN": "Todo",
        "INVESTIGATING": "In Progress",
        "HEALING": "In Progress",
        "RESOLVED": "Done",
        "FAILED": "In Progress"  # Keep in progress if failed
    }
    
    target_state_name = status_mapping.get(new_status)
    if not target_state_name:
        print(f"⚠️  No Linear state mapping for incident status: {new_status}")
        return
    
    # Update Linear ticket state
    try:
        linear_integration.update_issue_state(
            issue_id=linear_issue_id,
            state_name=target_state_name,
            team_id=team_id
        )
        
        print(f"✅ Updated Linear ticket {linear_issue_data.get('identifier', linear_issue_id)} to '{target_state_name}' (incident status: {new_status})")
        
    except ValueError as ve:
        # Handle expected errors (e.g., state not found) - log but don't traceback
        print(f"⚠️  Linear sync skipped for incident {incident.id}: {ve}")
    except Exception as e:
        # Handle unexpected errors - log with traceback
        print(f"⚠️  Failed to sync Linear ticket status for incident {incident.id}: {e}")
        import traceback
        traceback.print_exc()


def sync_linear_ticket_resolution(
    incident: Incident,
    db: Session
) -> None:
    """
    Update Linear ticket with resolution details when incident is resolved.
    
    This function gracefully handles cases where Linear integration is not available.
    It checks the database FIRST to verify Linear integration exists before processing.
    It silently returns if Linear is not configured, and only logs errors when
    Linear IS configured but something goes wrong.
    
    Args:
        incident: Incident object (should have status RESOLVED)
        db: Database session
    """
    if incident.status != "RESOLVED":
        return
    
    # Validate and get Linear context (shared validation logic)
    context = _validate_and_get_linear_context(incident, db)
    if not context:
        return
    
    linear_integration = context["linear_integration"]
    linear_issue_id = context["linear_issue_id"]
    linear_issue_data = context["linear_issue_data"]
    team_id = context["team_id"]
    
    # Build resolution text
    resolution_parts = []
    
    if incident.root_cause:
        resolution_parts.append(f"**Root Cause:**\n{incident.root_cause}")
    
    if incident.action_taken:
        resolution_parts.append(f"**Action Taken:**\n{incident.action_taken}")
    
    # Add PR link if available
    if incident.action_result and incident.action_result.get("pr_url"):
        pr_url = incident.action_result.get("pr_url")
        resolution_parts.append(f"**Pull Request:** [{pr_url}]({pr_url})")
    
    if not resolution_parts:
        resolution_parts.append("Incident has been resolved.")
    
    resolution_text = "\n\n".join(resolution_parts)
    
    # Update Linear ticket with resolution
    try:
        linear_integration.update_issue_with_resolution(
            issue_id=linear_issue_id,
            resolution=resolution_text,
            state_name="Done",
            team_id=team_id
        )
        
        print(f"✅ Updated Linear ticket {linear_issue_data.get('identifier', linear_issue_id)} with resolution details")
        
    except ValueError as ve:
        # Handle expected errors (e.g., state not found) - log but don't traceback
        print(f"⚠️  Linear resolution sync skipped for incident {incident.id}: {ve}")
    except Exception as e:
        # Handle unexpected errors - log with traceback
        print(f"⚠️  Failed to sync Linear ticket resolution for incident {incident.id}: {e}")
        import traceback
        traceback.print_exc()
