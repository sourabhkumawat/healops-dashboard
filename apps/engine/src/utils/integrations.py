"""
Integration utility functions.
"""
from sqlalchemy.orm import Session
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
