from fastapi import FastAPI, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import PlainTextResponse, FileResponse
from sqlalchemy.orm import Session
from database import engine, Base, get_db
from models import Incident, LogEntry, User, Integration, ApiKey
from auth import verify_password, get_password_hash, create_access_token, verify_token
from integrations import generate_api_key
from integrations.gcp import GCPIntegration
from integrations.aws import AWSIntegration
from integrations.k8s import K8sIntegration
from integrations.agent import AgentIntegration
from middleware import APIKeyMiddleware
from datetime import timedelta
import os
from pydantic import BaseModel
from typing import Optional, Dict, Any

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Self-Healing SaaS Engine")

# Add Middleware
app.add_middleware(APIKeyMiddleware)

class LogIngestRequest(BaseModel):
    service_name: str
    severity: str  # Changed from level to match PRD
    message: str
    source: str = "agent" # gcp, aws, k8s, agent
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

# ============================================================================
# Google Cloud Integration
# ============================================================================

@app.get("/integrations/gcp/oauth/start")
def gcp_oauth_start():
    """Start GCP OAuth flow."""
    import secrets
    state = secrets.token_urlsafe(32)
    
    # TODO: Store state in session/redis
    
    gcp = GCPIntegration("", "")
    oauth_url = gcp.get_oauth_url(state)
    
    return {"oauth_url": oauth_url, "state": state}

@app.get("/integrations/gcp/oauth/callback")
def gcp_oauth_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Handle GCP OAuth callback."""
    # TODO: Verify state, exchange code for token
    # TODO: Store integration in database
    
    return {"status": "success", "message": "GCP integration authorized"}

@app.post("/integrations/gcp/setup")
async def gcp_setup(integration_name: str, project_id: str, db: Session = Depends(get_db)):
    """Complete GCP integration setup."""
    # TODO: Get access token from database
    access_token = ""
    
    gcp = GCPIntegration(project_id, access_token)
    webhook_url = f"{os.getenv('BASE_URL', 'http://localhost:8000')}/ingest/logs"
    
    result = await gcp.setup_complete_integration(integration_name, webhook_url)
    
    return result

# ============================================================================
# AWS Integration
# ============================================================================

@app.get("/integrations/aws/template")
def aws_get_template():
    """Download AWS CloudFormation template."""
    template_path = os.path.join(os.path.dirname(__file__), "templates/aws-logs.yml")
    return FileResponse(template_path, media_type="text/yaml", filename="healops-aws.yml")

@app.get("/integrations/aws/deploy-url")
def aws_get_deploy_url(api_key: str, region: str = "us-east-1"):
    """Get one-click AWS deployment URL."""
    aws = AWSIntegration(region)
    webhook_url = f"{os.getenv('BASE_URL', 'http://localhost:8000')}/ingest/logs"
    
    deploy_url = aws.get_deploy_url(api_key, webhook_url)
    
    return {"deploy_url": deploy_url}

# ============================================================================
# Kubernetes Integration
# ============================================================================

@app.get("/integrations/k8s/manifest")
def k8s_get_manifest(api_key: str):
    """Get Kubernetes manifest with API key."""
    k8s = K8sIntegration()
    endpoint = os.getenv('BASE_URL', 'http://localhost:8000')
    
    manifest = k8s.generate_manifest(api_key, endpoint)
    
    return Response(content=manifest, media_type="text/yaml")

@app.get("/integrations/k8s/install-command")
def k8s_get_install_command(api_key: str):
    """Get kubectl install command."""
    k8s = K8sIntegration()
    
    return {
        "kubectl": k8s.get_install_command(),
        "helm": k8s.get_helm_install_command(api_key)
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
