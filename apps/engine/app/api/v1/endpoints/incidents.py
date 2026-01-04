from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db.base import get_db
from app.models.models import Incident, LogEntry
from app.schemas.incident import IncidentResponse, IncidentUpdate, IncidentAnalysisResponse
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter()

@router.get("/", response_model=List[IncidentResponse])
def list_incidents(
    status: Optional[str] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = db.query(Incident).filter(Incident.user_id == current_user.id)
    if status:
        query = query.filter(Incident.status == status)
    return query.order_by(Incident.last_seen_at.desc()).all()

@router.get("/{incident_id}", response_model=dict)
def get_incident(
    incident_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    incident = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.user_id == current_user.id
    ).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    logs = []
    if incident.log_ids:
        # Check if log_ids is a list
        log_ids = incident.log_ids if isinstance(incident.log_ids, list) else []
        if log_ids:
            logs = db.query(LogEntry).filter(
                LogEntry.id.in_(log_ids),
                LogEntry.user_id == current_user.id
            ).order_by(LogEntry.timestamp.desc()).all()

    # Trigger AI analysis if needed
    if not incident.root_cause:
        from app.services.ai_service import ai_service
        background_tasks.add_task(ai_service.analyze_incident_async, incident_id, db)

    return {
        "incident": incident,
        "logs": logs
    }

@router.patch("/{incident_id}", response_model=IncidentResponse)
def update_incident(
    incident_id: int,
    update_data: IncidentUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    incident = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.user_id == current_user.id
    ).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if update_data.status:
        incident.status = update_data.status
    if update_data.severity:
        incident.severity = update_data.severity
    if update_data.root_cause:
        incident.root_cause = update_data.root_cause

    db.commit()
    db.refresh(incident)
    return incident
