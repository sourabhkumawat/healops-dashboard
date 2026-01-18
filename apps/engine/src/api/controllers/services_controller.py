"""
Services Controller - Handles service listing.
"""
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session

from src.database.models import LogEntry, Incident
from src.api.controllers.base import get_user_id_from_request


class ServicesController:
    """Controller for service management."""
    
    @staticmethod
    def list_services(request: Request, db: Session):
        """Get list of unique service names from logs and incidents for the authenticated user."""
        try:
            # Get authenticated user (middleware ensures this is set)
            user_id = get_user_id_from_request(request, db=db)

            # ALWAYS filter by user_id - get unique service names from logs
            log_query = db.query(LogEntry.service_name).distinct().filter(
                LogEntry.service_name.isnot(None),
                LogEntry.service_name != "",
                LogEntry.user_id == user_id
            )
            log_services = log_query.all()

            # ALWAYS filter by user_id - get unique service names from incidents
            incident_query = db.query(Incident.service_name).distinct().filter(
                Incident.service_name.isnot(None),
                Incident.service_name != "",
                Incident.user_id == user_id
            )
            incident_services = incident_query.all()
            
            print(f"DEBUG: Found {len(log_services)} log services and {len(incident_services)} incident services")
            
            # Combine and deduplicate
            all_services = set()
            for (service,) in log_services:
                if service:
                    all_services.add(service)
                    print(f"DEBUG: Added service from logs: {service}")
            for (service,) in incident_services:
                if service:
                    all_services.add(service)
                    print(f"DEBUG: Added service from incidents: {service}")
            
            result = sorted(list(all_services))
            print(f"DEBUG: Returning {len(result)} unique services: {result}")
            
            return {
                "services": result
            }
        except Exception as e:
            print(f"ERROR in list_services: {e}")
            import traceback
            traceback.print_exc()
            return {
                "services": [],
                "error": str(e)
            }
