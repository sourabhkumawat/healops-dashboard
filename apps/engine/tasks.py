from database import SessionLocal
from models import LogEntry, Incident, IncidentSeverity, IntegrationStatus, IntegrationStatusEnum
from sqlalchemy import func
from datetime import datetime, timedelta
import json

# Converted from Celery task to regular async function for BackgroundTasks
async def process_log_entry(log_id: int):
    db = SessionLocal()
    try:
        log = db.query(LogEntry).filter(LogEntry.id == log_id).first()
        if not log:
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
            from models import Integration
            integration = db.query(Integration).filter(Integration.id == log.integration_id).first()
            if integration and integration.status != "ACTIVE":
                integration.status = "ACTIVE"
                integration.last_verified = datetime.utcnow()
            
            db.commit()

        # 2. Incident Logic
        # Only care about ERROR or CRITICAL
        if log.severity.upper() in ["ERROR", "CRITICAL"]:
            print(f"Detected critical log: {log.message}. Checking for existing incidents...")
            
            # Deduplication: Look for OPEN incidents for same service & source in last 3 mins
            three_mins_ago = datetime.utcnow() - timedelta(minutes=3)
            
            existing_incident = db.query(Incident).filter(
                Incident.status == "OPEN",
                Incident.service_name == log.service_name,
                Incident.source == log.source,
                Incident.last_seen_at >= three_mins_ago
            ).first()
            
            if existing_incident:
                print(f"Updating existing incident {existing_incident.id}...")
                existing_incident.last_seen_at = datetime.utcnow()
                
                # Append log ID to list
                current_logs = existing_incident.log_ids or []
                if log.id not in current_logs:
                    current_logs.append(log.id)
                    existing_incident.log_ids = current_logs
                
                # Escalate severity if needed
                if log.severity == "CRITICAL" and existing_incident.severity != "CRITICAL":
                    existing_incident.severity = "CRITICAL"
                    
                db.commit()
                return f"Updated incident: {existing_incident.id}"
            
            else:
                print("Creating new incident...")
                incident = Incident(
                    title=f"Detected {log.severity} in {log.service_name}",
                    description=log.message[:200], # Summary
                    severity=IncidentSeverity.HIGH if log.severity == "CRITICAL" else IncidentSeverity.MEDIUM,
                    service_name=log.service_name,
                    source=log.source,
                    integration_id=log.integration_id,
                    log_ids=[log.id],
                    trigger_event=json.loads(json.dumps({
                        "log_id": log.id,
                        "message": log.message,
                        "level": log.severity
                    })),
                    status="OPEN"
                )
                db.add(incident)
                db.commit()
                
                print(f"Incident created: {incident.id}")
                
                return f"Incident created: {incident.id}"
                
    except Exception as e:
        print(f"Error processing log: {e}")
        return f"Error: {e}"
    finally:
        db.close()
