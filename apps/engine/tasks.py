from celery_app import celery_app
from database import SessionLocal
from models import LogEntry, Incident, IncidentSeverity
import json

@celery_app.task
def analyze_log(log_id: int):
    db = SessionLocal()
    try:
        log = db.query(LogEntry).filter(LogEntry.id == log_id).first()
        if not log:
            return "Log not found"

        # Simple heuristic for now: if level is ERROR or CRITICAL, create an incident
        if log.level in ["ERROR", "CRITICAL"]:
            print(f"Detected critical log: {log.message}. Creating incident...")
            
            incident = Incident(
                title=f"Detected {log.level} in {log.service_name}",
                description=log.message,
                severity=IncidentSeverity.HIGH if log.level == "CRITICAL" else IncidentSeverity.MEDIUM,
                service_name=log.service_name,
                trigger_event=json.loads(json.dumps({
                    "log_id": log.id,
                    "message": log.message,
                    "level": log.level
                })),
                status="OPEN"
            )
            db.add(incident)
            db.commit()
            
            # Kick off AI diagnosis
            try:
                from crew import run_diagnosis_crew
                print(f"Starting AI diagnosis for incident {incident.id}...")
                result = run_diagnosis_crew({
                    "message": log.message,
                    "service": log.service_name,
                    "level": log.level
                })
                
                # Update incident with AI results
                incident.root_cause = str(result)
                incident.status = "INVESTIGATING"
                
                # Attempt to parse and execute action (Simple heuristic for MVP)
                # In reality, we'd force JSON output from CrewAI
                result_str = str(result).lower()
                from actions import ActionRegistry
                
                action_taken = None
                if "restart" in result_str and "container" in result_str:
                    action = ActionRegistry.get("restart_container")
                    if action:
                        print("Auto-executing restart_container...")
                        exec_result = action.execute({"service_name": log.service_name})
                        action_taken = f"restart_container: {exec_result}"
                        incident.status = "RESOLVED"
                
                if action_taken:
                    incident.action_taken = action_taken
                    incident.status = "RESOLVED"
                else:
                    incident.status = "NEEDS_APPROVAL"

                db.commit()
                print(f"AI diagnosis complete: {result}")
            except Exception as e:
                print(f"AI diagnosis failed: {e}")
                incident.reasoning_trace = {"error": str(e)}
                db.commit()

            return f"Incident created: {incident.id}"
    finally:
        db.close()
