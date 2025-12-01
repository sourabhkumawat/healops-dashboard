from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import PlainTextResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import engine, Base, get_db
from models import Incident, LogEntry, User, Integration, ApiKey, IntegrationStatus
from auth import verify_password, get_password_hash, create_access_token, verify_token
from integrations import generate_api_key

from integrations.github_integration import GithubIntegration
from middleware import APIKeyMiddleware
from crypto_utils import encrypt_token, decrypt_token
from datetime import timedelta, datetime
import os
import secrets
import time
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import requests


# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Self-Healing SaaS Engine")

# Add Middleware
# Add Middleware
from fastapi.middleware.cors import CORSMiddleware

# CORS Configuration - Read from environment variables
# Supports multiple origins separated by commas
# Example: CORS_ALLOWED_ORIGINS=http://localhost:3000,https://app.healops.ai
CORS_ALLOWED_ORIGINS_ENV = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001,https://experiment.healops.ai/")
cors_origins = [origin.strip() for origin in CORS_ALLOWED_ORIGINS_ENV.split(",") if origin.strip()]

print(f"🌐 CORS Allowed Origins: {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(APIKeyMiddleware)

class LogIngestRequest(BaseModel):
    service_name: str
    severity: str  # Changed from level to match PRD
    message: str
    source: str = "github" # agent
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

from fastapi import WebSocket, WebSocketDisconnect, BackgroundTasks

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Handle disconnection gracefully
                pass

manager = ConnectionManager()

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ============================================================================
# Log Ingestion
# ============================================================================

@app.post("/ingest/logs")
async def ingest_log(log: LogIngestRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # API Key is already validated by middleware
    api_key = request.state.api_key
    
    # Determine integration_id
    integration_id = api_key.integration_id
    if not integration_id and log.integration_id:
        integration = db.query(Integration).filter(
            Integration.id == log.integration_id,
            Integration.user_id == api_key.user_id
        ).first()
        if integration:
            integration_id = integration.id
    
    # Prepare log data
    log_data = {
        "service_name": log.service_name,
        "severity": log.severity,
        "message": log.message,
        "source": log.source,
        "timestamp": log.timestamp or datetime.utcnow().isoformat(),
        "metadata": log.metadata
    }
    
    # 1. Broadcast to WebSockets (ALL LOGS)
    await manager.broadcast(log_data)
    
    # 2. Persistence & Incident Logic (ERRORS ONLY)
    if log.severity.upper() in ["ERROR", "CRITICAL"]:
        db_log = LogEntry(
            service_name=log.service_name,
            level=log.severity,
            severity=log.severity,
            message=log.message,
            source=log.source,
            integration_id=integration_id,
            metadata_json=log.metadata
        )
        db.add(db_log)
        db.commit()
        db.refresh(db_log)
        
        # Trigger incident check
        from tasks import process_log_entry
        background_tasks.add_task(process_log_entry, db_log.id)
        
        return {"status": "ingested", "id": db_log.id, "persisted": True}
    
    return {"status": "broadcasted", "persisted": False}

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
async def ingest_otel_errors(payload: OTelErrorPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Ingest OpenTelemetry spans from HealOps SDK.
    Now receives ALL spans (success & error).
    - Broadcasts ALL spans to WebSocket (Live Logs)
    - Persists ONLY ERROR/CRITICAL spans to Database
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
    persisted_count = 0
    total_received = len(payload.spans)
    
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
        
        if exception_details:
            error_message = exception_details
        
        # Determine severity based on status code
        # SpanStatusCode: UNSET=0, OK=1, ERROR=2
        is_error = span.status.code == 2
        severity = "ERROR" if is_error else "INFO"
        
        # If it's not an error, check if it has exception details (could be a handled exception)
        if not is_error and exception_details:
            severity = "WARNING"
            is_error = True # Treat warning as something to persist? Requirement says "error logs", usually implies ERROR/CRITICAL. Let's stick to strict ERROR code for persistence unless it has exception.
        
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
        
        # Prepare log data for broadcast
        log_data = {
            "service_name": payload.serviceName,
            "severity": severity,
            "message": error_message,
            "source": "otel",
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata
        }
        
        # 1. Broadcast (ALL SPANS)
        await manager.broadcast(log_data)
        
        # 2. Persist (ONLY ERRORS)
        if is_error or severity.upper() in ["ERROR", "CRITICAL"]:
            db_log = LogEntry(
                service_name=payload.serviceName,
                level=severity,
                severity=severity,
                message=error_message,
                source="otel",
                integration_id=valid_key.integration_id,
                metadata_json=metadata
            )
            db.add(db_log)
            persisted_count += 1
    
    if persisted_count > 0:
        db.commit()
        
        # Trigger async analysis for persisted logs
        try:
            from tasks import process_log_entry
            # Fetch IDs of newly inserted logs
            recent_logs = db.query(LogEntry).filter(
                LogEntry.service_name == payload.serviceName,
                LogEntry.source == "otel"
            ).order_by(LogEntry.id.desc()).limit(persisted_count).all()
            
            for log in recent_logs:
                background_tasks.add_task(process_log_entry, log.id)
                
        except Exception as e:
            print(f"Failed to trigger tasks: {e}")
    
    return {
        "status": "success",
        "received": total_received,
        "persisted": persisted_count,
        "message": f"Received {total_received} spans, persisted {persisted_count} errors"
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
# GitHub Integration
# ============================================================================

class GithubConfig(BaseModel):
    access_token: str
    repo_name: Optional[str] = None

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

@app.get("/integrations/github/authorize")
def github_authorize():
    """Redirect user to GitHub OAuth authorization page."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GitHub Client ID not configured")
    
    # Scopes: repo (for private repos), read:user (for user info)
    scope = "repo read:user"
    return RedirectResponse(
        f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&scope={scope}"
    )

@app.get("/integrations/github/callback")
def github_callback(code: str, db: Session = Depends(get_db)):
    """Handle GitHub OAuth callback."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub credentials not configured")
        
    # Exchange code for token
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code
        }
    )
    
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to retrieve access token")
        
    data = response.json()
    access_token = data.get("access_token")
    
    if not access_token:
        raise HTTPException(status_code=400, detail=f"No access token returned: {data}")
        
    # Verify and get user info
    gh = GithubIntegration(access_token=access_token)
    user_info = gh.verify_connection()
    
    if user_info["status"] == "error":
        raise HTTPException(status_code=400, detail="Invalid token obtained")
        
    # TODO: Get actual user_id from session/auth. 
    # Since this is a callback, we might need to rely on a state parameter or cookie to identify the user if not using a global session.
    # For this MVP/Agent context, we'll assume user_id=1 or try to get it if possible, but the callback comes from GitHub.
    # A common pattern is to pass a state param with the user's session token, but for simplicity here we'll default to 1 
    # or assume single-user mode for the demo.
    user_id = 1 
    
    # Encrypt token
    encrypted_token = encrypt_token(access_token)
    
    # Check if integration already exists
    integration = db.query(Integration).filter(
        Integration.user_id == user_id,
        Integration.provider == "GITHUB"
    ).first()
    
    if not integration:
        integration = Integration(
            user_id=user_id,
            provider="GITHUB",
            name=f"GitHub ({user_info['username']})",
            status="ACTIVE",
            access_token=encrypted_token,
            last_verified=datetime.utcnow()
        )
        db.add(integration)
    else:
        integration.access_token = encrypted_token
        integration.status = "ACTIVE"
        integration.last_verified = datetime.utcnow()
        integration.name = f"GitHub ({user_info['username']})"
        
    db.commit()
    
    return RedirectResponse(f"{FRONTEND_URL}/settings?github_connected=true")


@app.post("/integrations/github/connect")
def github_connect(config: GithubConfig, db: Session = Depends(get_db)):
    """Connect GitHub integration."""
    # Verify token
    gh = GithubIntegration(access_token=config.access_token)
    verification = gh.verify_connection()
    
    if verification["status"] == "error":
        raise HTTPException(status_code=400, detail=verification["message"])
        
    # TODO: Associate with user/integration in DB
    # For now, we just return success to simulate the connection
    # In a real app, we'd update the Integration record
    
    return {
        "status": "connected",
        "username": verification.get("username"),
        "message": "GitHub connected successfully"
    }

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
