"""
Stats Controller - Handles system statistics and overview.
"""
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.database.models import Incident, LogEntry, IncidentStatus, IncidentSeverity
from src.api.controllers.base import get_user_id_from_request


class StatsController:
    """Controller for system statistics."""
    
    @staticmethod
    def get_system_stats(request: Request, db: Session):
        """Get system overview statistics for the authenticated user."""
        try:
            # Get authenticated user (middleware ensures this is set)
            user_id = get_user_id_from_request(request, db=db)

            # ALWAYS filter by user_id - build queries for the authenticated user
            incident_query = db.query(Incident).filter(Incident.user_id == user_id)
            log_query = db.query(LogEntry).filter(LogEntry.user_id == user_id)
            service_query_logs = db.query(LogEntry.service_name).distinct().filter(
                LogEntry.service_name.isnot(None),
                LogEntry.service_name != "",
                LogEntry.user_id == user_id
            )
            service_query_incidents = db.query(Incident.service_name).distinct().filter(
                Incident.service_name.isnot(None),
                Incident.service_name != "",
                Incident.user_id == user_id
            )
            
            # Count incidents by status
            total_incidents = incident_query.count()
            open_incidents = incident_query.filter(Incident.status == "OPEN").count()
            investigating_incidents = incident_query.filter(Incident.status == "INVESTIGATING").count()
            healing_incidents = incident_query.filter(Incident.status == "HEALING").count()
            resolved_incidents = incident_query.filter(Incident.status == "RESOLVED").count()
            failed_incidents = incident_query.filter(Incident.status == "FAILED").count()
            
            # Count incidents by severity
            critical_incidents = incident_query.filter(Incident.severity == "CRITICAL").count()
            high_incidents = incident_query.filter(Incident.severity == "HIGH").count()
            medium_incidents = incident_query.filter(Incident.severity == "MEDIUM").count()
            low_incidents = incident_query.filter(Incident.severity == "LOW").count()
            
            # Count total error logs
            error_logs_count = log_query.filter(
                func.upper(LogEntry.severity).in_(["ERROR", "CRITICAL"])
            ).count()
            
            # Get unique services count
            log_services = set([s[0] for s in service_query_logs.all() if s[0]])
            incident_services = set([s[0] for s in service_query_incidents.all() if s[0]])
            unique_services = len(log_services.union(incident_services))
            
            # Determine system status
            active_incidents = open_incidents + investigating_incidents + healing_incidents
            if critical_incidents > 0 or (active_incidents > 0 and high_incidents > 0):
                system_status = "CRITICAL"
                system_status_color = "text-red-500"
            elif active_incidents > 0:
                system_status = "DEGRADED"
                system_status_color = "text-yellow-500"
            else:
                system_status = "OPERATIONAL"
                system_status_color = "text-green-500"
            
            # Calculate unhealthy services (services with open incidents)
            unhealthy_services_list = incident_query.filter(
                Incident.status.in_(["OPEN", "INVESTIGATING", "HEALING"])
            ).with_entities(Incident.service_name).distinct().all()
            unhealthy_services_count = len([s[0] for s in unhealthy_services_list if s[0]])
            
            return {
                "system_status": system_status,
                "system_status_color": system_status_color,
                "total_incidents": total_incidents,
                "open_incidents": open_incidents,
                "investigating_incidents": investigating_incidents,
                "healing_incidents": healing_incidents,
                "resolved_incidents": resolved_incidents,
                "failed_incidents": failed_incidents,
                "critical_incidents": critical_incidents,
                "high_incidents": high_incidents,
                "medium_incidents": medium_incidents,
                "low_incidents": low_incidents,
                "active_incidents": active_incidents,
                "total_services": unique_services,
                "unhealthy_services": unhealthy_services_count,
                "error_logs_count": error_logs_count
            }
        except Exception as e:
            print(f"ERROR in get_system_stats: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Failed to fetch statistics: {str(e)}")
