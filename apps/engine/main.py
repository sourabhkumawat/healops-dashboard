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

# CORS Configuration - Allow all origins
# Note: allow_credentials must be False when allowing all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
            user_id=api_key.user_id,  # Store user_id from API key
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
                user_id=valid_key.user_id,  # Store user_id from API key
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

class ServiceMappingRequest(BaseModel):
    service_name: str
    repo_name: str  # Format: "owner/repo"

class ServiceMappingsUpdateRequest(BaseModel):
    service_mappings: Dict[str, str]  # Dict of {service_name: repo_name}
    default_repo: Optional[str] = None  # Default repo for services without mapping

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

@app.get("/integrations/{integration_id}/config")
def get_integration_config(integration_id: int, db: Session = Depends(get_db)):
    """Get integration configuration including service mappings."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    config = integration.config or {}
    service_mappings = config.get("service_mappings", {})
    default_repo = config.get("repo_name") or config.get("repository") or integration.project_id
    
    return {
        "integration_id": integration.id,
        "provider": integration.provider,
        "default_repo": default_repo,
        "service_mappings": service_mappings
    }

@app.post("/integrations/{integration_id}/service-mapping")
def add_service_mapping(integration_id: int, mapping: ServiceMappingRequest, db: Session = Depends(get_db)):
    """Add or update a service-to-repo mapping."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    if integration.provider != "GITHUB":
        raise HTTPException(status_code=400, detail="Service mappings are only supported for GitHub integrations")
    
    # Initialize config if needed
    if not integration.config:
        integration.config = {}
    
    # Initialize service_mappings if needed
    if "service_mappings" not in integration.config:
        integration.config["service_mappings"] = {}
    
    # Add or update the mapping
    integration.config["service_mappings"][mapping.service_name] = mapping.repo_name
    integration.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(integration)
    
    return {
        "status": "success",
        "message": f"Service mapping added: {mapping.service_name} -> {mapping.repo_name}",
        "service_mappings": integration.config.get("service_mappings", {})
    }

@app.put("/integrations/{integration_id}/service-mappings")
def update_service_mappings(integration_id: int, update: ServiceMappingsUpdateRequest, db: Session = Depends(get_db)):
    """Update all service-to-repo mappings at once."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    if integration.provider != "GITHUB":
        raise HTTPException(status_code=400, detail="Service mappings are only supported for GitHub integrations")
    
    # Initialize config if needed
    if not integration.config:
        integration.config = {}
    
    # Update service mappings
    integration.config["service_mappings"] = update.service_mappings
    
    # Update default repo if provided
    if update.default_repo:
        integration.config["repo_name"] = update.default_repo
    
    integration.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(integration)
    
    return {
        "status": "success",
        "message": "Service mappings updated",
        "service_mappings": integration.config.get("service_mappings", {}),
        "default_repo": integration.config.get("repo_name")
    }

@app.delete("/integrations/{integration_id}/service-mapping/{service_name}")
def remove_service_mapping(integration_id: int, service_name: str, db: Session = Depends(get_db)):
    """Remove a service-to-repo mapping."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    if not integration.config or "service_mappings" not in integration.config:
        raise HTTPException(status_code=404, detail="Service mapping not found")
    
    if service_name not in integration.config["service_mappings"]:
        raise HTTPException(status_code=404, detail="Service mapping not found")
    
    # Remove the mapping
    del integration.config["service_mappings"][service_name]
    integration.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "status": "success",
        "message": f"Service mapping removed: {service_name}",
        "service_mappings": integration.config.get("service_mappings", {})
    }

@app.get("/services")
def list_services(user_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Get list of unique service names from logs and incidents.
    If user_id is provided, only returns services for that user.
    Otherwise returns all services (for backward compatibility).
    """
    try:
        # Get unique service names from logs
        log_query = db.query(LogEntry.service_name).distinct().filter(
            LogEntry.service_name.isnot(None),
            LogEntry.service_name != ""
        )
        if user_id:
            log_query = log_query.filter(LogEntry.user_id == user_id)
        log_services = log_query.all()
        
        # Get unique service names from incidents
        incident_query = db.query(Incident.service_name).distinct().filter(
            Incident.service_name.isnot(None),
            Incident.service_name != ""
        )
        if user_id:
            incident_query = incident_query.filter(Incident.user_id == user_id)
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

@app.get("/integrations/{integration_id}/repositories")
def list_repositories(integration_id: int, db: Session = Depends(get_db)):
    """Get list of repositories accessible by the GitHub integration."""
    # TODO: Get user from JWT token
    user_id = 1  # Placeholder
    
    try:
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()
        
        if not integration:
            print(f"DEBUG: Integration {integration_id} not found for user {user_id}")
            raise HTTPException(status_code=404, detail="Integration not found")
        
        if integration.provider != "GITHUB":
            raise HTTPException(status_code=400, detail="This endpoint is only for GitHub integrations")
        
        print(f"DEBUG: Found integration {integration_id}, provider: {integration.provider}")
        
        github_integration = GithubIntegration(integration_id=integration.id)
        
        # Get user's repositories
        if not github_integration.client:
            print("DEBUG: GitHub client is None")
            return {"repositories": []}
        
        print("DEBUG: Fetching repositories from GitHub...")
        user = github_integration.client.get_user()
        repos = []
        
        # Get user's repos (limit to 100 for performance)
        repo_list = list(user.get_repos(type="all", sort="updated")[:100])
        print(f"DEBUG: Found {len(repo_list)} repositories from GitHub")
        
        for repo in repo_list:
            repos.append({
                "full_name": repo.full_name,
                "name": repo.name,
                "private": repo.private
            })
            print(f"DEBUG: Added repo: {repo.full_name}")
        
        print(f"DEBUG: Returning {len(repos)} repositories")
        return {
            "repositories": repos
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR fetching repositories: {e}")
        import traceback
        traceback.print_exc()
        return {
            "repositories": [],
            "error": str(e)
        }

# ============================================================================
# Incident Management
# ============================================================================

@app.get("/incidents")
def list_incidents(status: Optional[str] = None, user_id: Optional[int] = None, db: Session = Depends(get_db)):
    """List incidents with optional status and user_id filter.
    If user_id is provided, only returns incidents for that user.
    Otherwise returns all incidents (for backward compatibility).
    """
    query = db.query(Incident)
    if status:
        query = query.filter(Incident.status == status)
    if user_id:
        query = query.filter(Incident.user_id == user_id)
    
    incidents = query.order_by(Incident.last_seen_at.desc()).all()
    return incidents

@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Get incident details including related logs. Triggers AI analysis if not already done."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    # Fetch related logs
    logs = []
    if incident.log_ids:
        logs = db.query(LogEntry).filter(LogEntry.id.in_(incident.log_ids)).order_by(LogEntry.timestamp.desc()).all()
    
    # Trigger AI analysis in background if root_cause is not set
    if not incident.root_cause:
        from ai_analysis import analyze_incident_with_openrouter
        background_tasks.add_task(analyze_incident_async, incident_id)
    
    return {
        "incident": incident,
        "logs": logs
    }

@app.post("/incidents/{incident_id}/analyze")
async def analyze_incident(incident_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Manually trigger AI analysis for an incident."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    background_tasks.add_task(analyze_incident_async, incident_id)
    
    return {"status": "analysis_triggered", "message": "AI analysis started in background"}

async def analyze_incident_async(incident_id: int):
    """Background task to analyze an incident."""
    from database import SessionLocal
    from ai_analysis import analyze_incident_with_openrouter
    
    db = SessionLocal()
    incident = None
    try:
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            print(f"❌ Incident {incident_id} not found for analysis")
            return
        
        # Fetch related logs
        logs = []
        if incident.log_ids:
            logs = db.query(LogEntry).filter(LogEntry.id.in_(incident.log_ids)).order_by(LogEntry.timestamp.desc()).all()
        
        # Perform analysis
        analysis = analyze_incident_with_openrouter(incident, logs, db)
        
        # Update incident with analysis results
        # Always update root_cause (even if it's an error message) to stop infinite loading
        if analysis.get("root_cause"):
            incident.root_cause = analysis["root_cause"]
        if analysis.get("action_taken"):
            incident.action_taken = analysis["action_taken"]
        
        # Store PR information in action_result if PR was created
        if analysis.get("pr_url"):
            incident.action_result = {
                "pr_url": analysis.get("pr_url"),
                "pr_number": analysis.get("pr_number"),
                "pr_files_changed": analysis.get("pr_files_changed", []),
                "status": "pr_created"
            }
            print(f"✅ PR created for incident {incident_id}: {analysis.get('pr_url')}")
        elif analysis.get("pr_error"):
            # Store PR error if creation failed
            incident.action_result = {
                "status": "pr_failed",
                "error": analysis.get("pr_error")
            }
        
        # Ensure we always set something to stop infinite loading
        if not incident.root_cause:
            incident.root_cause = "Analysis failed - no results returned. Please check logs."
        
        db.commit()
        print(f"✅ AI analysis completed for incident {incident_id}: {incident.root_cause[:100]}")
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Error analyzing incident {incident_id}: {e}")
        print(f"Full traceback: {error_trace}")
        
        # Set error message to stop infinite loading in UI
        try:
            if incident:
                incident.root_cause = f"Analysis error: {str(e)[:200]}. Please check server logs."
                db.commit()
                print(f"✅ Set error message for incident {incident_id}")
            else:
                # Try to get incident again if we lost the reference
                incident = db.query(Incident).filter(Incident.id == incident_id).first()
                if incident:
                    incident.root_cause = f"Analysis error: {str(e)[:200]}. Please check server logs."
                    db.commit()
                    print(f"✅ Set error message for incident {incident_id}")
        except Exception as commit_error:
            print(f"❌ Failed to update incident with error message: {commit_error}")
            db.rollback()
    finally:
        db.close()

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
