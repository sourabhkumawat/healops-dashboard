from dotenv import load_dotenv
load_dotenv()

import json
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, Query
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import PlainTextResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func
from database import engine, Base, get_db, SessionLocal
from models import Incident, LogEntry, User, Integration, ApiKey, IntegrationStatus, SourceMap
import memory_models  # Ensure memory tables are created
from auth import verify_password, get_password_hash, create_access_token, verify_token
from integrations import generate_api_key

from integrations.github_integration import GithubIntegration
from middleware import APIKeyMiddleware
from crypto_utils import encrypt_token, decrypt_token
from datetime import timedelta, datetime
from partition_manager import ensure_partition_exists_for_timestamp
import os
import secrets
import time
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import requests
import redis
import asyncio
import threading

def backfill_integration_to_incidents(db: Session, integration_id: int, user_id: int, config: dict):
    """
    Backfill integration_id and repo_name to existing incidents for a user.
    
    Args:
        db: Database session
        integration_id: ID of the integration to assign
        user_id: User ID to filter incidents
        config: Integration config containing service_mappings
    """
    # Get the integration object
    integration = db.query(Integration).filter(Integration.id == integration_id).first()
    if not integration:
        print(f"⚠️  Integration {integration_id} not found for backfill")
        return
    
    # Get service mappings
    service_mappings = {}
    if config and isinstance(config, dict):
        service_mappings = config.get("service_mappings", {})
    
    # Find incidents without integration_id for this user
    incidents = db.query(Incident).filter(
        Incident.user_id == user_id,
        Incident.integration_id == None
    ).all()
    
    updated_count = 0
    for incident in incidents:
        should_update = False
        
        # If service mappings exist, only assign if service matches
        if service_mappings:
            if incident.service_name in service_mappings:
                incident.integration_id = integration_id
                should_update = True
        else:
            # No service mappings, assign to all incidents
            incident.integration_id = integration_id
            should_update = True
        
        # If we're updating the integration, also get repo_name
        if should_update:
            # Get repo_name from integration config based on service_name
            from ai_analysis import get_repo_name_from_integration
            if not incident.repo_name:
                repo_name = get_repo_name_from_integration(integration, incident.service_name)
                if repo_name:
                    incident.repo_name = repo_name
            
            updated_count += 1
    
    if updated_count > 0:
        db.commit()
        print(f"✅ Backfilled integration {integration_id} to {updated_count} incident(s)")



def get_user_id_from_request(request: Request, db: Session = None) -> int:
    """
    Get user_id from request state (set by AuthenticationMiddleware).

    SECURITY: This function now REQUIRES authentication. The AuthenticationMiddleware
    ensures request.state.user_id is always set for protected endpoints.

    Args:
        request: FastAPI Request object
        db: Optional database session (unused, kept for backward compatibility)

    Returns:
        int: The authenticated user's ID

    Raises:
        HTTPException: If user_id not found (should never happen if middleware works correctly)
    """
    # User ID should have been set by middleware
    if hasattr(request.state, 'user_id') and request.state.user_id:
        return request.state.user_id

    # If we reach here, middleware failed - this is a critical error
    raise HTTPException(
        status_code=401,
        detail="Authentication required but user_id not found in request state. This indicates a middleware configuration error."
    )


# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Self-Healing SaaS Engine")

@app.on_event("startup")
async def startup_event():
    """Initialize ConnectionManager with event loop on startup"""
    try:
        loop = asyncio.get_event_loop()
        manager.initialize(loop)
        print("✓ ConnectionManager initialized with Redis pub/sub")
    except Exception as e:
        print(f"⚠ Error initializing ConnectionManager: {e}")
        import traceback
        traceback.print_exc()

# Add Middleware
from fastapi.middleware.cors import CORSMiddleware
from middleware import APIKeyMiddleware, AuthenticationMiddleware

# CORS Configuration - Allow all origins
# Note: allow_credentials must be False when allowing all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SECURITY: AuthenticationMiddleware MUST be added FIRST (order matters!)
# It protects ALL endpoints except public ones
app.add_middleware(AuthenticationMiddleware)

# APIKeyMiddleware validates API keys for /ingest/logs and /api/sourcemaps
app.add_middleware(APIKeyMiddleware)

class LogIngestRequest(BaseModel):
    service_name: str
    severity: str  # Changed from level to match PRD
    message: str
    source: str = "github" # agent
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    integration_id: Optional[int] = None
    release: Optional[str] = None  # Release identifier for source map resolution
    environment: Optional[str] = None  # Environment name for source map resolution

class LogBatchRequest(BaseModel):
    logs: List[LogIngestRequest]

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
    # Return token in both body (standard OAuth2) and Authorization header (for convenience)
    return Response(
        content=json.dumps({"access_token": access_token, "token_type": "bearer"}),
        media_type="application/json",
        headers={"Authorization": f"Bearer {access_token}"}
    )

def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Get current user from JWT token"""
    from fastapi import HTTPException, status
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise credentials_exception
    
    token = auth_header.replace("Bearer ", "").strip()
    email = verify_token(token, credentials_exception)
    
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    
    return user

@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
        "name": current_user.name,
        "organization_name": current_user.organization_name,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None
    }

class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    organization_name: Optional[str] = None

@app.put("/auth/me")
def update_me(update: UserUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update current user information"""
    if update.name is not None:
        current_user.name = update.name
    if update.organization_name is not None:
        current_user.organization_name = update.organization_name
    
    db.commit()
    db.refresh(current_user)
    
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
        "name": current_user.name,
        "organization_name": current_user.organization_name,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None
    }

class TestEmailRequest(BaseModel):
    recipient_email: str

@app.post("/auth/test-email")
def test_email_endpoint(request_data: TestEmailRequest, current_user: User = Depends(get_current_user)):
    """Test email functionality by sending a test email"""
    from email_service import send_test_email
    
    try:
        success = send_test_email(
            recipient_email=request_data.recipient_email,
            subject="🧪 HealOps SMTP Test - Email Service Verification"
        )
        
        if success:
            return {
                "status": "success",
                "message": f"Test email sent successfully to {request_data.recipient_email}",
                "recipient": request_data.recipient_email
            }
        else:
            return {
                "status": "error",
                "message": "Failed to send test email. Please check SMTP configuration.",
                "recipient": request_data.recipient_email
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error sending test email: {str(e)}"
        )

from fastapi import WebSocket, WebSocketDisconnect, BackgroundTasks

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
REDIS_LOG_CHANNEL = "healops:logs"

# Initialize Redis client with error handling
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5)
    # Test connection
    redis_client.ping()
    print(f"✓ Redis client connected: {REDIS_URL}")
except Exception as e:
    print(f"⚠ Warning: Redis connection failed: {e}")
    print("  Logs will still work but may not be distributed via pub/sub")
    redis_client = None

# WebSocket Connection Manager with Redis Pub/Sub
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.message_queue = None
        self.loop = None
        self.redis_subscriber = None
        self.redis_pubsub = None
        self.subscriber_thread = None

    def initialize(self, loop):
        """Initialize with FastAPI's event loop"""
        self.loop = loop
        self.message_queue = asyncio.Queue()
        self._start_redis_subscriber()
        # Start message processor as background task
        asyncio.create_task(self._process_messages())

    def _start_redis_subscriber(self):
        """Start Redis subscriber in a background thread"""
        def redis_listener():
            try:
                subscriber = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5)
                # Test connection
                subscriber.ping()
                pubsub = subscriber.pubsub()
                pubsub.subscribe(REDIS_LOG_CHANNEL)
                
                self.redis_subscriber = subscriber
                self.redis_pubsub = pubsub
                
                print(f"✓ Redis subscriber started on channel: {REDIS_LOG_CHANNEL}")
                
                for message in pubsub.listen():
                    if message['type'] == 'message':
                        try:
                            log_data = json.loads(message['data'])
                            # Put message in queue for async processing
                            if self.loop and self.message_queue:
                                asyncio.run_coroutine_threadsafe(
                                    self.message_queue.put(log_data),
                                    self.loop
                                )
                        except Exception as e:
                            print(f"Error processing Redis message: {e}")
            except (redis.ConnectionError, redis.TimeoutError, ConnectionError) as e:
                print(f"⚠ Redis connection error in subscriber: {e}")
                print("  WebSocket will still work but won't receive Redis pub/sub messages")
            except Exception as e:
                print(f"Error in Redis subscriber thread: {e}")
                import traceback
                traceback.print_exc()
        
        # Only start subscriber if Redis is available
        if redis_client:
            self.subscriber_thread = threading.Thread(target=redis_listener, daemon=True)
            self.subscriber_thread.start()
        else:
            print("⚠ Redis subscriber not started (Redis unavailable)")

    async def _process_messages(self):
        """Process messages from queue and broadcast to WebSockets"""
        while True:
            try:
                message = await self.message_queue.get()
                await self._broadcast_to_websockets(message)
            except Exception as e:
                print(f"Error processing message from queue: {e}")
                await asyncio.sleep(0.1)  # Prevent tight loop on error

    async def _broadcast_to_websockets(self, message: dict):
        """Broadcast message to all active WebSocket connections"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        # Remove disconnected connections
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Publish message to Redis channel (for pub/sub)"""
        if redis_client:
            try:
                redis_client.publish(REDIS_LOG_CHANNEL, json.dumps(message))
            except Exception as e:
                print(f"Error publishing to Redis: {e}")
                # Fallback: broadcast directly to WebSockets if Redis fails
                await self._broadcast_to_websockets(message)
        else:
            # If Redis is not available, broadcast directly to WebSockets
            await self._broadcast_to_websockets(message)

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
    """
    Ingest logs from clients. All logs are broadcast to WebSockets.
    Only ERROR and CRITICAL logs are persisted to the database.
    """
    try:
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
        # Don't let WebSocket failures prevent persistence
        try:
            await manager.broadcast(log_data)
        except Exception as ws_error:
            print(f"Warning: Failed to broadcast log to WebSockets: {ws_error}")
            # Continue execution even if broadcast fails
        
        # 2. Persistence & Incident Logic (ERRORS ONLY)
        severity_upper = log.severity.upper() if log.severity else ""
        should_persist = severity_upper in ["ERROR", "CRITICAL"]
        
        if should_persist:
            try:
                # Resolve source maps in metadata before saving
                resolved_metadata = log.metadata
                if log.metadata and isinstance(log.metadata, dict):
                    try:
                        from sourcemap_resolver import resolve_metadata_with_sourcemaps
                        # Use release/environment from top-level request, fallback to metadata
                        release = log.release or log.metadata.get('release') or log.metadata.get('releaseId') or None
                        environment = log.environment or log.metadata.get('environment') or log.metadata.get('env') or "production"
                        resolved_metadata = resolve_metadata_with_sourcemaps(
                            db=db,
                            user_id=api_key.user_id,
                            service_name=log.service_name,
                            metadata=log.metadata,
                            release=release,
                            environment=environment
                        )
                    except Exception as sm_error:
                        # Don't fail log ingestion if source map resolution fails
                        print(f"Warning: Source map resolution failed: {sm_error}")
                        resolved_metadata = log.metadata
                
                # Parse timestamp and ensure partition exists
                log_timestamp = datetime.utcnow()
                if log.timestamp:
                    try:
                        # Try parsing ISO format timestamp
                        if isinstance(log.timestamp, str):
                            log_timestamp = datetime.fromisoformat(log.timestamp.replace('Z', '+00:00'))
                        elif isinstance(log.timestamp, datetime):
                            log_timestamp = log.timestamp
                    except (ValueError, AttributeError) as e:
                        print(f"Warning: Could not parse timestamp {log.timestamp}, using current time: {e}")
                        log_timestamp = datetime.utcnow()
                
                # Ensure partition exists before inserting
                ensure_partition_exists_for_timestamp(log_timestamp)
                
                db_log = LogEntry(
                    service_name=log.service_name,
                    level=log.severity,
                    severity=log.severity,
                    message=log.message,
                    source=log.source,
                    integration_id=integration_id,
                    user_id=api_key.user_id,  # Store user_id from API key
                    metadata_json=resolved_metadata,  # Use resolved metadata with source maps
                    timestamp=log_timestamp
                )
                db.add(db_log)
                db.commit()
                db.refresh(db_log)
                
                # Log successful persistence for debugging
                print(f"✓ Persisted {severity_upper} log: id={db_log.id}, service={log.service_name}, message={log.message[:50]}")
                
                # Trigger incident check
                try:
                    from tasks import process_log_entry
                    background_tasks.add_task(process_log_entry, db_log.id)
                except Exception as task_error:
                    print(f"Warning: Failed to queue incident check task: {task_error}")
                    # Don't fail the request if task queuing fails
                
                return {"status": "ingested", "id": db_log.id, "persisted": True, "severity": log.severity}
            except Exception as db_error:
                # Rollback on error
                db.rollback()
                print(f"✗ Failed to persist log to database: {db_error}")
                print(f"  Log details: service={log.service_name}, severity={log.severity}, message={log.message[:50]}")
                # Return error response but don't raise exception (log was received)
                return {
                    "status": "broadcasted",
                    "persisted": False,
                    "error": "Failed to persist log to database",
                    "severity": log.severity
                }
        else:
            # Log received but not persisted (INFO/WARNING)
            print(f"Received {severity_upper} log (not persisted): service={log.service_name}, message={log.message[:50]}")
            return {"status": "broadcasted", "persisted": False, "severity": log.severity}
            
    except Exception as e:
        # Catch any unexpected errors
        print(f"✗ Unexpected error in ingest_log: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/ingest/logs/batch")
async def ingest_logs_batch(batch: LogBatchRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Ingest multiple logs in a batch. All logs are broadcast to WebSockets.
    Only ERROR and CRITICAL logs are persisted to the database.
    """
    try:
        # API Key is already validated by middleware
        api_key = request.state.api_key
        
        # Determine integration_id (same for all logs in batch)
        integration_id = api_key.integration_id
        
        results = []
        persisted_count = 0
        broadcasted_count = 0
        
        for log in batch.logs:
            try:
                # Override integration_id if provided in log
                log_integration_id = integration_id
                if not log_integration_id and log.integration_id:
                    integration = db.query(Integration).filter(
                        Integration.id == log.integration_id,
                        Integration.user_id == api_key.user_id
                    ).first()
                    if integration:
                        log_integration_id = integration.id
                
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
                try:
                    await manager.broadcast(log_data)
                    broadcasted_count += 1
                except Exception as ws_error:
                    print(f"Warning: Failed to broadcast log to WebSockets: {ws_error}")
                    # Continue execution even if broadcast fails
                
                # 2. Persistence & Incident Logic (ERRORS ONLY)
                severity_upper = log.severity.upper() if log.severity else ""
                should_persist = severity_upper in ["ERROR", "CRITICAL"]
                
                if should_persist:
                    try:
                        # Use a savepoint for each log so failures don't affect others
                        savepoint = db.begin_nested()
                        try:
                            # Parse timestamp and ensure partition exists
                            log_timestamp = datetime.utcnow()
                            if log.timestamp:
                                try:
                                    # Try parsing ISO format timestamp
                                    if isinstance(log.timestamp, str):
                                        log_timestamp = datetime.fromisoformat(log.timestamp.replace('Z', '+00:00'))
                                    elif isinstance(log.timestamp, datetime):
                                        log_timestamp = log.timestamp
                                except (ValueError, AttributeError) as e:
                                    print(f"Warning: Could not parse timestamp {log.timestamp}, using current time: {e}")
                                    log_timestamp = datetime.utcnow()
                            
                            # Resolve source maps in metadata before saving
                            resolved_metadata = log.metadata
                            if log.metadata and isinstance(log.metadata, dict):
                                try:
                                    from sourcemap_resolver import resolve_metadata_with_sourcemaps
                                    # Use release/environment from top-level request, fallback to metadata
                                    release = log.release or log.metadata.get('release') or log.metadata.get('releaseId') or None
                                    environment = log.environment or log.metadata.get('environment') or log.metadata.get('env') or "production"
                                    resolved_metadata = resolve_metadata_with_sourcemaps(
                                        db=db,
                                        user_id=api_key.user_id,
                                        service_name=log.service_name,
                                        metadata=log.metadata,
                                        release=release,
                                        environment=environment
                                    )
                                except Exception as sm_error:
                                    # Don't fail log ingestion if source map resolution fails
                                    print(f"Warning: Source map resolution failed: {sm_error}")
                                    resolved_metadata = log.metadata
                            
                            # Ensure partition exists before inserting
                            ensure_partition_exists_for_timestamp(log_timestamp)
                            
                            db_log = LogEntry(
                                service_name=log.service_name,
                                level=log.severity,
                                severity=log.severity,
                                message=log.message,
                                source=log.source,
                                integration_id=log_integration_id,
                                user_id=api_key.user_id,  # Store user_id from API key
                                metadata_json=resolved_metadata,  # Use resolved metadata with source maps
                                timestamp=log_timestamp
                            )
                            db.add(db_log)
                            db.flush()  # Flush to get the ID without committing
                            savepoint.commit()
                            persisted_count += 1
                            
                            # Log successful persistence for debugging
                            print(f"✓ Persisted {severity_upper} log: id={db_log.id}, service={log.service_name}, message={log.message[:50]}")
                            
                            # Trigger incident check
                            try:
                                from tasks import process_log_entry
                                background_tasks.add_task(process_log_entry, db_log.id)
                            except Exception as task_error:
                                print(f"Warning: Failed to queue incident check task: {task_error}")
                                # Don't fail the request if task queuing fails
                            
                            results.append({"status": "ingested", "id": db_log.id, "persisted": True, "severity": log.severity})
                        except Exception as db_error:
                            # Rollback only this savepoint, not the entire transaction
                            savepoint.rollback()
                            print(f"✗ Failed to persist log to database: {db_error}")
                            print(f"  Log details: service={log.service_name}, severity={log.severity}, message={log.message[:50]}")
                            # Return error response but don't raise exception (log was received)
                            results.append({
                                "status": "broadcasted",
                                "persisted": False,
                                "error": "Failed to persist log to database",
                                "severity": log.severity
                            })
                    except Exception as savepoint_error:
                        # Handle savepoint creation errors
                        print(f"✗ Failed to create savepoint: {savepoint_error}")
                        results.append({
                            "status": "broadcasted",
                            "persisted": False,
                            "error": "Failed to persist log to database",
                            "severity": log.severity
                        })
                else:
                    # Log received but not persisted (INFO/WARNING)
                    print(f"Received {severity_upper} log (not persisted): service={log.service_name}, message={log.message[:50]}")
                    results.append({"status": "broadcasted", "persisted": False, "severity": log.severity})
                    
            except Exception as log_error:
                # Handle individual log errors without failing the entire batch
                print(f"✗ Error processing log in batch: {log_error}")
                import traceback
                traceback.print_exc()
                results.append({
                    "status": "error",
                    "error": str(log_error),
                    "severity": log.severity if log else "UNKNOWN"
                })
        
        # Commit all persisted logs at once
        if persisted_count > 0:
            try:
                db.commit()
            except Exception as commit_error:
                db.rollback()
                print(f"✗ Failed to commit batch: {commit_error}")
                raise HTTPException(status_code=500, detail=f"Failed to commit logs: {str(commit_error)}")
        
        return {
            "status": "success",
            "total": len(batch.logs),
            "broadcasted": broadcasted_count,
            "persisted": persisted_count,
            "results": results
        }
            
    except Exception as e:
        # Catch any unexpected errors
        print(f"✗ Unexpected error in ingest_logs_batch: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

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
    
    # Ensure partition exists for current date (all logs will use current timestamp)
    ensure_partition_exists_for_timestamp(datetime.utcnow())
    
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
def create_api_key(request: ApiKeyRequest, http_request: Request, db: Session = Depends(get_db)):
    """Generate a new API key for integrations."""
    # Get user_id from request if available (from API key or JWT), otherwise default to 1
    user_id = get_user_id_from_request(http_request, db=db)
    
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
def list_api_keys(request: Request, db: Session = Depends(get_db)):
    """List all API keys (without revealing the actual keys)."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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
def list_logs(limit: int = 50, request: Request = None, db: Session = Depends(get_db)):
    """List recent log entries for the authenticated user only."""
    # Get authenticated user (middleware ensures this is set)
    user_id = get_user_id_from_request(request, db=db)

    # ALWAYS filter by user_id - no exceptions
    query = db.query(LogEntry).filter(LogEntry.user_id == user_id)
    logs = query.order_by(LogEntry.timestamp.desc()).limit(limit).all()

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
# Source Maps Upload
# ============================================================================

class SourceMapFile(BaseModel):
    file_path: str
    source_map: str  # Base64 encoded source map

class SourceMapUploadRequest(BaseModel):
    service_name: str
    release: str
    environment: str = "production"
    files: List[SourceMapFile]

@app.post("/api/sourcemaps/upload")
async def upload_sourcemaps(
    request: SourceMapUploadRequest,
    http_request: Request,
    db: Session = Depends(get_db)
):
    """
    Upload source maps for a service/release/environment combination.
    Requires API key authentication via X-HealOps-Key header.
    Optimized for bulk uploads with batch processing.
    """
    import base64
    import json
    
    try:
        # API Key is already validated by middleware
        api_key = http_request.state.api_key
        user_id = api_key.user_id
        
        # Bulk fetch existing source maps in one query
        file_paths = [f.file_path for f in request.files]
        existing_maps = {
            sm.file_path: sm 
            for sm in db.query(SourceMap).filter(
                SourceMap.user_id == user_id,
                SourceMap.service_name == request.service_name,
                SourceMap.release == request.release,
                SourceMap.environment == request.environment,
                SourceMap.file_path.in_(file_paths)
            ).all()
        }
        
        # Process files in batches for better performance
        uploaded_count = 0
        skipped_count = 0
        new_source_maps = []
        
        for file_data in request.files:
            # Decode base64 source map
            try:
                source_map_content = base64.b64decode(file_data.source_map).decode('utf-8')
                # Validate it's valid JSON (quick validation)
                json.loads(source_map_content)
            except Exception as e:
                # Skip invalid source maps but continue with others
                print(f"Warning: Invalid source map for {file_data.file_path}: {e}")
                skipped_count += 1
                continue
            
            # Check if source map already exists
            if file_data.file_path in existing_maps:
                # Update existing source map
                existing_maps[file_data.file_path].source_map = source_map_content
            else:
                # Create new source map (add to batch)
                source_map = SourceMap(
                    user_id=user_id,
                    service_name=request.service_name,
                    release=request.release,
                    environment=request.environment,
                    file_path=file_data.file_path,
                    source_map=source_map_content
                )
                new_source_maps.append(source_map)
            
            uploaded_count += 1
        
        # Bulk insert new source maps
        if new_source_maps:
            db.add_all(new_source_maps)
        
        # Commit all changes at once
        db.commit()
        
        return {
            "success": True,
            "files_uploaded": uploaded_count,
            "files_skipped": skipped_count,
            "release_id": request.release,
            "message": f"Successfully uploaded {uploaded_count} source maps"
        }
        
    except Exception as e:
        db.rollback()
        print(f"Error uploading source maps: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


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
GITHUB_CALLBACK_URL = "https://engine.healops.ai/integrations/github/callback"

@app.get("/integrations/github/reconnect")
def github_reconnect(
    request: Request,
    integration_id: int = Query(..., description="The integration ID to reconnect"),
    db: Session = Depends(get_db)
):
    """Handle reconnection by first trying to revoke existing token, then redirecting to authorize."""
    # Get the integration to reconnect
    user_id = get_user_id_from_request(request, db=db)
    
    # Try to find the integration - first by ID and provider
    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.provider == "GITHUB"
    ).first()
    
    if not integration:
        raise HTTPException(
            status_code=404, 
            detail=f"GitHub integration with ID {integration_id} not found. Please check the integration ID."
        )
    
    # Log for debugging (can be removed in production)
    print(f"Found integration: ID={integration.id}, user_id={integration.user_id}, requested_user_id={user_id}")
    
    # Try to revoke the existing authorization on GitHub if we have a token
    if integration.access_token:
        try:
            from crypto_utils import decrypt_token
            try:
                access_token = decrypt_token(integration.access_token)
            except Exception:
                # Fallback for legacy plain text tokens
                access_token = integration.access_token
            
            # Note: We can't easily revoke the GitHub OAuth grant programmatically without
            # the user's explicit action. Instead, we'll clear our local token and
            # redirect to authorize, which should prompt GitHub to show the authorization page
            # if the grant has been revoked or if we use a unique state parameter
        except Exception as e:
            # Continue even if revocation fails
            pass
    
    # Mark as disconnected to indicate we're reconnecting
    integration.status = "DISCONNECTED"
    # Clear the access token so it won't be reused
    integration.access_token = None
    db.commit()
    
    # Redirect to authorize with reconnect flag
    # Use a unique timestamp and nonce to ensure GitHub treats it as a new authorization request
    import base64
    state_data = {
        "timestamp": int(time.time() * 1000),  # Use milliseconds for uniqueness
        "reconnect": True,
        "integration_id": integration_id,
        "nonce": secrets.token_urlsafe(16)  # Add random nonce to force new authorization
    }
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
    
    # Get the callback URL - use environment variable if set, otherwise construct from request
    from urllib.parse import quote
    if GITHUB_CALLBACK_URL:
        callback_url = GITHUB_CALLBACK_URL
    else:
        # Construct from request, forcing HTTPS for production
        base_url = str(request.base_url).rstrip('/')
        if 'localhost' not in base_url and base_url.startswith('http://'):
            # Force HTTPS for production
            base_url = base_url.replace('http://', 'https://')
        callback_url = f"{base_url}/integrations/github/callback"
    
    scope = "repo read:user"
    # Build authorization URL
    # Note: redirect_uri must match exactly what's configured in GitHub OAuth App settings
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&scope={scope}"
        f"&state={state}"
        f"&redirect_uri={quote(callback_url, safe='')}"
    )
    
    return RedirectResponse(auth_url)

@app.get("/integrations/github/authorize")
def github_authorize(request: Request, reconnect: Optional[str] = None, integration_id: Optional[int] = None):
    """Redirect user to GitHub OAuth authorization page."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GitHub Client ID not configured")
    
    # Generate state parameter for OAuth security
    # Include integration_id if reconnecting to track which integration is being reconnected
    state_data = {
        "timestamp": int(time.time() * 1000),  # Use milliseconds for uniqueness
        "reconnect": reconnect == "true",
        "nonce": secrets.token_urlsafe(16)  # Add random nonce to ensure uniqueness
    }
    if integration_id:
        state_data["integration_id"] = integration_id
    
    # Encode state as base64 JSON for passing through OAuth flow
    import base64
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
    
    # Scopes: repo (for private repos), read:user (for user info)
    # Note: GitHub OAuth doesn't allow selecting specific repos - it grants access to all repos
    # For repo selection, GitHub Apps installation flow would be needed
    scope = "repo read:user"
    
    # Get the callback URL - use environment variable if set, otherwise construct from request
    from urllib.parse import quote
    if GITHUB_CALLBACK_URL:
        callback_url = GITHUB_CALLBACK_URL
    else:
        # Construct from request, forcing HTTPS for production
        base_url = str(request.base_url).rstrip('/')
        if 'localhost' not in base_url and base_url.startswith('http://'):
            # Force HTTPS for production
            base_url = base_url.replace('http://', 'https://')
        callback_url = f"{base_url}/integrations/github/callback"
    
    # Build authorization URL
    # Note: redirect_uri must match exactly what's configured in GitHub OAuth App settings
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&scope={scope}"
        f"&state={state}"
        f"&redirect_uri={quote(callback_url, safe='')}"
    )
    
    return RedirectResponse(auth_url)

@app.get("/integrations/github/callback")
def github_callback(code: str, state: Optional[str] = None, db: Session = Depends(get_db)):
    """Handle GitHub OAuth callback."""
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub credentials not configured")
        
    # Decode state parameter if provided
    reconnect = False
    integration_id = None
    if state:
        try:
            import base64
            state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
            reconnect = state_data.get("reconnect", False)
            integration_id = state_data.get("integration_id")
        except Exception:
            # If state decoding fails, continue without it (backwards compatibility)
            pass
        
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
        
    # Get user_id from request if available, otherwise default to 1
    # NOTE: In production, the state parameter should include user session info
    user_id = 1  # TODO: Extract from state or session
    
    # Encrypt token
    encrypted_token = encrypt_token(access_token)
    
    # Handle reconnection: if integration_id is provided and reconnecting, update that specific integration
    if reconnect and integration_id:
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.provider == "GITHUB"
        ).first()
        if integration:
            integration.access_token = encrypted_token
            integration.status = "CONFIGURING"  # Set to CONFIGURING, not ACTIVE
            integration.last_verified = datetime.utcnow()
            integration.name = f"GitHub ({user_info['username']})"
            db.commit()
            db.refresh(integration)
            # Redirect to setup page for repository selection
            return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}&reconnected=true")

    # Check if integration already exists for this user
    integration = db.query(Integration).filter(
        Integration.user_id == user_id,
        Integration.provider == "GITHUB"
    ).first()

    if not integration:
        integration = Integration(
            user_id=user_id,
            provider="GITHUB",
            name=f"GitHub ({user_info['username']})",
            status="CONFIGURING",  # Start in CONFIGURING state
            access_token=encrypted_token,
            last_verified=datetime.utcnow()
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        # Redirect to setup page for initial configuration
        return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}&new=true")
    else:
        # Update existing integration with new token
        integration.access_token = encrypted_token
        integration.status = "CONFIGURING"  # Set to CONFIGURING for reconfiguration
        integration.last_verified = datetime.utcnow()
        integration.name = f"GitHub ({user_info['username']})"
        db.commit()
        db.refresh(integration)
        # Redirect to setup page
        return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}")


@app.post("/integrations/github/connect")
def github_connect(config: GithubConfig, request: Request, db: Session = Depends(get_db)):
    """Connect GitHub integration."""
    # Verify token
    gh = GithubIntegration(access_token=config.access_token)
    verification = gh.verify_connection()
    
    if verification["status"] == "error":
        raise HTTPException(status_code=400, detail=verification["message"])
    
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
    # Encrypt token
    encrypted_token = encrypt_token(config.access_token)
    
    # Check if integration already exists
    integration = db.query(Integration).filter(
        Integration.user_id == user_id,
        Integration.provider == "GITHUB"
    ).first()
    
    if not integration:
        integration = Integration(
            user_id=user_id,
            provider="GITHUB",
            name=f"GitHub ({verification.get('username', 'User')})",
            status="ACTIVE",
            access_token=encrypted_token,
            last_verified=datetime.utcnow()
        )
        db.add(integration)
    else:
        integration.access_token = encrypted_token
        integration.status = "ACTIVE"
        integration.last_verified = datetime.utcnow()
        integration.name = f"GitHub ({verification.get('username', 'User')})"
    
    db.commit()
    db.refresh(integration)
    
    # Backfill integration_id and repo_name to existing incidents for this user
    backfill_integration_to_incidents(db, integration.id, user_id, integration.config)
    
    return {
        "status": "connected",
        "username": verification.get("username"),
        "message": "GitHub connected successfully"
    }

# ============================================================================
# Integration Management
# ============================================================================

@app.get("/integrations")
def list_integrations(request: Request, db: Session = Depends(get_db)):
    """List all user integrations."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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
def get_integration_config(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Get integration configuration including service mappings."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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
def add_service_mapping(integration_id: int, mapping: ServiceMappingRequest, request: Request, db: Session = Depends(get_db)):
    """Add or update a service-to-repo mapping."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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
    
    # Flag the config column as modified so SQLAlchemy detects the change
    flag_modified(integration, "config")
    
    db.commit()
    db.refresh(integration)
    
    return {
        "status": "success",
        "message": f"Service mapping added: {mapping.service_name} -> {mapping.repo_name}",
        "service_mappings": integration.config.get("service_mappings", {})
    }

@app.put("/integrations/{integration_id}/service-mappings")
def update_service_mappings(integration_id: int, update: ServiceMappingsUpdateRequest, request: Request, db: Session = Depends(get_db)):
    """Update all service-to-repo mappings at once."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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
    
    # Flag the config column as modified so SQLAlchemy detects the change
    flag_modified(integration, "config")
    
    db.commit()
    db.refresh(integration)
    
    return {
        "status": "success",
        "message": "Service mappings updated",
        "service_mappings": integration.config.get("service_mappings", {}),
        "default_repo": integration.config.get("repo_name")
    }

@app.delete("/integrations/{integration_id}/service-mapping/{service_name}")
def remove_service_mapping(integration_id: int, service_name: str, request: Request, db: Session = Depends(get_db)):
    """Remove a service-to-repo mapping."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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
    
    # Flag the config column as modified so SQLAlchemy detects the change
    flag_modified(integration, "config")
    
    db.commit()
    
    return {
        "status": "success",
        "message": f"Service mapping removed: {service_name}",
        "service_mappings": integration.config.get("service_mappings", {})
    }

@app.get("/services")
def list_services(request: Request = None, db: Session = Depends(get_db)):
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

@app.get("/integrations/{integration_id}/repositories")
def list_repositories(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Get list of repositories accessible by the GitHub integration."""
    # Get user_id from request if available, otherwise default to 1
    user_id = get_user_id_from_request(request, db=db)
    
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

@app.put("/integrations/{integration_id}")
def update_integration(
    integration_id: int,
    update_data: dict,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Update integration configuration (default repo, service mappings, etc.).
    This endpoint allows editing the integration after initial connection.
    """
    user_id = get_user_id_from_request(request, db=db)

    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Update allowed fields
    if "name" in update_data:
        integration.name = update_data["name"]

    if "default_repo" in update_data or "repository" in update_data:
        # Initialize config if needed
        if not integration.config:
            integration.config = {}

        # Set default repository
        default_repo = update_data.get("default_repo") or update_data.get("repository")
        integration.config["repo_name"] = default_repo
        integration.project_id = default_repo  # Also set project_id for backward compatibility

    if "service_mappings" in update_data:
        # Initialize config if needed
        if not integration.config:
            integration.config = {}

        integration.config["service_mappings"] = update_data["service_mappings"]

    # Update status if provided
    if "status" in update_data:
        integration.status = update_data["status"]

    integration.updated_at = datetime.utcnow()

    # Flag config as modified for SQLAlchemy
    if integration.config:
        flag_modified(integration, "config")

    db.commit()
    db.refresh(integration)

    # Backfill integration to incidents if repo was set
    if integration.config and integration.config.get("repo_name"):
        backfill_integration_to_incidents(db, integration.id, user_id, integration.config)

    return {
        "status": "success",
        "message": "Integration updated successfully",
        "integration": {
            "id": integration.id,
            "provider": integration.provider,
            "name": integration.name,
            "status": integration.status,
            "default_repo": integration.config.get("repo_name") if integration.config else None,
            "service_mappings": integration.config.get("service_mappings", {}) if integration.config else {}
        }
    }

@app.post("/integrations/{integration_id}/setup")
def complete_integration_setup(
    integration_id: int,
    setup_data: dict,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Complete the initial setup of a GitHub integration.
    Called after OAuth connection to set default repository and optionally service mappings.

    Expected setup_data:
    {
        "default_repo": "owner/repo-name",
        "service_mappings": {
            "service1": "owner/repo1",
            "service2": "owner/repo2"
        }
    }
    """
    user_id = get_user_id_from_request(request, db=db)

    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    if integration.provider != "GITHUB":
        raise HTTPException(
            status_code=400,
            detail="Setup is only supported for GitHub integrations"
        )

    # Validate that we have at least a default repo
    default_repo = setup_data.get("default_repo") or setup_data.get("repository")
    if not default_repo:
        raise HTTPException(
            status_code=400,
            detail="default_repo is required to complete setup"
        )

    # Initialize config if needed
    if not integration.config:
        integration.config = {}

    # Set default repository
    integration.config["repo_name"] = default_repo
    integration.project_id = default_repo

    # Set service mappings if provided
    if "service_mappings" in setup_data:
        integration.config["service_mappings"] = setup_data["service_mappings"]
    elif "service_mappings" not in integration.config:
        integration.config["service_mappings"] = {}

    # Mark integration as active (setup complete)
    integration.status = "ACTIVE"
    integration.updated_at = datetime.utcnow()

    # Flag config as modified
    flag_modified(integration, "config")

    db.commit()
    db.refresh(integration)

    # Backfill integration to existing incidents
    backfill_integration_to_incidents(db, integration.id, user_id, integration.config)

    return {
        "status": "success",
        "message": "Integration setup completed successfully",
        "integration": {
            "id": integration.id,
            "provider": integration.provider,
            "name": integration.name,
            "status": integration.status,
            "default_repo": integration.config.get("repo_name"),
            "service_mappings": integration.config.get("service_mappings", {})
        }
    }

@app.get("/integrations/{integration_id}")
def get_integration_details(
    integration_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific integration."""
    user_id = get_user_id_from_request(request, db=db)

    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    config = integration.config or {}

    return {
        "id": integration.id,
        "provider": integration.provider,
        "name": integration.name,
        "status": integration.status,
        "default_repo": config.get("repo_name") or integration.project_id,
        "service_mappings": config.get("service_mappings", {}),
        "created_at": integration.created_at.isoformat() if integration.created_at else None,
        "updated_at": integration.updated_at.isoformat() if integration.updated_at else None,
        "last_verified": integration.last_verified.isoformat() if integration.last_verified else None
    }

# ============================================================================
# Statistics & Overview
# ============================================================================

@app.get("/stats")
def get_system_stats(request: Request = None, db: Session = Depends(get_db)):
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

# ============================================================================
# Incident Management
# ============================================================================

@app.get("/incidents")
def list_incidents(status: Optional[str] = None, request: Request = None, db: Session = Depends(get_db)):
    """List incidents for the authenticated user only."""
    # Get authenticated user (middleware ensures this is set)
    user_id = get_user_id_from_request(request, db=db)

    # ALWAYS filter by user_id - no exceptions
    query = db.query(Incident).filter(Incident.user_id == user_id)

    if status:
        query = query.filter(Incident.status == status)

    incidents = query.order_by(Incident.last_seen_at.desc()).all()
    return incidents

@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Get incident details including related logs. Triggers AI analysis if not already done."""
    # Get authenticated user (middleware ensures this is set)
    user_id = get_user_id_from_request(request, db=db)

    # ALWAYS filter by user_id to prevent cross-user access
    incident = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.user_id == user_id
    ).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Fetch related logs (ALWAYS filter by user_id)
    logs = []
    if incident.log_ids:
        logs = db.query(LogEntry).filter(
            LogEntry.id.in_(incident.log_ids),
            LogEntry.user_id == user_id
        ).order_by(LogEntry.timestamp.desc()).all()

    # Trigger AI analysis in background if root_cause is not set
    if not incident.root_cause:
        from ai_analysis import analyze_incident_with_openrouter
        background_tasks.add_task(analyze_incident_async, incident_id)

    return {
        "incident": incident,
        "logs": logs
    }

@app.post("/incidents/{incident_id}/analyze")
async def analyze_incident(incident_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Manually trigger AI analysis for an incident."""
    # Get authenticated user (middleware ensures this is set)
    user_id = get_user_id_from_request(request, db=db)

    # ALWAYS filter by user_id to prevent cross-user access
    incident = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.user_id == user_id
    ).first()

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
                "changes": analysis.get("changes", {}),
                "original_contents": analysis.get("original_contents", {}),
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
def update_incident(incident_id: int, update_data: dict, request: Request, db: Session = Depends(get_db)):
    """Update incident status or severity."""
    # Get authenticated user (middleware ensures this is set)
    user_id = get_user_id_from_request(request, db=db)

    # ALWAYS filter by user_id to prevent cross-user modification
    incident = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.user_id == user_id
    ).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    # Store old status to detect changes
    old_status = incident.status
    
    if "status" in update_data:
        incident.status = update_data["status"]
    if "severity" in update_data:
        incident.severity = update_data["severity"]
        
    db.commit()
    db.refresh(incident)
    
    # Send email notification if incident was just resolved
    # Check both old_status and new status to ensure we only send email on actual transition to RESOLVED
    status_changed_to_resolved = (
        "status" in update_data 
        and update_data["status"] == "RESOLVED" 
        and old_status != "RESOLVED"
    )
    
    if status_changed_to_resolved:
        try:
            from email_service import send_incident_resolved_email
            from models import User
            
            # Get user email from incident
            user_email = None
            if incident.user_id:
                user = db.query(User).filter(User.id == incident.user_id).first()
                if user and user.email:
                    user_email = user.email
            
            if user_email:
                # Prepare incident data for email
                # Use updated_at as resolved_at, fallback to current time if not set
                resolved_at = incident.updated_at
                if resolved_at is None:
                    resolved_at = datetime.now()
                
                incident_data = {
                    "id": incident.id,
                    "title": incident.title or "Untitled Incident",
                    "service_name": incident.service_name or "Unknown Service",
                    "severity": incident.severity or "MEDIUM",
                    "status": incident.status,
                    "user_id": incident.user_id,
                    "created_at": incident.created_at.isoformat() if incident.created_at else None,
                    "resolved_at": resolved_at.isoformat() if hasattr(resolved_at, 'isoformat') else str(resolved_at),
                    "root_cause": incident.root_cause or "No root cause analysis available",
                    "action_taken": incident.action_taken or "No action details available"
                }
                
                # Send email notification (non-blocking)
                try:
                    send_incident_resolved_email(
                        recipient_email=user_email,
                        incident=incident_data,
                        db_session=db
                    )
                except Exception as e:
                    # Log error but don't fail the request
                    print(f"⚠️  Failed to send incident resolved email notification: {e}")
            else:
                print(f"⚠️  No user email found for incident {incident.id}, skipping email notification")
        except Exception as e:
            # Log error but don't fail the request
            print(f"⚠️  Error preparing incident resolved email notification: {e}")
            import traceback
            traceback.print_exc()
    
    return incident
