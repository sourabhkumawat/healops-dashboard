from fastapi import FastAPI, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import PlainTextResponse, FileResponse
from sqlalchemy.orm import Session
from database import engine, Base, get_db
from models import Incident, LogEntry, User, Integration, ApiKey, IntegrationStatus
from auth import verify_password, get_password_hash, create_access_token, verify_token
from integrations import generate_api_key

from integrations.agent import AgentIntegration
from middleware import APIKeyMiddleware
from crypto_utils import encrypt_token, decrypt_token
from datetime import timedelta, datetime
import os
import secrets
import time
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Self-Healing SaaS Engine")

# Add Middleware
app.add_middleware(APIKeyMiddleware)

class LogIngestRequest(BaseModel):
    service_name: str
    severity: str  # Changed from level to match PRD
    message: str
    source: str = "agent" # agent
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    integration_id: Optional[int] = None

@app.get("/")
def read_root():
    return {"status": "online", "service": "engine"}

# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.post("/auth/register")
def register(user_data: dict, db: Session = Depends(get_db)):
    email = user_data.get("email")
    password = user_data.get("password")
    
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
        
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    hashed_password = get_password_hash(password)
    new_user = User(email=email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User created successfully"}

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

# ============================================================================
# Log Ingestion
# ============================================================================

@app.post("/ingest/logs")
def ingest_log(log: LogIngestRequest, request: Request, db: Session = Depends(get_db)):
    # API Key is already validated by middleware
    api_key = request.state.api_key
    
    # Determine integration_id
    # Priority: 1. API Key's integration_id (if set) -> 2. Payload's integration_id
    integration_id = api_key.integration_id
    if not integration_id and log.integration_id:
        # Verify this integration belongs to the user
        integration = db.query(Integration).filter(
            Integration.id == log.integration_id,
            Integration.user_id == api_key.user_id
        ).first()
        if integration:
            integration_id = integration.id
    
    db_log = LogEntry(
        service_name=log.service_name,
        level=log.severity, # Mapping severity to level for backward compat or just use severity
        severity=log.severity,
        message=log.message,
        source=log.source,
        integration_id=integration_id,
        metadata_json=log.metadata
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    
    # Trigger async analysis
    try:
        from tasks import process_log_entry
        process_log_entry.delay(db_log.id)
    except Exception as e:
        print(f"Failed to trigger task: {e}")
        pass  # Celery might not be running
    
    return {"status": "ingested", "id": db_log.id}

# ============================================================================
# OpenTelemetry Error Ingestion
# ============================================================================

class OTelSpanEvent(BaseModel):
    name: str
    time: float
    attributes: Optional[Dict[str, Any]] = None

class OTelSpanStatus(BaseModel):
    code: int
    message: Optional[str] = None

class OTelSpan(BaseModel):
    traceId: str
    spanId: str
    parentSpanId: Optional[str] = None
    name: str
    timestamp: float
    startTime: float
    endTime: float
    attributes: Dict[str, Any]
    events: List[OTelSpanEvent]
    status: OTelSpanStatus
    resource: Dict[str, Any]

class OTelErrorPayload(BaseModel):
    apiKey: str
    serviceName: str
    spans: List[OTelSpan]

@app.post("/otel/errors")
def ingest_otel_errors(payload: OTelErrorPayload, db: Session = Depends(get_db)):
    """
    Ingest OpenTelemetry error spans from HealOps SDK.
    This endpoint receives batched error spans and stores them as log entries.
    """
    # Verify API key
    from integrations import verify_api_key
    
    api_key_obj = db.query(ApiKey).filter(ApiKey.is_active == 1).all()
    valid_key = None
    
    for key in api_key_obj:
        if verify_api_key(payload.apiKey, key.key_hash):
            valid_key = key
            break
    
    if not valid_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Update last_used timestamp
    valid_key.last_used = datetime.utcnow()
    
    # Process each span
    ingested_count = 0
    for span in payload.spans:
        # Extract error information
        error_message = span.status.message or span.name
        
        # Check for exception in events
        exception_details = None
        for event in span.events:
            if event.name == 'exception' and event.attributes:
                exception_type = event.attributes.get('exception.type', 'Unknown')
                exception_message = event.attributes.get('exception.message', '')
                exception_stacktrace = event.attributes.get('exception.stacktrace', '')
                exception_details = f"{exception_type}: {exception_message}\n{exception_stacktrace}"
                break
        
        # Check for exception in attributes
        if not exception_details:
            if 'exception.type' in span.attributes or 'exception.message' in span.attributes:
                exception_type = span.attributes.get('exception.type', 'Unknown')
                exception_message = span.attributes.get('exception.message', '')
                exception_stacktrace = span.attributes.get('exception.stacktrace', '')
                exception_details = f"{exception_type}: {exception_message}\n{exception_stacktrace}"
        
        # Use exception details as message if available
        if exception_details:
            error_message = exception_details
        
        # Determine severity based on status code
        # SpanStatusCode: UNSET=0, OK=1, ERROR=2
        severity = "ERROR" if span.status.code == 2 else "WARNING"
        
        # Create metadata with all span information
        metadata = {
            "traceId": span.traceId,
            "spanId": span.spanId,
            "parentSpanId": span.parentSpanId,
            "spanName": span.name,
            "startTime": span.startTime,
            "endTime": span.endTime,
            "duration": span.endTime - span.startTime,
            "attributes": span.attributes,
            "events": [
                {
                    "name": event.name,
                    "time": event.time,
                    "attributes": event.attributes
                }
                for event in span.events
            ],
            "resource": span.resource,
            "statusCode": span.status.code,
            "statusMessage": span.status.message
        }
        
        # Create log entry
        db_log = LogEntry(
            service_name=payload.serviceName,
            level=severity,
            severity=severity,
            message=error_message,
            source="otel",  # Mark as coming from OpenTelemetry
            integration_id=valid_key.integration_id,
            metadata_json=metadata
        )
        db.add(db_log)
        ingested_count += 1
    
    db.commit()
    
    # Trigger async analysis for each log
    try:
        from tasks import process_log_entry
        for log in db.query(LogEntry).filter(
            LogEntry.service_name == payload.serviceName,
            LogEntry.source == "otel"
        ).order_by(LogEntry.id.desc()).limit(ingested_count).all():
            process_log_entry.delay(log.id)
    except Exception as e:
        print(f"Failed to trigger tasks: {e}")
        pass  # Celery might not be running
    
    return {
        "status": "success",
        "ingested": ingested_count,
        "message": f"Ingested {ingested_count} error spans"
    }


# ============================================================================
# API Key Management
# ============================================================================

class ApiKeyRequest(BaseModel):
    name: str

@app.post("/api-keys/generate")
def create_api_key(request: ApiKeyRequest, db: Session = Depends(get_db)):
    """Generate a new API key for integrations."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    full_key, key_hash, key_prefix = generate_api_key()
    
    api_key = ApiKey(
        user_id=user_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=request.name,
        scopes=["logs:write", "metrics:write"]
    )
    
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    
    return {
        "api_key": full_key,  # Only shown once!
        "key_prefix": key_prefix,
        "name": request.name,
        "created_at": api_key.created_at
    }

@app.get("/api-keys")
def list_api_keys(db: Session = Depends(get_db)):
    """List all API keys (without revealing the actual keys)."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    keys = db.query(ApiKey).filter(ApiKey.user_id == user_id).all()
    
    return {
        "keys": [
            {
                "id": key.id,
                "name": key.name,
                "key_prefix": key.key_prefix,
                "created_at": key.created_at,
                "last_used": key.last_used,
                "is_active": key.is_active
            }
            for key in keys
        ]
    }

@app.get("/logs")
def list_logs(db: Session = Depends(get_db), limit: int = 50, api_key: str = None):
    """List recent log entries. Requires API key via X-API-Key header or query parameter."""
    # Get API key from header or query parameter
    from fastapi import Header
    
    # For testing, just return all logs
    logs = db.query(LogEntry).order_by(LogEntry.timestamp.desc()).limit(limit).all()
    
    return {
        "logs": [
            {
                "id": log.id,
                "service_name": log.service_name,
                "severity": log.severity or log.level,
                "level": log.level,
                "message": log.message,
                "source": log.source,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "metadata": log.metadata_json,
                "integration_id": log.integration_id
            }
            for log in logs
        ]
    }





# ============================================================================
# Universal Agent Integration
# ============================================================================

@app.get("/integrations/agent/install.sh", response_class=PlainTextResponse)
def agent_get_install_script():
    """Download agent install script."""
    script_path = os.path.join(os.path.dirname(__file__), "templates/install.sh")
    
    with open(script_path, 'r') as f:
        return f.read()

@app.get("/integrations/agent/install-command")
def agent_get_install_command(api_key: str):
    """Get one-line install command."""
    agent = AgentIntegration()
    endpoint = os.getenv('BASE_URL', 'http://localhost:8000')
    
    return {
        "linux": agent.get_install_command(api_key, endpoint),
        "windows": agent.get_windows_install_command(api_key, endpoint)
    }

@app.post("/integrations/agent/register")
def agent_register(hostname: str, os_type: str, api_key_hash: str, db: Session = Depends(get_db)):
    """Register a new agent."""
    agent = AgentIntegration()
    
    result = agent.register_agent(hostname, os_type, api_key_hash)
    
    # TODO: Store in database
    
    return result

# ============================================================================
# Integration Management
# ============================================================================

@app.get("/integrations")
def list_integrations(db: Session = Depends(get_db)):
    """List all user integrations."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    integrations = db.query(Integration).filter(Integration.user_id == user_id).all()
    
    return {
        "integrations": [
            {
                "id": i.id,
                "provider": i.provider,
                "name": i.name,
                "status": i.status,
                "project_id": i.project_id,
                "created_at": i.created_at,
                "last_verified": i.last_verified
            }
            for i in integrations
        ]
    }

@app.get("/integrations/providers")
def list_providers():
    """List available integration providers."""
    from integrations import IntegrationRegistry
    
    return IntegrationRegistry.list_providers()

# ============================================================================
# Incident Management
# ============================================================================

@app.get("/incidents")
def list_incidents(status: Optional[str] = None, db: Session = Depends(get_db)):
    """List incidents with optional status filter."""
    query = db.query(Incident)
    if status:
        query = query.filter(Incident.status == status)
    
    incidents = query.order_by(Incident.last_seen_at.desc()).all()
    return incidents

@app.get("/incidents/{incident_id}")
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    """Get incident details including related logs."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    # Fetch related logs
    logs = []
    if incident.log_ids:
        logs = db.query(LogEntry).filter(LogEntry.id.in_(incident.log_ids)).order_by(LogEntry.timestamp.desc()).all()
    
    return {
        "incident": incident,
        "logs": logs
    }

@app.patch("/incidents/{incident_id}")
def update_incident(incident_id: int, update_data: dict, db: Session = Depends(get_db)):
    """Update incident status or severity."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    if "status" in update_data:
        incident.status = update_data["status"]
    if "severity" in update_data:
        incident.severity = update_data["severity"]
        
    db.commit()
    db.refresh(incident)
    return incident
