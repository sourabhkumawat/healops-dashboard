"""
Script to remove all incidents and logs for a specific service.
Usage: python cleanup_service.py <service_name>
"""
import os
import sys
from sqlalchemy.orm import Session
from src.database.database import SessionLocal
from src.database.models import Incident, LogEntry, EmailLog

def cleanup_service(service_name: str, confirm: bool = False):
    """
    Remove all incidents and logs for a specific service.
    
    Args:
        service_name: The name of the service to clean up
        confirm: If False, will show a preview. If True, will actually delete.
    """
    print(f"🔍 Cleaning up service: {service_name}")
    db = SessionLocal()

    try:
        # Count incidents
        incident_count = db.query(Incident).filter(
            Incident.service_name == service_name
        ).count()
        
        # Count logs
        log_count = db.query(LogEntry).filter(
            LogEntry.service_name == service_name
        ).count()
        
        # Count email logs related to these incidents
        incident_ids = [inc.id for inc in db.query(Incident.id).filter(
            Incident.service_name == service_name
        ).all()]
        email_log_count = 0
        if incident_ids:
            email_log_count = db.query(EmailLog).filter(
                EmailLog.incident_id.in_(incident_ids)
            ).count()
        
        print(f"\n📊 Found:")
        print(f"  - {incident_count} incidents")
        print(f"  - {log_count} logs")
        print(f"  - {email_log_count} email logs (related to incidents)")
        
        if not confirm:
            print(f"\n⚠️  This is a PREVIEW. No data has been deleted.")
            print(f"   Run with --confirm flag to actually delete the data.")
            return
        
        if incident_count == 0 and log_count == 0:
            print("\n✨ No data found for this service. Nothing to delete.")
            return
        
        # Confirm deletion
        print(f"\n🗑️  Deleting data for service: {service_name}")
        
        # Delete email logs first (they reference incidents)
        if email_log_count > 0:
            deleted_emails = db.query(EmailLog).filter(
                EmailLog.incident_id.in_(incident_ids)
            ).delete(synchronize_session=False)
            print(f"  ✅ Deleted {deleted_emails} email logs")
        
        # Delete incidents
        if incident_count > 0:
            deleted_incidents = db.query(Incident).filter(
                Incident.service_name == service_name
            ).delete(synchronize_session=False)
            print(f"  ✅ Deleted {deleted_incidents} incidents")
        
        # Delete logs
        if log_count > 0:
            deleted_logs = db.query(LogEntry).filter(
                LogEntry.service_name == service_name
            ).delete(synchronize_session=False)
            print(f"  ✅ Deleted {deleted_logs} logs")
        
        # Commit all changes
        db.commit()
        print(f"\n✅ Successfully cleaned up service: {service_name}")
        print(f"   Total deleted: {incident_count} incidents, {log_count} logs, {email_log_count} email logs")

    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cleanup_service.py <service_name> [--confirm]")
        print("Example: python cleanup_service.py experiment-ATR-Backend --confirm")
        sys.exit(1)
    
    service_name = sys.argv[1]
    confirm = "--confirm" in sys.argv or "-y" in sys.argv
    
    cleanup_service(service_name, confirm=confirm)

