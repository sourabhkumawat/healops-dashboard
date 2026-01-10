from dotenv import load_dotenv
load_dotenv()

# Add src directory to Python path for imports
import sys
from pathlib import Path
_engine_root = Path(__file__).parent
if str(_engine_root) not in sys.path:
    sys.path.insert(0, str(_engine_root))

# Initialize Langtrace before any CrewAI imports
from langtrace_python_sdk import langtrace
import os

langtrace_api_key = os.getenv("LANGTRACE_API_KEY")
if langtrace_api_key:
    langtrace.init(api_key=langtrace_api_key)
    print("✅ Langtrace initialized successfully")
else:
    print("⚠️  LANGTRACE_API_KEY not found in environment. Langtrace tracing disabled.")

import json
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, Query
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import PlainTextResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func
from src.database import engine, Base, get_db, SessionLocal
from src.database import (
    Incident, LogEntry, User, Integration, ApiKey,
    IntegrationStatus, SourceMap, IncidentStatus, IncidentSeverity
)
from src.memory import (
    AgentEvent, AgentPlan, AgentWorkspace, AgentMemoryError,
    AgentMemoryFix, AgentRepoContext, AgentLearningPattern
)  # Ensure memory tables are created
from src.auth import verify_password, get_password_hash, create_access_token, verify_token
from src.auth import encrypt_token, decrypt_token
from src.integrations import generate_api_key, GithubIntegration
from src.integrations.github import get_installation_info, get_installation_repositories
from src.middleware import APIKeyMiddleware, check_rate_limit
from src.memory import ensure_partition_exists_for_timestamp
from datetime import timedelta, datetime
import secrets
import time
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import requests
import redis
import asyncio
import threading
import logging

# Configure logging
logger = logging.getLogger(__name__)

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
            from src.core.ai_analysis import get_repo_name_from_integration
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
from src.middleware import APIKeyMiddleware, AuthenticationMiddleware

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
    from src.services.email.service import send_test_email
    
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


# Agent Events WebSocket Manager
class AgentEventManager:
    """Manages WebSocket connections for agent events."""
    
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, incident_id: int):
        """Connect a WebSocket for a specific incident."""
        await websocket.accept()
        if incident_id not in self.active_connections:
            self.active_connections[incident_id] = []
        self.active_connections[incident_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, incident_id: int):
        """Disconnect a WebSocket."""
        if incident_id in self.active_connections:
            if websocket in self.active_connections[incident_id]:
                self.active_connections[incident_id].remove(websocket)
            if not self.active_connections[incident_id]:
                del self.active_connections[incident_id]
    
    async def broadcast(self, incident_id: int, event: Dict[str, Any]):
        """Broadcast event to all connected clients for an incident."""
        if incident_id in self.active_connections:
            message = json.dumps(event)
            disconnected = []
            for websocket in self.active_connections[incident_id]:
                try:
                    await websocket.send_text(message)
                except Exception:
                    disconnected.append(websocket)
            
            # Remove disconnected websockets
            for ws in disconnected:
                self.disconnect(ws, incident_id)

agent_event_manager = AgentEventManager()

# Export for use in agent_orchestrator
__all__ = ['agent_event_manager']

@app.websocket("/ws/agent-events/{incident_id}")
async def agent_events_websocket(websocket: WebSocket, incident_id: int):
    """WebSocket endpoint for streaming agent thinking events in real-time."""
    await agent_event_manager.connect(websocket, incident_id)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        agent_event_manager.disconnect(websocket, incident_id)

# ============================================================================
# Slack Webhooks
# ============================================================================

@app.post("/slack/events")
async def slack_events(request: Request):
    """
    Handle Slack Events API webhook.
    Receives events like app_mentions, messages, etc.
    """
    try:
        import hmac
        import hashlib
        import time
        
        # Read body once (can't read twice in FastAPI)
        body_bytes = await request.body()
        body_str = body_bytes.decode('utf-8')
        
        # Parse request body first to check for challenge
        try:
            data = json.loads(body_str)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
        # Handle URL verification challenge FIRST (before signature verification)
        # Slack sends this during initial setup and it doesn't require signature verification
        if data.get("type") == "url_verification":
            challenge = data.get("challenge")
            if challenge:
                print(f"✅ Received Slack URL verification challenge: {challenge[:20]}...")
                return {"challenge": challenge}
            else:
                print("❌ Slack challenge request missing 'challenge' parameter")
                raise HTTPException(status_code=400, detail="Challenge parameter missing")
        
        # For all other requests, verify Slack request signature
        signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        if signing_secret:
            timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
            signature = request.headers.get("X-Slack-Signature", "")
            
            if not timestamp or not signature:
                raise HTTPException(status_code=401, detail="Missing Slack signature headers")
            
            # Check timestamp to prevent replay attacks (5 minute window)
            try:
                if abs(time.time() - int(timestamp)) > 60 * 5:
                    raise HTTPException(status_code=400, detail="Request timestamp too old")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid timestamp format")
            
            # Verify signature
            sig_basestring = f"v0:{timestamp}:{body_str}"
            computed_signature = "v0=" + hmac.new(
                signing_secret.encode(),
                sig_basestring.encode(),
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(computed_signature, signature):
                raise HTTPException(status_code=401, detail="Invalid Slack signature")
        
        # Handle event callbacks
        if data.get("type") == "event_callback":
            event = data.get("event", {})
            event_type = event.get("type")
            
            print(f"📥 Received Slack event: type={event_type}, channel={event.get('channel', 'unknown')}")
            
            # Handle app mentions (Slack sends "app_mention" singular, not "app_mentions")
            if event_type == "app_mention" or event_type == "app_mentions":
                # Skip bot messages to prevent recursive responses
                subtype = event.get("subtype")
                user_id = event.get("user")
                if subtype == "bot_message" or not user_id:
                    print(f"⏭️  Skipping app_mention from bot/system (subtype: {subtype}, user_id: {user_id})")
                    return {"status": "ok"}
                
                print(f"🔔 App mention detected from user {user_id}: {event.get('text', '')[:100]}...")
                await handle_slack_mention(event, data.get("team_id"))
                return {"status": "ok"}
            
            # Handle direct messages
            if event_type == "message" and event.get("channel_type") == "im":
                # Skip bot messages to prevent recursive responses
                subtype = event.get("subtype")
                user_id = event.get("user")
                if subtype == "bot_message" or not user_id:
                    print(f"⏭️  Skipping DM from bot/system (subtype: {subtype}, user_id: {user_id})")
                    return {"status": "ok"}
                
                print(f"💬 Direct message detected from user {user_id}: {event.get('text', '')[:100]}...")
                await handle_slack_dm(event, data.get("team_id"))
                return {"status": "ok"}
            
            # Handle channel messages in threads (when user replies to bot's message)
            if event_type == "message" and event.get("channel_type") == "channel":
                # Skip bot messages to prevent recursive responses
                subtype = event.get("subtype")
                event_user_id = event.get("user")
                event_text = event.get("text", "")
                channel_id = event.get("channel")  # Extract channel_id from event
                
                # Skip bot messages, message updates, and system messages
                if subtype == "bot_message" or subtype == "message_changed" or not event_user_id:
                    print(f"⏭️  Skipping bot/system message (subtype: {subtype}, user_id: {event_user_id})")
                    return {"status": "ok"}
                
                # Skip messages we're currently updating (prevent duplicate processing)
                event_ts = event.get("ts")
                if event_ts and event_ts in _updating_messages:
                    print(f"⏭️  Skipping message we're currently updating (ts: {event_ts[:10]}...)")
                    return {"status": "ok"}
                
                # Skip messages we just posted (prevent recursive responses)
                if event_ts and event_ts in _recently_posted_messages:
                    print(f"⏭️  Skipping message we just posted (ts: {event_ts[:10]}...)")
                    return {"status": "ok"}
                
                # Also skip if message looks like our thinking indicator (extra safeguard)
                if "💭 Thinking" in event_text or "Thinking..." in event_text:
                    print(f"⏭️  Skipping thinking indicator message")
                    return {"status": "ok"}
                
                thread_ts = event.get("thread_ts")
                # Only handle if it's a reply in a thread (has thread_ts)
                if thread_ts:
                    # IMPORTANT: Check if this is a thread we just responded to
                    # When we post with thread_ts=ts, Slack sends it back with thread_ts=ts
                    # We need to skip it immediately
                    if thread_ts in _recently_responded_threads:
                        print(f"⏭️  Skipping message in thread we just responded to (thread_ts: {thread_ts[:10]}...)")
                        return {"status": "ok"}
                    
                    # IMPORTANT: Check if message is from bot BEFORE checking conversation context
                    # This prevents processing bot's own messages even if subtype check fails
                    # Try database first (faster), then fallback to API
                    bot_user_id = get_bot_user_id_from_db(channel_id) or get_bot_user_id()
                    if bot_user_id and event_user_id == bot_user_id:
                        print(f"⏭️  Skipping message from bot itself (user_id: {event_user_id}, bot_user_id: {bot_user_id})")
                        return {"status": "ok"}
                    
                    # Check if this thread has bot messages (conversation context exists)
                    # Only process if we have conversation context (meaning bot has responded before)
                    # AND we haven't just responded to this thread
                    thread_id = thread_ts
                    if thread_id in _conversation_contexts:
                        print(f"💬 Thread reply detected: {event_text[:100]}...")
                        await handle_thread_reply(event, data.get("team_id"), thread_id)
                        return {"status": "ok"}
                    else:
                        print(f"📢 Channel message in thread (no bot context): {event_text[:100]}...")
                else:
                    print(f"📢 Channel message (not in thread): {event_text[:100]}...")
                pass
        
        # Log if we receive an unexpected event type
        if data.get("type") != "url_verification" and data.get("type") != "event_callback":
            print(f"⚠️  Unexpected Slack event type: {data.get('type')}")
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error handling Slack event: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing Slack event: {str(e)}")


@app.post("/slack/interactive")
async def slack_interactive(request: Request):
    """
    Handle Slack Interactive Components (buttons, modals, etc.).
    Note: Slack sends interactive components as application/x-www-form-urlencoded.
    """
    try:
        import hmac
        import hashlib
        import urllib.parse
        
        # Read body for signature verification
        body_bytes = await request.body()
        body_str = body_bytes.decode('utf-8')
        
        # Verify Slack request signature
        signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        if signing_secret:
            timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
            signature = request.headers.get("X-Slack-Signature", "")
            
            if timestamp and signature:
                # Verify signature (body is already in form-encoded format)
                sig_basestring = f"v0:{timestamp}:{body_str}"
                computed_signature = "v0=" + hmac.new(
                    signing_secret.encode(),
                    sig_basestring.encode(),
                    hashlib.sha256
                ).hexdigest()
                
                if not hmac.compare_digest(computed_signature, signature):
                    raise HTTPException(status_code=401, detail="Invalid Slack signature")
        
        # Parse form data (need to restore body for FastAPI form parser)
        # Create a new request with the body restored
        async def receive():
            return {"type": "http.request", "body": body_bytes}
        request._receive = receive
        
        form_data = await request.form()
        payload_str = form_data.get("payload", "{}")
        payload = json.loads(payload_str)
        
        # Handle different interactive component types
        if payload.get("type") == "block_actions":
            # Handle button clicks, etc.
            pass
        elif payload.get("type") == "view_submission":
            # Handle modal submissions
            pass
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error handling Slack interactive component: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing Slack interaction: {str(e)}")


async def handle_slack_mention(event: Dict[str, Any], team_id: Optional[str]):
    """
    Handle when the bot is mentioned in a channel.
    
    Args:
        event: Slack event data
        team_id: Slack workspace team ID
    """
    try:
        from src.services.slack.service import SlackService
        from src.database.models import AgentEmployee
        from src.database.database import SessionLocal
        
        channel_id = event.get("channel")
        user_id = event.get("user")
        text = event.get("text", "")
        ts = event.get("ts")
        subtype = event.get("subtype")
        
        # Skip bot messages (bot responding to itself)
        if subtype == "bot_message" or subtype == "message_changed" or not user_id:
            print(f"⏭️  Skipping bot/system message (subtype: {subtype}, user_id: {user_id})")
            return
        
        # Skip messages we just posted (prevent recursive responses)
        if ts and ts in _recently_posted_messages:
            print(f"⏭️  Skipping message we just posted in mention handler (ts: {ts[:10]}...)")
            return
        
        # Also skip if message looks like a bot thinking indicator or response
        if "💭 Thinking" in text or "Thinking..." in text:
            print(f"⏭️  Skipping thinking indicator message")
            return
        
        # Early check: Get bot user ID and verify message is not from bot
        channel_id = event.get("channel")
        if channel_id:
            bot_user_id = get_bot_user_id_from_db(channel_id) or get_bot_user_id()
            if bot_user_id and user_id == bot_user_id:
                print(f"⏭️  Skipping mention from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id})")
                return
        
        print(f"📩 Slack mention received from user {user_id}: {text[:100]}...")
        
        # Parse mention to extract agent name and query
        # Format: "@healops-agent @alexandra.chen what are you working on?"
        agent_name_match = None
        query = text.lower()
        
        # Find agent mentions in text (format: @agent-name or agent name)
        db = SessionLocal()
        try:
            # Get all agent employees for this channel
            agents = db.query(AgentEmployee).filter(
                AgentEmployee.slack_channel_id == channel_id
            ).all()
            
            print(f"🔍 Found {len(agents)} agent(s) for channel {channel_id}")
            if not agents:
                # Try to find agents without channel filter (in case channel_id format is different)
                all_agents = db.query(AgentEmployee).all()
                print(f"⚠️  No agents found for channel {channel_id}, but {len(all_agents)} agent(s) exist total")
                print(f"   Channel IDs in DB: {[a.slack_channel_id for a in all_agents if a.slack_channel_id]}")
            
            # Try to match agent name from text
            for agent in agents:
                # Check for agent name in mention
                agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                agent_last_name = agent.name.split()[-1].lower() if len(agent.name.split()) > 1 else ""
                
                if agent_first_name in query or agent.name.lower() in query:
                    agent_name_match = agent
                    print(f"✅ Matched agent by name: {agent.name}")
                    break
            
            # If no specific agent mentioned, use first agent in channel
            if not agent_name_match and agents:
                agent_name_match = agents[0]
                print(f"✅ Using first agent in channel: {agent_name_match.name}")
            
            if not agent_name_match:
                print(f"⚠️  No agent found for channel {channel_id}")
                print(f"   Query text: {query}")
                print(f"   Channel ID received: {channel_id}")
                
                # Try to find any agent (fallback if channel_id doesn't match)
                all_agents = db.query(AgentEmployee).all()
                if all_agents:
                    print(f"   Attempting to use first available agent from {len(all_agents)} total agents")
                    agent_name_match = all_agents[0]
                    print(f"   Using agent: {agent_name_match.name} (channel: {agent_name_match.slack_channel_id})")
                else:
                    # Try to send a helpful message back to the channel
                    try:
                        bot_token = os.getenv("SLACK_BOT_TOKEN")
                        if bot_token:
                            from src.services.slack.service import SlackService
                            slack_service = SlackService(bot_token)
                            slack_service.client.chat_postMessage(
                                channel=channel_id,
                                text="⚠️ No agent employee found in the system. Please run the onboarding script: `python onboard_agent_employee.py --role coding_agent --slack-channel \"#engineering\"`",
                                thread_ts=ts
                            )
                    except Exception as e:
                        print(f"⚠️  Could not send error message to Slack: {e}")
                return
            
            # Initialize Slack service
            from src.auth.crypto_utils import decrypt_token
            bot_token = None
            
            # Try to get token from agent's stored token (encrypted)
            if agent_name_match.slack_bot_token:
                try:
                    decrypted = decrypt_token(agent_name_match.slack_bot_token)
                    if decrypted:
                        bot_token = decrypted
                        print(f"✅ Retrieved bot token from agent's stored token (decrypted successfully)")
                    else:
                        print(f"⚠️  Decryption returned empty string - token may be corrupted or encryption key changed")
                        bot_token = None
                except Exception as e:
                    print(f"⚠️  Failed to decrypt stored token: {e}")
                    import traceback
                    traceback.print_exc()
                    bot_token = None
            
            # Fallback to environment variable
            if not bot_token:
                bot_token = os.getenv("SLACK_BOT_TOKEN")
                if bot_token:
                    print(f"✅ Using bot token from environment variable")
            
            if not bot_token:
                print("⚠️  No Slack bot token available")
                print(f"   Agent has stored token: {'YES' if agent_name_match.slack_bot_token else 'NO'}")
                print(f"   Environment variable SLACK_BOT_TOKEN: {'SET' if os.getenv('SLACK_BOT_TOKEN') else 'NOT SET'}")
                # Try to send error message using any available token
                try:
                    # Last resort: try to use the raw token if it's not encrypted
                    if agent_name_match.slack_bot_token and not agent_name_match.slack_bot_token.startswith('gAAAAA'):
                        bot_token = agent_name_match.slack_bot_token
                        print(f"⚠️  Attempting to use raw token (may not be encrypted)")
                except:
                    pass
                
                if not bot_token:
                    return
            
            slack_service = SlackService(bot_token)
            
            # Check if message is from bot itself (prevent recursive responses)
            bot_user_id = agent_name_match.slack_user_id or slack_service.bot_user_id
            if bot_user_id and user_id == bot_user_id:
                print(f"⏭️  Skipping message from bot itself (user_id: {user_id})")
                return
            
            # Use thread_ts as conversation context ID (but don't create context yet)
            # We'll create context AFTER posting to prevent recursive responses
            thread_id = event.get("thread_ts") or ts  # Use existing thread or create new one
            
            # Get conversation history (if it exists) but don't create new context yet
            conversation_history = None
            if thread_id in _conversation_contexts:
                conversation_history = get_conversation_context(thread_id)
                print(f"📚 Found existing conversation context with {len(conversation_history)} messages")
            else:
                print(f"📝 No existing conversation context, starting new thread")
            
            # Refresh agent status from database to get latest status (in case it changed)
            db.refresh(agent_name_match)
            logger.debug(
                "Refreshed agent status from database",
                extra={
                    "agent_name": agent_name_match.name,
                    "status": agent_name_match.status,
                    "has_current_task": bool(agent_name_match.current_task)
                }
            )
            
            # Post thinking indicator immediately to show bot is processing
            thinking_ts = slack_service.post_thinking_indicator(channel_id, thread_ts=ts)
            
            # Generate LLM-powered response with conversation context (this takes time)
            # Pass conversation_history but don't add to context yet
            response_text = generate_agent_response_llm(agent_name_match, query, thread_id=None, conversation_history=conversation_history)
            
            # Delete the thinking indicator and post the actual response
            # This prevents duplicate messages that can occur when updating
            if thinking_ts:
                try:
                    # Delete the thinking indicator message
                    slack_service.client.chat_delete(channel=channel_id, ts=thinking_ts)
                    print(f"✅ Deleted thinking indicator message")
                except Exception as e:
                    print(f"⚠️  Could not delete thinking indicator: {e}")
                    # Continue to post response anyway
                
                # Post the actual response
                response = slack_service.client.chat_postMessage(
                    channel=channel_id,
                    text=response_text,
                    thread_ts=ts
                )
                # Track this message to prevent recursive processing
                if response.get("ok") and response.get("ts"):
                    response_ts = response.get("ts")
                    _recently_posted_messages.add(response_ts)
                    
                    # Track this thread as recently responded to
                    # When we post with thread_ts=ts, Slack will send it back with thread_ts=ts
                    # We need to skip it when it comes back
                    if ts:
                        _recently_responded_threads.add(ts)
                        print(f"🔒 Tracked thread {ts[:10]}... as recently responded to")
                        # Clean up after 3 seconds (enough time for Slack to send events back)
                        import threading
                        def clear_thread_tracking():
                            import time
                            time.sleep(3)
                            _recently_responded_threads.discard(ts)
                            print(f"🔓 Removed thread {ts[:10]}... from tracking")
                        threading.Thread(target=clear_thread_tracking, daemon=True).start()
                    
                    # NOW add to conversation context AFTER successfully posting
                    # Use a delay to prevent the bot from responding to its own messages
                    import threading
                    def add_context_after_posting():
                        import time
                        time.sleep(2)  # Wait 2 seconds before adding context to prevent recursion
                        # Only add context if thread_id is valid (not None)
                        if thread_id:
                            # Clean up the query (remove bot mentions)
                            clean_query = query
                            add_to_conversation_context(thread_id, "user", clean_query)
                            add_to_conversation_context(thread_id, "assistant", response_text)
                            print(f"📚 Added conversation context for thread {thread_id[:10]}... (after 2s delay)")
                    
                    threading.Thread(target=add_context_after_posting, daemon=True).start()
                    
                    print(f"✅ Posted response message (ts: {response_ts[:10]}...), tracked to prevent recursion")
                    print(f"   Tracked messages: {len(_recently_posted_messages)}")
                    print(f"   Tracked threads: {len(_recently_responded_threads)}")
                    # Clean up after 10 seconds
                    import threading
                    def clear_posted_message():
                        import time
                        time.sleep(10)
                        _recently_posted_messages.discard(response_ts)
                        print(f"🗑️  Removed message {response_ts[:10]}... from tracking")
                    threading.Thread(target=clear_posted_message, daemon=True).start()
                else:
                    print(f"⚠️  Failed to post response or get timestamp: {response}")
                    print(f"✅ Posted response message (no tracking)")
            else:
                # Fallback: if thinking indicator failed, just post the response
                print(f"⚠️  No thinking_ts available, posting response as new message")
                response = slack_service.client.chat_postMessage(
                    channel=channel_id,
                    text=response_text,
                    thread_ts=ts
                )
                # Track this message to prevent recursive processing
                if response.get("ok") and response.get("ts"):
                    response_ts = response.get("ts")
                    _recently_posted_messages.add(response_ts)
                    print(f"✅ Posted response message fallback (ts: {response_ts[:10]}...)")
                    # Clean up after 10 seconds
                    import threading
                    def clear_posted_message():
                        import time
                        time.sleep(10)
                        _recently_posted_messages.discard(response_ts)
                    threading.Thread(target=clear_posted_message, daemon=True).start()
            
        finally:
            db.close()
            
    except Exception as e:
        print(f"❌ Error handling Slack mention: {e}")
        import traceback
        traceback.print_exc()
        # Try to send error message to Slack if possible
        try:
            if 'slack_service' in locals() and 'channel_id' in locals():
                slack_service.client.chat_postMessage(
                    channel=channel_id,
                    text=f"⚠️ Sorry, I encountered an error: {str(e)}"
                )
        except:
            pass  # Don't fail if we can't send error message


async def handle_slack_dm(event: Dict[str, Any], team_id: Optional[str]):
    """
    Handle direct messages to the bot.
    
    Args:
        event: Slack event data
        team_id: Slack workspace team ID
    """
    try:
        # Similar to handle_slack_mention but for DMs
        # For now, redirect to mention handler
        await handle_slack_mention(event, team_id)
    except Exception as e:
        print(f"❌ Error handling Slack DM: {e}")
        import traceback
        traceback.print_exc()


async def handle_thread_reply(event: Dict[str, Any], team_id: Optional[str], thread_id: str):
    """
    Handle when a user replies to the bot's message in a thread.
    
    Args:
        event: Slack event data
        team_id: Slack workspace team ID
        thread_id: Thread timestamp (used as conversation context ID)
    """
    try:
        from src.services.slack.service import SlackService
        from src.database.models import AgentEmployee
        from src.database.database import SessionLocal
        
        channel_id = event.get("channel")
        user_id = event.get("user")
        text = event.get("text", "")
        ts = event.get("ts")
        thread_ts = event.get("thread_ts")
        subtype = event.get("subtype")
        
        # Skip bot messages and system messages to prevent recursive responses
        if subtype == "bot_message" or subtype == "message_changed" or not user_id:
            print(f"⏭️  Skipping bot/system message in thread (subtype: {subtype}, user_id: {user_id})")
            return
        
        # Skip messages we just posted (prevent recursive responses)
        if ts and ts in _recently_posted_messages:
            print(f"⏭️  Skipping message we just posted in thread reply handler (ts: {ts[:10]}...)")
            return
        
        # Also skip if message looks like a bot thinking indicator to prevent recursive responses
        if "💭 Thinking" in text or "Thinking..." in text:
            print(f"⏭️  Skipping thinking indicator message in thread")
            return
        
        # Early check: Get bot user ID and verify message is not from bot
        bot_user_id = None
        if channel_id:
            bot_user_id = get_bot_user_id_from_db(channel_id) or get_bot_user_id()
            if bot_user_id and user_id == bot_user_id:
                print(f"⏭️  Skipping thread reply from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id})")
                return
        
        print(f"💬 Thread reply received from user {user_id}: {text[:100]}...")
        
        # Find agent for this channel
        db = SessionLocal()
        try:
            # Get agent for this channel
            agents = db.query(AgentEmployee).filter(
                AgentEmployee.slack_channel_id == channel_id
            ).all()
            
            if not agents:
                # Try to find any agent
                agents = db.query(AgentEmployee).all()
            
            if not agents:
                print(f"⚠️  No agent found for thread reply")
                return
            
            agent = agents[0]  # Use first available agent
            
            # Get bot token
            from src.auth.crypto_utils import decrypt_token
            bot_token = None
            
            if agent.slack_bot_token:
                try:
                    decrypted = decrypt_token(agent.slack_bot_token)
                    if decrypted:
                        bot_token = decrypted
                except:
                    pass
            
            if not bot_token:
                bot_token = os.getenv("SLACK_BOT_TOKEN")
            
            if not bot_token:
                print("⚠️  No Slack bot token available for thread reply")
                return
            
            slack_service = SlackService(bot_token)
            
            # Check if message is from bot itself (prevent recursive responses)
            bot_user_id = agent.slack_user_id or slack_service.bot_user_id
            if bot_user_id and user_id == bot_user_id:
                print(f"⏭️  Skipping thread reply from bot itself (user_id: {user_id})")
                return
            
            # Refresh agent status from database to get latest status (in case it changed)
            db.refresh(agent)
            logger.debug(
                "Refreshed agent status from database for thread reply",
                extra={
                    "agent_name": agent.name,
                    "status": agent.status,
                    "has_current_task": bool(agent.current_task)
                }
            )
            
            # Use thread_ts as conversation context ID (parameter thread_id is the thread timestamp)
            conversation_thread_id = thread_ts or thread_id or ts  # Use thread_ts if available, otherwise use thread_id parameter or ts
            
            # Post thinking indicator immediately to show bot is processing
            thinking_ts = slack_service.post_thinking_indicator(channel_id, thread_ts=thread_ts)
            
            # Get existing conversation history (if thread exists) but don't create new context
            conversation_history = None
            if conversation_thread_id and conversation_thread_id in _conversation_contexts:
                conversation_history = get_conversation_context(conversation_thread_id)
                print(f"📚 Found existing conversation context with {len(conversation_history)} messages")
            
            # Generate LLM-powered response with conversation context (this takes time)
            # Pass conversation_history but don't add to context yet
            response_text = generate_agent_response_llm(agent, text, thread_id=None, conversation_history=conversation_history)
            
            # Delete the thinking indicator and post the actual response
            # This prevents duplicate messages that can occur when updating
            if thinking_ts:
                try:
                    # Delete the thinking indicator message
                    slack_service.client.chat_delete(channel=channel_id, ts=thinking_ts)
                    print(f"✅ Deleted thinking indicator message in thread")
                except Exception as e:
                    print(f"⚠️  Could not delete thinking indicator in thread: {e}")
                    # Continue to post response anyway
                
                # Post the actual response
                response = slack_service.client.chat_postMessage(
                    channel=channel_id,
                    text=response_text,
                    thread_ts=thread_ts
                )
                # Track this message to prevent recursive processing
                if response.get("ok") and response.get("ts"):
                    response_ts = response.get("ts")
                    _recently_posted_messages.add(response_ts)
                    
                    # Track this thread as recently responded to
                    # When we post with thread_ts=thread_ts, Slack will send it back with thread_ts=thread_ts
                    if thread_ts:
                        _recently_responded_threads.add(thread_ts)
                        print(f"🔒 Tracked thread {thread_ts[:10]}... as recently responded to")
                        # Clean up after 3 seconds
                        import threading
                        def clear_thread_tracking():
                            import time
                            time.sleep(3)
                            _recently_responded_threads.discard(thread_ts)
                            print(f"🔓 Removed thread {thread_ts[:10]}... from tracking")
                        threading.Thread(target=clear_thread_tracking, daemon=True).start()
                    
                    # Add to conversation context AFTER posting with delay
                    import threading
                    def add_context_after_posting():
                        import time
                        time.sleep(2)  # Wait 2 seconds before adding context to prevent recursion
                        if thread_id:
                            add_to_conversation_context(thread_id, "user", text)
                            add_to_conversation_context(thread_id, "assistant", response_text)
                            print(f"📚 Added conversation context for thread {thread_id[:10]}... (after 2s delay)")
                    
                    threading.Thread(target=add_context_after_posting, daemon=True).start()
                    
                    print(f"✅ Posted thread reply (ts: {response_ts[:10]}...), tracked to prevent recursion")
                    print(f"   Tracked threads: {len(_recently_responded_threads)}")
                    # Clean up after 10 seconds
                    import threading
                    def clear_posted_message():
                        import time
                        time.sleep(10)
                        _recently_posted_messages.discard(response_ts)
                    threading.Thread(target=clear_posted_message, daemon=True).start()
                else:
                    print(f"✅ Posted thread reply")
            else:
                # Fallback: if thinking indicator failed, just post the response
                print(f"⚠️  No thinking_ts available for thread, posting response as new message")
                response = slack_service.client.chat_postMessage(
                    channel=channel_id,
                    text=response_text,
                    thread_ts=thread_ts
                )
                # Track this message to prevent recursive processing
                if response.get("ok") and response.get("ts"):
                    response_ts = response.get("ts")
                    _recently_posted_messages.add(response_ts)
                    print(f"✅ Posted thread reply fallback (ts: {response_ts[:10]}...)")
                    # Clean up after 10 seconds
                    import threading
                    def clear_posted_message():
                        import time
                        time.sleep(10)
                        _recently_posted_messages.discard(response_ts)
                    threading.Thread(target=clear_posted_message, daemon=True).start()
                else:
                    print(f"✅ Sent thread reply")
            
        finally:
            db.close()
            
    except Exception as e:
        print(f"❌ Error handling thread reply: {e}")
        import traceback
        traceback.print_exc()


# Conversation context storage (in-memory, can be moved to Redis for production)
_conversation_contexts: Dict[str, List[Dict[str, str]]] = {}

# Cache bot user ID to prevent recursive responses (avoid creating SlackService repeatedly)
_cached_bot_user_id: Optional[str] = None

# Track message timestamps we're currently updating to prevent duplicate posts
_updating_messages: set = set()

# Track message timestamps we just posted to prevent recursive processing
_recently_posted_messages: set = set()

# Track threads we just responded to (by thread_ts) to prevent recursive responses
_recently_responded_threads: set = set()

def get_bot_user_id_from_db(channel_id: str) -> Optional[str]:
    """Get bot user ID from database for a specific channel (faster than API call)."""
    try:
        from src.database.models import AgentEmployee
        from src.database.database import SessionLocal
        
        db = SessionLocal()
        try:
            agent = db.query(AgentEmployee).filter(
                AgentEmployee.slack_channel_id == channel_id
            ).first()
            
            if agent and agent.slack_user_id:
                return agent.slack_user_id
            
            # Try any agent as fallback
            agent = db.query(AgentEmployee).first()
            if agent and agent.slack_user_id:
                return agent.slack_user_id
        finally:
            db.close()
    except Exception as e:
        print(f"⚠️  Error getting bot user ID from DB: {e}")
    return None

def get_bot_user_id() -> Optional[str]:
    """Get bot user ID, caching it for performance."""
    global _cached_bot_user_id
    if _cached_bot_user_id:
        return _cached_bot_user_id
    
    try:
        bot_token = os.getenv("SLACK_BOT_TOKEN")
        if bot_token:
            from src.services.slack.service import SlackService
            slack_service = SlackService(bot_token)
            _cached_bot_user_id = slack_service.bot_user_id
            return _cached_bot_user_id
    except Exception as e:
        print(f"⚠️  Error getting bot user ID: {e}")
    return None

def get_conversation_context(thread_id: str, max_messages: int = 10) -> List[Dict[str, str]]:
    """Get conversation history for a thread."""
    if thread_id not in _conversation_contexts:
        _conversation_contexts[thread_id] = []
    # Return last N messages
    return _conversation_contexts[thread_id][-max_messages:]

def add_to_conversation_context(thread_id: str, role: str, content: str):
    """Add a message to conversation context."""
    if thread_id not in _conversation_contexts:
        _conversation_contexts[thread_id] = []
    _conversation_contexts[thread_id].append({"role": role, "content": content})
    # Keep only last 20 messages to prevent memory issues
    if len(_conversation_contexts[thread_id]) > 20:
        _conversation_contexts[thread_id] = _conversation_contexts[thread_id][-20:]

def generate_agent_response_llm(agent: Any, query: str, thread_id: str = None, conversation_history: List[Dict[str, str]] = None) -> str:
    """
    Generate an LLM-powered response from an agent.
    
    Args:
        agent: AgentEmployee object
        query: User's query text
        thread_id: Thread ID for conversation context
        conversation_history: Previous messages in the conversation
    
    Returns:
        Response text
    """
    api_key = os.getenv("OPENCOUNCIL_API")
    if not api_key:
        # Fallback to simple responses if LLM not configured
        return generate_agent_response_simple(agent, query)
    
    # Build system prompt with agent context
    system_prompt = f"""You are {agent.name}, a {agent.role} from the {agent.department} department at HealOps.

Your role: {agent.role}
Department: {agent.department}
Current status: {agent.status}
Current task: {agent.current_task or "No active task"}
Capabilities: {', '.join(agent.capabilities or [])}

You are an AI agent employee helping with incident resolution, code fixes, and technical support. 
Be helpful, professional, and concise. You can discuss:
- Your current work and tasks
- Technical questions about incidents and code
- Status updates on ongoing work
- General questions about your capabilities

Keep responses conversational and friendly. If asked about specific incidents or code, provide helpful information based on your role and capabilities."""

    # Build messages with conversation history
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add conversation history if provided
    if conversation_history:
        messages.extend(conversation_history)
    
    # Add current query
    messages.append({"role": "user", "content": query})
    
    try:
        import requests
        from src.core.ai_analysis import MODEL_CONFIG
        
        # Use chat model from config (free Xiaomi model)
        chat_config = MODEL_CONFIG.get("chat", MODEL_CONFIG["simple_analysis"])
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", "https://healops.ai"),
                "X-Title": "HealOps Agent Chat",
            },
            json={
                "model": chat_config["model"],
                "messages": messages,
                "temperature": chat_config["temperature"],
                "max_tokens": chat_config["max_tokens"],
            },
            timeout=15
        )
        
        if response.status_code == 200:
            result = response.json()
            assistant_message = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if assistant_message:
                # NOTE: Do NOT add to conversation context here - we'll add it AFTER posting
                # to prevent the bot from responding to its own messages
                # The conversation context will be added after the message is successfully posted
                return assistant_message
            else:
                return generate_agent_response_simple(agent, query)
        else:
            print(f"⚠️  LLM API error: {response.status_code} - {response.text[:200]}")
            return generate_agent_response_simple(agent, query)
            
    except Exception as e:
        print(f"⚠️  Error calling LLM: {e}")
        import traceback
        traceback.print_exc()
        return generate_agent_response_simple(agent, query)

def generate_agent_response_simple(agent: Any, query: str) -> str:
    """
    Simple keyword-based fallback responses.
    
    Args:
        agent: AgentEmployee object
        query: User's query text
    
    Returns:
        Response text
    """
    query_lower = query.lower()
    
    # Simple keyword-based responses
    if "what are you working on" in query_lower or "current task" in query_lower:
        if agent.current_task:
            return f"🚀 I'm currently working on: {agent.current_task}"
        else:
            return f"💤 I'm currently idle. No active tasks."
    
    if "completed" in query_lower or "what did you do" in query_lower:
        completed = agent.completed_tasks or []
        if completed:
            tasks_text = "\n".join([f"• {task}" for task in completed[-5:]])
            return f"✅ Recently completed tasks:\n{tasks_text}"
        else:
            return "No completed tasks yet."
    
    if "status" in query_lower or "what's your status" in query_lower:
        status_emoji = {"available": "✅", "working": "⚙️", "idle": "💤"}.get(agent.status, "❓")
        return f"{status_emoji} Status: {agent.status}\nDepartment: {agent.department}\nRole: {agent.role}"
    
    # Default response
    return f"Hi! I'm {agent.name}, {agent.role} from {agent.department}. Ask me:\n• 'What are you working on?'\n• 'What have you completed?'\n• 'What's your status?'"

def generate_agent_response(agent: Any, query: str, thread_id: str = None) -> str:
    """
    Generate a response from an agent (uses LLM if available, falls back to simple).
    
    Args:
        agent: AgentEmployee object
        query: User's query text
        thread_id: Thread ID for conversation context
    
    Returns:
        Response text
    """
    # Get conversation history if thread_id provided
    conversation_history = None
    if thread_id:
        conversation_history = get_conversation_context(thread_id)
    
    # Use LLM-powered response
    return generate_agent_response_llm(agent, query, thread_id, conversation_history)

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
async def ingest_otel_errors(payload: OTelErrorPayload, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """
    Ingest OpenTelemetry spans from HealOps SDK.
    Now receives ALL spans (success & error).
    - Broadcasts ALL spans to WebSocket (Live Logs)
    - Persists ONLY ERROR/CRITICAL spans to Database
    """
    # API key is already validated by APIKeyMiddleware and set in request.state
    # Use it to update last_used timestamp
    if not hasattr(request.state, 'api_key') or not request.state.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    valid_key = request.state.api_key
    
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

# GitHub OAuth (legacy - may be removed after migration)
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

# GitHub App configuration
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
GITHUB_CALLBACK_URL = "https://engine.healops.ai/integrations/github/callback"

@app.get("/integrations/github/reconnect")
def github_reconnect(
    request: Request,
    integration_id: int = Query(..., description="The integration ID to reconnect"),
    db: Session = Depends(get_db)
):
    """Handle reconnection by redirecting to GitHub App installation page."""
    if not GITHUB_APP_SLUG:
        raise HTTPException(status_code=500, detail="GitHub App Slug not configured")
    
    # Get the integration to reconnect
    user_id = get_user_id_from_request(request, db=db)
    
    # SECURITY: Verify the integration belongs to the authenticated user
    integration = db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id,  # SECURITY: Verify integration belongs to user
        Integration.provider == "GITHUB"
    ).first()
    
    if not integration:
        raise HTTPException(
            status_code=404, 
            detail=f"GitHub integration with ID {integration_id} not found or does not belong to your account"
        )
    
    # Mark as disconnected to indicate we're reconnecting
    integration.status = "DISCONNECTED"
    db.commit()
    
    # Redirect to GitHub App installation with reconnect flag
    # Use a unique timestamp and nonce to ensure GitHub treats it as a new installation request
    import base64
    state_data = {
        "timestamp": int(time.time() * 1000),  # Use milliseconds for uniqueness
        "user_id": user_id,  # SECURITY: Include user_id to ensure integration is associated with correct user
        "reconnect": True,
        "integration_id": integration_id,
        "nonce": secrets.token_urlsafe(16)  # Add random nonce to force new installation
    }
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
    
    # GitHub App installation URL
    install_url = (
        f"https://github.com/apps/{GITHUB_APP_SLUG}/installations/new"
        f"?state={state}"
    )
    
    return RedirectResponse(install_url)

@app.get("/integrations/github/authorize")
def github_authorize(request: Request, reconnect: Optional[str] = None, integration_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Redirect user to GitHub App installation page.
    
    SECURITY: This endpoint requires authentication to ensure integrations are
    created for the correct user. User ID is included in state parameter.
    """
    if not GITHUB_APP_SLUG:
        raise HTTPException(status_code=500, detail="GitHub App Slug not configured")
    
    # Get authenticated user_id (middleware ensures this is set)
    user_id = get_user_id_from_request(request, db=db)
    
    # Generate state parameter for security
    # Include user_id, integration_id if reconnecting, and a nonce
    state_data = {
        "timestamp": int(time.time() * 1000),  # Use milliseconds for uniqueness
        "user_id": user_id,  # SECURITY: Include user_id to associate integration with correct user
        "reconnect": reconnect == "true",
        "nonce": secrets.token_urlsafe(16)  # Add random nonce to ensure uniqueness
    }
    if integration_id:
        state_data["integration_id"] = integration_id
    
    # Encode state as base64 JSON for passing through installation flow
    import base64
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
    
    # GitHub App installation URL
    # Users can select organizations and repositories during installation
    install_url = (
        f"https://github.com/apps/{GITHUB_APP_SLUG}/installations/new"
        f"?state={state}"
    )
    
    return RedirectResponse(install_url)

@app.get("/integrations/github/callback")
def github_callback(request: Request, installation_id: Optional[str] = None, setup_action: Optional[str] = None, state: Optional[str] = None, db: Session = Depends(get_db)):
    """Handle GitHub App installation callback.
    
    GitHub redirects here after installation with installation_id in query params.
    """
    try:
        if not GITHUB_APP_ID:
            print("ERROR: GITHUB_APP_ID not configured")
            raise HTTPException(status_code=500, detail="GitHub App ID not configured")
        
        if not installation_id:
            print("ERROR: installation_id parameter missing")
            raise HTTPException(status_code=400, detail="installation_id parameter is required")
        
        try:
            installation_id_int = int(installation_id)
        except ValueError:
            print(f"ERROR: Invalid installation_id format: {installation_id}")
            raise HTTPException(status_code=400, detail="Invalid installation_id format")
        
        # Decode state parameter if provided
        reconnect = False
        integration_id = None
        user_id = None
        if state:
            try:
                import base64
                state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
                reconnect = state_data.get("reconnect", False)
                integration_id = state_data.get("integration_id")
                user_id = state_data.get("user_id")  # SECURITY: Extract user_id from state
                print(f"DEBUG: Decoded state - user_id={user_id}, reconnect={reconnect}, integration_id={integration_id}")
            except Exception as e:
                print(f"ERROR: Failed to decode state parameter: {e}")
                import traceback
                traceback.print_exc()
                raise HTTPException(status_code=400, detail=f"Invalid state parameter: {str(e)}")
        
        # SECURITY: user_id is required - try to get from state, or from request state (if authenticated)
        if not user_id:
            # Fallback: try to get user_id from request state (if user is authenticated via session)
            try:
                if hasattr(request.state, 'user_id') and request.state.user_id:
                    user_id = request.state.user_id
                    print(f"DEBUG: Got user_id from request.state: {user_id}")
            except Exception as e:
                print(f"DEBUG: Could not get user_id from request.state: {e}")
                pass
        
        # If still no user_id, this is an invalid request
        if not user_id:
            print("ERROR: No user_id found in state or request")
            error_msg = "Please initiate the GitHub installation from the application settings page."
            return RedirectResponse(f"{FRONTEND_URL}/settings?tab=integrations&error={error_msg}")
        
        print(f"DEBUG: Getting installation info for installation_id={installation_id_int}")
        # Get installation info to get account details
        installation_info = get_installation_info(installation_id_int)
        if not installation_info:
            print(f"ERROR: Failed to retrieve installation info for installation_id={installation_id_int}")
            raise HTTPException(status_code=400, detail="Failed to retrieve installation information from GitHub. Please check your GitHub App configuration.")
        
        account = installation_info.get("account", {})
        account_login = account.get("login", "GitHub App")
        account_type = account.get("type", "User")
        print(f"DEBUG: Installation account: {account_login} (type: {account_type})")
    
        # Handle reconnection: if integration_id is provided and reconnecting, update that specific integration
        # SECURITY: Verify the integration belongs to the user_id from state
        if reconnect and integration_id:
            print(f"DEBUG: Reconnecting integration_id={integration_id}")
            integration = db.query(Integration).filter(
                Integration.id == integration_id,
                Integration.user_id == user_id,  # SECURITY: Verify integration belongs to user
                Integration.provider == "GITHUB"
            ).first()
            if not integration:
                print(f"ERROR: Integration {integration_id} not found for user {user_id}")
                raise HTTPException(
                    status_code=404, 
                    detail=f"Integration not found or does not belong to your account"
                )
            integration.installation_id = installation_id_int
            integration.access_token = None  # Clear OAuth token if present
            integration.status = "CONFIGURING"  # Set to CONFIGURING for repository selection
            integration.last_verified = datetime.utcnow()
            integration.name = f"GitHub ({account_login})"
            # Store installation metadata in config
            if not integration.config:
                integration.config = {}
            integration.config["installation_account"] = account_login
            integration.config["installation_account_type"] = account_type
            db.commit()
            db.refresh(integration)
            print(f"DEBUG: Reconnected integration {integration.id}, redirecting to setup")
            # Redirect to setup page for repository selection (though repos are already selected during installation)
            return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}&reconnected=true")

        # Check if integration already exists for this user
        print(f"DEBUG: Checking for existing integration for user_id={user_id}")
        integration = db.query(Integration).filter(
            Integration.user_id == user_id,
            Integration.provider == "GITHUB"
        ).first()

        if not integration:
            print(f"DEBUG: Creating new integration for user_id={user_id}")
            integration = Integration(
                user_id=user_id,
                provider="GITHUB",
                name=f"GitHub ({account_login})",
                status="CONFIGURING",  # Start in CONFIGURING state
                installation_id=installation_id_int,
                access_token=None,  # GitHub Apps don't use OAuth tokens
                last_verified=datetime.utcnow(),
                config={
                    "installation_account": account_login,
                    "installation_account_type": account_type
                }
            )
            db.add(integration)
            db.commit()
            db.refresh(integration)
            print(f"DEBUG: Created integration {integration.id}, redirecting to setup")
            # Redirect to setup page for initial configuration
            return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}&new=true")
        else:
            print(f"DEBUG: Updating existing integration {integration.id}")
            # Update existing integration with new installation_id
            integration.installation_id = installation_id_int
            integration.access_token = None  # Clear OAuth token if present
            integration.status = "CONFIGURING"  # Set to CONFIGURING for reconfiguration
            integration.last_verified = datetime.utcnow()
            integration.name = f"GitHub ({account_login})"
            if not integration.config:
                integration.config = {}
            integration.config["installation_account"] = account_login
            integration.config["installation_account_type"] = account_type
            db.commit()
            db.refresh(integration)
            print(f"DEBUG: Updated integration {integration.id}, redirecting to setup")
            # Redirect to setup page
            return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}")
    
    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as e:
        # Log unexpected errors
        print(f"ERROR: Unexpected error in github_callback: {e}")
        import traceback
        traceback.print_exc()
        # Redirect to frontend with error
        error_msg = f"Internal server error: {str(e)}"
        return RedirectResponse(f"{FRONTEND_URL}/settings?tab=integrations&error={error_msg}")


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
    from src.integrations import IntegrationRegistry
    
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

# ============================================================================
# GitHub Webhooks
# ============================================================================

@app.post("/integrations/github/webhook")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Handle GitHub webhook events, particularly PR events from Alex.
    Triggers QA review when Alex creates or updates a PR.
    """
    try:
        import hmac
        import hashlib
        
        # Read body
        body_bytes = await request.body()
        body_str = body_bytes.decode('utf-8')
        
        # Verify GitHub webhook signature (optional but recommended)
        github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
        if github_webhook_secret:
            signature_header = request.headers.get("X-Hub-Signature-256")
            if signature_header:
                expected_signature = hmac.new(
                    github_webhook_secret.encode(),
                    body_bytes,
                    hashlib.sha256
                ).hexdigest()
                actual_signature = signature_header.replace("sha256=", "")
                if not hmac.compare_digest(expected_signature, actual_signature):
                    print("⚠️  GitHub webhook signature verification failed")
                    raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Parse webhook payload
        try:
            payload = json.loads(body_str)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
        event_type = request.headers.get("X-GitHub-Event")
        print(f"📥 Received GitHub webhook: event_type={event_type}")
        
        # Handle pull request events
        if event_type == "pull_request":
            action = payload.get("action")
            pr_data = payload.get("pull_request", {})
            
            if action in ["opened", "synchronize"]:  # PR created or updated
                pr_number = pr_data.get("number")
                repo_name = payload.get("repository", {}).get("full_name")
                
                print(f"🔔 PR #{pr_number} {action} in {repo_name}")
                
                # Check if PR was created by Alex using database tracking
                # Since Alex doesn't have a GitHub account, PRs are created by HealOps app
                # We track this in the AgentPR table
                from src.database.models import AgentPR, AgentEmployee
                
                agent_pr = db.query(AgentPR).filter(
                    AgentPR.pr_number == pr_number,
                    AgentPR.repo_name == repo_name
                ).first()
                
                if not agent_pr:
                    print(f"   PR #{pr_number} is not tracked as created by Alex. Skipping review.")
                    return {"status": "ok", "message": "PR not tracked as created by Alex, skipping review"}
                
                # Verify it's by Alex
                alex_agent = db.query(AgentEmployee).filter(
                    AgentEmployee.id == agent_pr.agent_employee_id,
                    AgentEmployee.email == "alexandra.chen@healops.work"
                ).first()
                
                if not alex_agent:
                    print(f"   PR #{pr_number} is not by Alex. Skipping review.")
                    return {"status": "ok", "message": "PR not by Alex, skipping review"}
                
                print(f"✅ PR #{pr_number} confirmed to be created by {alex_agent.name}")
                
                # Find integration for this repository
                integration = db.query(Integration).filter(
                    Integration.provider == "GITHUB",
                    Integration.status == "ACTIVE"
                ).first()
                
                if not integration:
                    print("⚠️  No active GitHub integration found")
                    return {"status": "ok", "message": "No active GitHub integration"}
                
                # Trigger QA review asynchronously
                print(f"🚀 Triggering QA review for PR #{pr_number} by {alex_agent.name}")
                from src.agents.qa_orchestrator import review_pr_for_alex
                import asyncio
                
                # Run review in background
                asyncio.create_task(
                    review_pr_for_alex(
                        repo_name=repo_name,
                        pr_number=pr_number,
                        integration_id=integration.id,
                        user_id=integration.user_id,
                        db=db
                    )
                )
                
                return {
                    "status": "ok",
                    "message": f"QA review triggered for PR #{pr_number}",
                    "pr_number": pr_number,
                    "repo": repo_name
                }
        
        # Handle ping event (webhook setup)
        elif event_type == "ping":
            print("✅ GitHub webhook ping received - webhook is configured correctly")
            return {"status": "ok", "message": "Webhook is active"}
        
        return {"status": "ok", "message": f"Event {event_type} received but not handled"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error handling GitHub webhook: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing webhook: {str(e)}")

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
        
        # Check if this is a GitHub App installation
        if integration.installation_id:
            print("DEBUG: Using GitHub App installation")
            repos_data = get_installation_repositories(integration.installation_id)
            repos = [
                {
                    "full_name": repo.get("full_name"),
                    "name": repo.get("name"),
                    "private": repo.get("private", False)
                }
                for repo in repos_data
            ]
            print(f"DEBUG: Found {len(repos)} repositories from GitHub App installation")
            return {
                "repositories": repos
            }
        else:
            # Legacy OAuth token flow
            github_integration = GithubIntegration(integration_id=integration.id)
            
            # Get user's repositories
            if not github_integration.client:
                print("DEBUG: GitHub client is None")
                return {"repositories": []}
            
            print("DEBUG: Fetching repositories from GitHub (OAuth)...")
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
def list_incidents(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    service: Optional[str] = None,
    request: Request = None,
    db: Session = Depends(get_db)
):
    """List incidents for the authenticated user only."""
    try:
        # Get authenticated user (middleware ensures this is set)
        user_id = get_user_id_from_request(request, db=db)

        # ALWAYS filter by user_id - no exceptions
        query = db.query(Incident).filter(Incident.user_id == user_id)

        # Validate and apply status filter
        if status:
            valid_statuses = [s.value for s in IncidentStatus]
            if status not in valid_statuses:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
                )
            query = query.filter(Incident.status == status)

        # Validate and apply severity filter
        if severity:
            valid_severities = [s.value for s in IncidentSeverity]
            if severity not in valid_severities:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid severity. Must be one of: {', '.join(valid_severities)}"
                )
            query = query.filter(Incident.severity == severity)

        # Apply source filter (no validation needed - can be any string)
        if source:
            query = query.filter(Incident.source == source)

        # Apply service filter (no validation needed - can be any string)
        if service:
            query = query.filter(Incident.service_name == service)

        incidents = query.order_by(Incident.last_seen_at.desc()).all()
        return incidents
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch incidents: {str(e)}"
        )

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
        from src.core.ai_analysis import analyze_incident_with_openrouter
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

    # Rate limiting: 5 analyses per user per hour
    rate_limit_key = f"analyze_incident:user:{user_id}"
    is_allowed, remaining = check_rate_limit(rate_limit_key, max_requests=5, window_seconds=3600)
    
    if not is_allowed:
        # Calculate human-readable time
        if remaining >= 3600:
            time_str = f"{remaining // 3600} hour(s)"
        elif remaining >= 60:
            time_str = f"{remaining // 60} minute(s)"
        else:
            time_str = f"{remaining} second(s)"
        
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum 5 analyses per hour. Try again in {time_str}."
        )

    # ALWAYS filter by user_id to prevent cross-user access
    incident = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.user_id == user_id
    ).first()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Track analytics
    try:
        # Log analysis request
        print(f"📊 Analytics: Analysis requested for incident {incident_id} by user {user_id}")
    except Exception as e:
        print(f"⚠️  Failed to log analytics: {e}")

    background_tasks.add_task(analyze_incident_async, incident_id, user_id)

    return {"status": "analysis_triggered", "message": "AI analysis started in background"}

async def analyze_incident_async(incident_id: int, user_id: Optional[int] = None):
    """Background task to analyze an incident."""
    from src.database.database import SessionLocal
    from src.core.ai_analysis import analyze_incident_with_openrouter
    
    db = SessionLocal()
    incident = None
    analysis_start_time = time.time()
    analysis_success = False
    analysis_error = None
    
    try:
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            print(f"❌ Incident {incident_id} not found for analysis")
            return
        
        # Fetch related logs
        logs = []
        if incident.log_ids:
            # Ensure log_ids is a list and not empty
            log_id_list = incident.log_ids if isinstance(incident.log_ids, list) else []
            if log_id_list:
                logs = db.query(LogEntry).filter(LogEntry.id.in_(log_id_list)).order_by(LogEntry.timestamp.desc()).all()
        
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
            changes = analysis.get("changes", {})
            original_contents = analysis.get("original_contents", {})
            
            # Ensure original_contents has entries for all changed files
            # If missing, set to empty string (new file) to prevent UI errors
            for file_path in changes.keys():
                if file_path not in original_contents:
                    original_contents[file_path] = ""
            
            incident.action_result = {
                "pr_url": analysis.get("pr_url"),
                "pr_number": analysis.get("pr_number"),
                "pr_files_changed": analysis.get("pr_files_changed", []),
                "changes": changes,
                "original_contents": original_contents,
                "is_draft": analysis.get("is_draft", False),
                "confidence_score": analysis.get("confidence_score"),
                "decision": analysis.get("decision", {}),
                "status": "pr_created_draft" if analysis.get("is_draft") else "pr_created"
            }
            pr_type = "DRAFT PR" if analysis.get("is_draft") else "PR"
            print(f"✅ {pr_type} created for incident {incident_id}: {analysis.get('pr_url')}")
        elif analysis.get("pr_error"):
            # Store PR error if creation failed
            incident.action_result = {
                "status": "pr_failed",
                "error": analysis.get("pr_error"),
                "code_fix_explanation": analysis.get("code_fix_explanation", f"Failed to create pull request: {analysis.get('pr_error')}")
            }
        elif analysis.get("changes"):
            # Store changes even if PR wasn't created (for UI display)
            changes = analysis.get("changes", {})
            original_contents = analysis.get("original_contents", {})
            
            # Ensure original_contents has entries for all changed files
            # If missing, set to empty string (new file) to prevent UI errors
            for file_path in changes.keys():
                if file_path not in original_contents:
                    original_contents[file_path] = ""
            
            incident.action_result = {
                "changes": changes,
                "original_contents": original_contents,
                "pr_files_changed": list(changes.keys()),  # Set file list for UI display
                "confidence_score": analysis.get("confidence_score"),
                "decision": analysis.get("decision", {}),
                "status": "changes_generated",
                "code_fix_explanation": analysis.get("code_fix_explanation")
            }
            print(f"📝 Changes generated for incident {incident_id} (no PR created)")
        
        # Store explanation if no code fixes were attempted
        elif analysis.get("code_fix_explanation"):
            incident.action_result = {
                "status": "no_code_fix",
                "code_fix_explanation": analysis.get("code_fix_explanation")
            }
        
        # Ensure we always set something to stop infinite loading
        if not incident.root_cause:
            incident.root_cause = "Analysis failed - no results returned. Please check logs."
        
        # Commit with error handling
        try:
            db.commit()
            analysis_success = True
        except Exception as commit_error:
            db.rollback()
            print(f"❌ Failed to commit analysis results for incident {incident_id}: {commit_error}")
            # Try to set error message and commit again
            try:
                incident.root_cause = f"Analysis completed but failed to save: {str(commit_error)[:200]}"
                db.commit()
            except Exception as retry_error:
                db.rollback()
                print(f"❌ Failed to save error message: {retry_error}")
        
        analysis_duration = time.time() - analysis_start_time
        if analysis_success:
            print(f"✅ AI analysis completed for incident {incident_id}: {incident.root_cause[:100]}")
        
        # Track successful analysis analytics
        try:
            print(f"📊 Analytics: Analysis succeeded for incident {incident_id}, duration: {analysis_duration:.2f}s, user: {user_id}")
            # In production, you would send this to your analytics service
            # Example: analytics.track('incident_analysis_success', {
            #     'incident_id': incident_id,
            #     'user_id': user_id,
            #     'duration_seconds': analysis_duration,
            #     'has_pr': bool(incident.action_result and incident.action_result.get('pr_url')),
            #     'is_draft': bool(incident.action_result and incident.action_result.get('is_draft'))
            # })
        except Exception as analytics_error:
            print(f"⚠️  Failed to track analytics: {analytics_error}")
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        analysis_error = str(e)
        analysis_duration = time.time() - analysis_start_time
        print(f"❌ Error analyzing incident {incident_id}: {e}")
        print(f"Full traceback: {error_trace}")
        
        # Track failed analysis analytics
        try:
            print(f"📊 Analytics: Analysis failed for incident {incident_id}, duration: {analysis_duration:.2f}s, error: {str(e)[:100]}, user: {user_id}")
            # In production, you would send this to your analytics service
            # Example: analytics.track('incident_analysis_failure', {
            #     'incident_id': incident_id,
            #     'user_id': user_id,
            #     'duration_seconds': analysis_duration,
            #     'error_type': type(e).__name__,
            #     'error_message': str(e)[:200]
            # })
        except Exception as analytics_error:
            print(f"⚠️  Failed to track analytics: {analytics_error}")
        
        # Set error message to stop infinite loading in UI
        try:
            if incident:
                incident.root_cause = f"Analysis error: {str(e)[:200]}. Please check server logs."
                try:
                    db.commit()
                    print(f"✅ Set error message for incident {incident_id}")
                except Exception as commit_error:
                    db.rollback()
                    print(f"❌ Failed to commit error message: {commit_error}")
            else:
                # Try to get incident again if we lost the reference
                try:
                    incident = db.query(Incident).filter(Incident.id == incident_id).first()
                    if incident:
                        incident.root_cause = f"Analysis error: {str(e)[:200]}. Please check server logs."
                        try:
                            db.commit()
                            print(f"✅ Set error message for incident {incident_id}")
                        except Exception as commit_error:
                            db.rollback()
                            print(f"❌ Failed to commit error message: {commit_error}")
                except Exception as query_error:
                    print(f"❌ Failed to query incident: {query_error}")
        except Exception as update_error:
            print(f"❌ Failed to update incident with error message: {update_error}")
            try:
                db.rollback()
            except Exception:
                pass  # Ignore rollback errors if connection is already closed
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
            from src.services.email.service import send_incident_resolved_email
            from src.database.models import User
            
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

@app.post("/incidents/{incident_id}/test-agent")
async def test_agent_endpoint(
    incident_id: int, 
    request: Request, 
    db: Session = Depends(get_db)
):
    """
    Test endpoint to run the agent synchronously and see detailed thinking process.
    
    This endpoint runs the agent directly and returns all events, steps, and thinking
    in the response. Useful for debugging and understanding agent behavior.
    
    NOTE: This endpoint does NOT require authentication - it's for testing only.
    It will work with any incident ID without checking user permissions.
    
    Args:
        incident_id: ID of the incident to test
    
    Returns:
        Detailed response with agent execution, events, thinking, and results
    """
    # No authentication required for testing endpoint
    # Get incident without user filtering
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    
    if not incident:
        raise HTTPException(
            status_code=404, 
            detail=f"Incident {incident_id} not found"
        )
    
    print(f"\n{'='*60}")
    print(f"🧪 TEST AGENT ENDPOINT - Incident {incident_id}")
    print(f"{'='*60}")
    print(f"Incident: {incident.title}")
    print(f"Status: {incident.status}")
    print(f"Root cause: {incident.root_cause[:100] if incident.root_cause else 'Not set'}")
    print(f"{'='*60}\n")
    
    try:
        # Get logs
        logs = []
        if incident.log_ids:
            log_id_list = incident.log_ids if isinstance(incident.log_ids, list) else []
            if log_id_list:
                logs = db.query(LogEntry).filter(LogEntry.id.in_(log_id_list)).order_by(LogEntry.timestamp.desc()).all()
        
        # Get GitHub integration
        github_integration = None
        if incident.integration_id:
            try:
                print(f"🔧 Loading GitHub integration (ID: {incident.integration_id})...")
                github_integration = GithubIntegration(integration_id=incident.integration_id)
                print(f"✅ GitHub integration loaded (ID: {incident.integration_id})")
                
                # Verify connection
                if github_integration.client:
                    verification = github_integration.verify_connection()
                    if verification.get("status") == "verified":
                        print(f"✅ GitHub connection verified: {verification.get('username', 'N/A')}")
                    else:
                        print(f"⚠️  GitHub connection verification failed: {verification.get('message', 'Unknown error')}")
                else:
                    print(f"⚠️  GitHub client not initialized after loading integration")
            except Exception as e:
                print(f"⚠️  Warning: Failed to load GitHub integration: {e}")
                import traceback
                traceback.print_exc()
        
        repo_name = incident.repo_name or "owner/repo"
        root_cause = incident.root_cause or "Test root cause - agent testing"
        
        if not incident.root_cause:
            print(f"⚠️  Root cause not set, using placeholder: {root_cause}")
        
        # Import agent orchestrator
        from src.agents.orchestrator import run_robust_crew
        
        print(f"\n🚀 Starting agent execution...")
        print(f"   Repository: {repo_name}")
        print(f"   Logs: {len(logs)} entries")
        print(f"   Root cause: {root_cause[:100]}\n")
        
        # Run agent synchronously
        start_time = time.time()
        result = run_robust_crew(
            incident=incident,
            logs=logs,
            root_cause=root_cause,
            github_integration=github_integration,
            repo_name=repo_name,
            db=db
        )
        execution_time = time.time() - start_time
        
        print(f"\n✅ Agent execution completed in {execution_time:.2f}s")
        
        # Format response with detailed information
        response = {
            "success": result.get("success", False),
            "status": result.get("status", "unknown"),
            "execution_time_seconds": round(execution_time, 2),
            "incident_id": incident_id,
            "incident_info": {
                "title": incident.title,
                "status": incident.status,
                "severity": incident.severity,
                "service_name": incident.service_name,
                "root_cause": incident.root_cause,
                "repo_name": repo_name,
                "has_integration": github_integration is not None
            },
            "agent_execution": {
                "iterations": result.get("iterations", 0),
                "plan_progress": result.get("plan_progress", {}),
                "workspace_state": result.get("workspace_state", {})
            },
            "events": result.get("events", []),
            "fixes": result.get("fixes", {}),
            "error_signature": result.get("error_signature"),
            "thinking_summary": _extract_thinking_summary(result.get("events", [])),
            "steps_taken": _extract_steps_taken(result.get("events", [])),
            "error": result.get("error")
        }
        
        return response
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"\n❌ Error in test agent endpoint: {e}")
        print(f"Full traceback:\n{error_trace}")
        
        return {
            "success": False,
            "status": "error",
            "incident_id": incident_id,
            "error": str(e),
            "error_trace": error_trace,
            "message": "Agent execution failed. Check error details."
        }


def _extract_thinking_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract a summary of agent thinking from events."""
    thinking = {
        "total_events": len(events),
        "event_types": {},
        "agent_actions": [],
        "observations": [],
        "plan_changes": [],
        "errors": []
    }
    
    for event in events:
        event_type = event.get("type", "unknown")
        thinking["event_types"][event_type] = thinking["event_types"].get(event_type, 0) + 1
        
        data = event.get("data", {})
        
        if event_type == "agent_action":
            thinking["agent_actions"].append({
                "agent": event.get("agent"),
                "action": data.get("action", ""),
                "timestamp": event.get("timestamp")
            })
        elif event_type == "observation":
            thinking["observations"].append({
                "observation": data.get("observation", "")[:200],
                "timestamp": event.get("timestamp")
            })
        elif event_type in ["plan_created", "plan_updated"]:
            thinking["plan_changes"].append({
                "type": event_type,
                "steps_count": len(data.get("plan", [])),
                "timestamp": event.get("timestamp")
            })
        elif event_type == "error":
            thinking["errors"].append({
                "message": data.get("message", ""),
                "timestamp": event.get("timestamp")
            })
    
    return thinking


def _extract_steps_taken(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract chronological steps taken by the agent."""
    steps = []
    
    for event in events:
        event_type = event.get("type", "unknown")
        data = event.get("data", {})
        
        if event_type == "plan_step_started":
            steps.append({
                "step_number": data.get("step_number"),
                "description": data.get("description", ""),
                "status": "started",
                "timestamp": event.get("timestamp")
            })
        elif event_type == "plan_step_completed":
            # Update existing step or add new
            step_found = False
            for step in steps:
                if step.get("step_number") == data.get("step_number"):
                    step["status"] = "completed"
                    step["result"] = data.get("result", "")
                    step["completed_at"] = event.get("timestamp")
                    step_found = True
                    break
            if not step_found:
                steps.append({
                    "step_number": data.get("step_number"),
                    "description": data.get("description", ""),
                    "status": "completed",
                    "result": data.get("result", ""),
                    "timestamp": event.get("timestamp")
                })
        elif event_type == "plan_step_failed":
            # Update existing step or add new
            step_found = False
            for step in steps:
                if step.get("step_number") == data.get("step_number"):
                    step["status"] = "failed"
                    step["error"] = data.get("error", "")
                    step["failed_at"] = event.get("timestamp")
                    step_found = True
                    break
            if not step_found:
                steps.append({
                    "step_number": data.get("step_number"),
                    "description": data.get("description", ""),
                    "status": "failed",
                    "error": data.get("error", ""),
                    "timestamp": event.get("timestamp")
                })
    
    return steps
