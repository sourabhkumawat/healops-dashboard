from dotenv import load_dotenv
load_dotenv()

# Add src directory to Python path for imports
import sys
from pathlib import Path
_engine_root = Path(__file__).parent
if str(_engine_root) not in sys.path:
    sys.path.insert(0, str(_engine_root))
import os
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
from src.api.controllers.base import get_user_id_from_request
from src.api.controllers.auth_controller import AuthController, get_current_user, UserUpdateRequest, TestEmailRequest
from src.api.controllers.slack_controller import SlackController
from src.api.controllers.logs_controller import LogsController, LogIngestRequest, LogBatchRequest, OTelSpanEvent, OTelSpanStatus, OTelSpan, OTelErrorPayload
from src.api.controllers.api_keys_controller import APIKeysController, ApiKeyRequest
from src.api.controllers.sourcemaps_controller import SourceMapsController, SourceMapFile, SourceMapUploadRequest
from src.api.controllers.services_controller import ServicesController
from src.api.controllers.stats_controller import StatsController
from src.api.controllers.incidents_controller import IncidentsController
from src.api.controllers.integrations_controller import IntegrationsController, GithubConfig, ServiceMappingRequest, ServiceMappingsUpdateRequest
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


@app.get("/")
def read_root():
    return {"status": "online", "service": "engine"}

# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.post("/auth/register")
def register(user_data: dict, db: Session = Depends(get_db)):
    """Register user - delegates to AuthController."""
    return AuthController.register(user_data, db)

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Login - delegates to AuthController."""
    return AuthController.login(form_data, db)

@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    """Get current user - delegates to AuthController."""
    return AuthController.get_me(current_user)
@app.put("/auth/me")
def update_me(update: UserUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update user - delegates to AuthController."""
    return AuthController.update_me(update, current_user, db)


@app.post("/auth/test-email")
def test_email_endpoint(request_data: TestEmailRequest, current_user: User = Depends(get_current_user)):
    """Test email - delegates to AuthController."""
    return AuthController.test_email(request_data)
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

def get_bot_token_for_agent(agent_name: str, agent_role: str = None, agent_stored_token: str = None) -> Optional[str]:
    """
    Get the appropriate Slack bot token for an agent.
    
    Priority:
    1. Agent's stored token (decrypted if encrypted)
    2. Agent-specific environment variable (SLACK_BOT_TOKEN_MORGAN, SLACK_BOT_TOKEN_ALEX)
    3. Generic SLACK_BOT_TOKEN environment variable
    
    Args:
        agent_name: Agent's name (e.g., "Morgan Taylor", "Alexandra Chen")
        agent_role: Agent's role (e.g., "QA Engineer", "Senior Software Engineer")
        agent_stored_token: Encrypted token stored in database (optional)
    
    Returns:
        Bot token string or None if not found
    """
    # Try agent's stored token first (if provided)
    if agent_stored_token:
        try:
            from src.auth.crypto_utils import decrypt_token
            decrypted = decrypt_token(agent_stored_token)
            if decrypted:
                return decrypted
        except Exception as e:
            print(f"⚠️  Failed to decrypt stored token: {e}")
    
    # Try agent-specific environment variable
    agent_token_var = None
    agent_name_lower = agent_name.lower() if agent_name else ""
    agent_role_lower = agent_role.lower() if agent_role else ""
    
    if "alex" in agent_name_lower or "alexandra" in agent_name_lower:
        agent_token_var = os.getenv("SLACK_BOT_TOKEN_ALEX")
    elif "morgan" in agent_name_lower or "qa" in agent_role_lower:
        agent_token_var = os.getenv("SLACK_BOT_TOKEN_MORGAN")
    
    if agent_token_var:
        return agent_token_var
    
    # Fallback to generic token
    return os.getenv("SLACK_BOT_TOKEN")


@app.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack Events API - delegates to SlackController."""
    return await SlackController.handle_events(request)


@app.post("/slack/interactive")
async def slack_interactive(request: Request):
    """Handle Slack Interactive Components - delegates to SlackController."""
    return await SlackController.handle_interactive(request)
async def handle_slack_mention(event: Dict[str, Any], team_id: Optional[str], used_secret_name: Optional[str] = None):
    """
    Handle when the bot is mentioned in a channel.
    
    Args:
        event: Slack event data
        team_id: Slack workspace team ID
        used_secret_name: Which signing secret was used (identifies which bot received the event)
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
        
        # Note: We can't determine which specific bot this is from until we match the agent
        # The subtype check above should catch most bot messages
        # We'll do a more thorough check after agent matching below
        channel_id = event.get("channel")
        
        print(f"📩 Slack mention received from user {user_id}: {text[:100]}...")
        
        # Parse mention to extract agent name and query
        # Format: "@healops-agent @alexandra.chen what are you working on?"
        # Or: "@healops-agent ask Morgan what are you working on?"
        agent_name_match = None
        
        # Extract Slack user mentions BEFORE cleaning to match by display name
        import re
        mentioned_user_ids = []
        mentioned_display_names = []
        # Extract Slack user mentions: <@U123456> or <@U123456|Display Name>
        mention_pattern = r'<@([A-Z0-9]+)(?:\|([^>]+))?>'
        for match in re.finditer(mention_pattern, text):
            mentioned_user_id = match.group(1)  # Don't overwrite user_id (the actual message sender)
            display_name = match.group(2) if match.group(2) else None
            mentioned_user_ids.append(mentioned_user_id)
            if display_name:
                mentioned_display_names.append(display_name.lower())
        
        # Clean text: Remove Slack user mention formatting like <@U123456|displayname>
        # Remove Slack user mentions: <@U123456> or <@U123456|Display Name>
        cleaned_text = re.sub(r'<@[A-Z0-9]+\|?[^>]*>', '', text)
        # Remove extra whitespace
        cleaned_text = ' '.join(cleaned_text.split())
        query = cleaned_text.lower()
        original_text_lower = text.lower()  # Keep original for matching
        
        print(f"🔍 Query after cleaning: {query[:100]}...")
        if mentioned_display_names:
            print(f"🔍 Found Slack mentions with display names: {mentioned_display_names}")
        
        # Find agent mentions in text (format: @agent-name or agent name)
        db = SessionLocal()
        try:
            # Get all agent employees for this channel
            agents = db.query(AgentEmployee).filter(
                AgentEmployee.slack_channel_id == channel_id
            ).all()
            
            print(f"🔍 Found {len(agents)} agent(s) for channel {channel_id}")
            for idx, agent in enumerate(agents):
                slack_id_display = f"{agent.slack_user_id[:10]}..." if agent.slack_user_id else "None"
                print(f"   Agent {idx}: {agent.name} (role: {agent.role}, slack_user_id: {slack_id_display})")
            
            # Also log mentioned user IDs for debugging
            if mentioned_user_ids:
                print(f"🔍 Looking for agent with slack_user_id matching: {mentioned_user_ids}")
            
            if not agents:
                # Try to find agents without channel filter (in case channel_id format is different)
                all_agents = db.query(AgentEmployee).all()
                print(f"⚠️  No agents found for channel {channel_id}, but {len(all_agents)} agent(s) exist total")
                print(f"   Channel IDs in DB: {[a.slack_channel_id for a in all_agents if a.slack_channel_id]}")
                agents = all_agents  # Use all agents as fallback
                for idx, agent in enumerate(agents):
                    print(f"   Agent {idx}: {agent.name} (role: {agent.role}, slack_user_id: {agent.slack_user_id[:10] if agent.slack_user_id else 'None'}...)")
            
            # First, try to match by Slack user ID (most reliable)
            # IMPORTANT: Check ALL agents, not just those for this channel, because
            # the mentioned agent might be in a different channel or have no channel set
            if mentioned_user_ids:
                print(f"🔍 Attempting to match by user IDs: {mentioned_user_ids}")
                # First check agents in channel
                for agent in agents:
                    if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                        agent_name_match = agent
                        print(f"✅ Matched agent by Slack user ID (in channel): {agent.name} (ID: {agent.slack_user_id})")
                        break
                
                # If not found in channel agents, check ALL agents
                if not agent_name_match:
                    print(f"   Not found in channel agents, checking all agents...")
                    all_agents = db.query(AgentEmployee).all()
                    print(f"   Checking {len(all_agents)} total agent(s):")
                    for agent in all_agents:
                        print(f"      - {agent.name}: slack_user_id={agent.slack_user_id if agent.slack_user_id else 'None'}")
                        if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                            agent_name_match = agent
                            print(f"✅ Matched agent by Slack user ID (all agents): {agent.name} (ID: {agent.slack_user_id})")
                            print(f"   Note: Agent {agent.name} is not in channel {channel_id} but was mentioned by user ID")
                            break
                    
                    # If still no match and we have mentioned user IDs, try to resolve by fetching bot_user_id from Slack
                    if not agent_name_match and mentioned_user_ids:
                        print(f"   ⚠️  No match found. Attempting to resolve user ID {mentioned_user_ids[0]} by checking agents' bot tokens...")
                        for agent in all_agents:
                            # Skip if agent's slack_user_id already matches (we already checked above)
                            if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                                continue
                            
                            # Try to get bot token and fetch bot_user_id from Slack
                            # This works even if agent doesn't have token in DB - get_bot_token_for_agent checks env vars
                            try:
                                from src.auth.crypto_utils import decrypt_token
                                bot_token = get_bot_token_for_agent(
                                    agent_name=agent.name,
                                    agent_role=agent.role,
                                    agent_stored_token=agent.slack_bot_token
                                )
                                if bot_token:
                                    from src.services.slack.service import SlackService
                                    slack_service = SlackService(bot_token)
                                    auth_test = slack_service.test_connection()
                                    if auth_test.get("ok"):
                                        bot_user_id = auth_test.get("user_id")
                                        print(f"      📡 Fetched bot_user_id for {agent.name}: {bot_user_id}")
                                        
                                        # Update agent's slack_user_id in database
                                        if bot_user_id:
                                            agent.slack_user_id = bot_user_id
                                            # Also update channel_id if not set and we have one
                                            if not agent.slack_channel_id and channel_id:
                                                agent.slack_channel_id = channel_id
                                                print(f"      💾 Updated {agent.name}'s slack_channel_id to {channel_id}")
                                            db.commit()
                                            print(f"      💾 Updated {agent.name}'s slack_user_id to {bot_user_id}")
                                        
                                        # Check if this matches the mentioned user ID
                                        if bot_user_id in mentioned_user_ids:
                                            agent_name_match = agent
                                            print(f"✅ Matched agent by resolving bot_user_id: {agent.name} (ID: {bot_user_id})")
                                            break
                            except Exception as e:
                                print(f"      ⚠️  Could not resolve bot_user_id for {agent.name}: {e}")
                                continue
            
            # If no match by user ID, try to match by display name from Slack mention
            if not agent_name_match and mentioned_display_names:
                print(f"🔍 Attempting to match by display names: {mentioned_display_names}")
                # First check agents in channel
                for agent in agents:
                    if not agent.name:
                        continue
                    agent_full_name = agent.name.lower()
                    agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                    
                    print(f"   Checking agent: {agent.name} (full: '{agent_full_name}', first: '{agent_first_name}')")
                    
                    # Check if any mentioned display name matches the agent's name
                    for display_name in mentioned_display_names:
                        print(f"      Comparing display_name '{display_name}' with agent '{agent.name}'")
                        # Exact match on full name (highest priority)
                        if display_name == agent_full_name:
                            agent_name_match = agent
                            print(f"✅ Matched agent by exact display name: {agent.name} (display: '{display_name}')")
                            break
                        # Match on first name (e.g., "morgan" matches "Morgan Taylor")
                        elif agent_first_name and display_name == agent_first_name:
                            agent_name_match = agent
                            print(f"✅ Matched agent by first name from display: {agent.name} (display: '{display_name}')")
                            break
                        # Partial match (e.g., "morgan taylor" in display name)
                        elif agent_full_name in display_name or display_name in agent_full_name:
                            agent_name_match = agent
                            print(f"✅ Matched agent by partial display name: {agent.name} (display: '{display_name}')")
                            break
                    
                    if agent_name_match:
                        break
            
            # If still no match, try to match from text content (prioritize exact matches)
            if not agent_name_match:
                print(f"🔍 No match by user ID or display name, trying text content matching")
                print(f"   Original text (lower): {original_text_lower[:200]}")
                # Build match scores for each agent (higher score = better match)
                agent_scores = []
                
                for agent in agents:
                    if not agent.name:
                        continue
                    
                    score = 0
                    matched_pattern = None
                    
                    # Check for agent name in mention
                    agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                    agent_last_name = agent.name.split()[-1].lower() if len(agent.name.split()) > 1 else ""
                    agent_full_name = agent.name.lower()
                    
                    # Priority 1: Exact full name match (highest priority)
                    if agent_full_name in original_text_lower:
                        # Check for word boundaries to avoid partial matches
                        full_name_pattern = r'\b' + re.escape(agent_full_name) + r'\b'
                        if re.search(full_name_pattern, original_text_lower):
                            score = 100
                            matched_pattern = f"exact full name: '{agent_full_name}'"
                    
                    # Priority 2: Full name with "morgan taylor" format (for Morgan)
                    if score == 0 and agent_first_name == "morgan" and "morgan taylor" in original_text_lower:
                        score = 90
                        matched_pattern = "full name format: 'morgan taylor'"
                    
                    # Priority 3: First name match (but lower priority to avoid false matches)
                    if score == 0:
                        first_name_pattern = r'\b' + re.escape(agent_first_name) + r'\b'
                        if re.search(first_name_pattern, original_text_lower):
                            # Check if it's not a substring of another word
                            score = 50
                            matched_pattern = f"first name: '{agent_first_name}'"
                    
                    # Priority 4: Nickname matches (for Alex)
                    if score == 0 and agent_first_name == "alexandra":
                        if re.search(r'\balex\b', original_text_lower):
                            score = 60
                            matched_pattern = "nickname: 'alex'"
                    
                    # Priority 5: Role keywords (lowest priority)
                    if score == 0 and agent.role:
                        role_lower = agent.role.lower()
                        if "qa" in role_lower and "qa" in original_text_lower:
                            score = 20
                            matched_pattern = "role keyword: 'qa'"
                        elif "engineer" in role_lower and "engineer" in original_text_lower:
                            score = 10
                            matched_pattern = "role keyword: 'engineer'"
                    
                    if score > 0:
                        agent_scores.append((score, agent, matched_pattern))
                        print(f"   Agent {agent.name} scored {score} (pattern: {matched_pattern})")
                    else:
                        print(f"   Agent {agent.name} scored 0 (no match)")
                
                # Sort by score (highest first) and pick the best match
                if agent_scores:
                    print(f"🔍 Found {len(agent_scores)} agent(s) with matches, scores: {[f'{a[1].name}:{a[0]}' for a in agent_scores]}")
                    agent_scores.sort(key=lambda x: x[0], reverse=True)
                    best_score, agent_name_match, matched_pattern = agent_scores[0]
                    print(f"✅ Matched agent by text: {agent_name_match.name} (score: {best_score}, pattern: {matched_pattern})")
                    
                    # If agent doesn't have slack_user_id but we have a mentioned user ID, store it
                    if not agent_name_match.slack_user_id and mentioned_user_ids:
                        agent_name_match.slack_user_id = mentioned_user_ids[0]
                        print(f"      💾 Stored mentioned user ID {mentioned_user_ids[0]} for {agent_name_match.name}")
                    # Also update channel_id if not set
                    if not agent_name_match.slack_channel_id and channel_id:
                        agent_name_match.slack_channel_id = channel_id
                        print(f"      💾 Stored channel_id {channel_id} for {agent_name_match.name}")
                    if (not agent_name_match.slack_user_id and mentioned_user_ids) or (not agent_name_match.slack_channel_id and channel_id):
                        try:
                            db.commit()
                        except Exception as e:
                            print(f"      ⚠️  Could not save agent updates: {e}")
                    
                    # If there's a tie or close scores, prefer exact matches
                    if len(agent_scores) > 1 and agent_scores[1][0] >= best_score * 0.8:
                        # Check if we have an exact full name match
                        exact_matches = [a for a in agent_scores if a[0] >= 90]
                        if exact_matches:
                            agent_name_match = exact_matches[0][1]
                            print(f"✅ Preferring exact match: {agent_name_match.name}")
                else:
                    # No matches found in text - log why
                    print(f"⚠️  No agent matches found in text content")
                    print(f"   Original text: {text[:200]}")
                    print(f"   Original text (lower): {original_text_lower[:200]}")
                    print(f"   Mentioned user IDs: {mentioned_user_ids}")
                    print(f"   Mentioned display names: {mentioned_display_names}")
                    print(f"   Available agents: {[a.name for a in agents]}")
            
            # Only use fallback to first agent if NO mentions were detected at all
            # If mentions were detected but didn't match, that's an error - don't default
            if not agent_name_match:
                if mentioned_user_ids or mentioned_display_names:
                    # We detected mentions but couldn't match them - this is an error
                    print(f"❌ ERROR: Detected agent mentions but couldn't match to any agent!")
                    print(f"   Mentioned user IDs: {mentioned_user_ids}")
                    print(f"   Mentioned display names: {mentioned_display_names}")
                    print(f"   Available agents: {[(a.name, a.slack_user_id) for a in agents]}")
                    print(f"   This message will NOT be processed to avoid wrong agent responding")
                    return  # Don't process - avoid wrong agent responding
                elif agents:
                    # No mentions detected at all - safe to use first agent as fallback
                    agent_name_match = agents[0]
                    print(f"✅ No agent mentioned, using first agent in channel: {agent_name_match.name}")
            
            if not agent_name_match:
                print(f"⚠️  No agent found for channel {channel_id}")
                print(f"   Query text: {query}")
                print(f"   Channel ID received: {channel_id}")
                
                # Only use fallback if NO mentions were detected
                # If mentions were detected but didn't match, that's an error
                if mentioned_user_ids or mentioned_display_names:
                    print(f"❌ ERROR: Detected agent mentions but couldn't match to any agent!")
                    print(f"   Mentioned user IDs: {mentioned_user_ids}")
                    print(f"   Mentioned display names: {mentioned_display_names}")
                    print(f"   This message will NOT be processed to avoid wrong agent responding")
                    return  # Don't process - avoid wrong agent responding
                
                # Try to find any agent (fallback if channel_id doesn't match AND no mentions detected)
                all_agents = db.query(AgentEmployee).all()
                if all_agents:
                    print(f"   Attempting to use first available agent from {len(all_agents)} total agents (no mentions detected)")
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
            # Get bot token using helper function (checks agent-specific tokens)
            bot_token = get_bot_token_for_agent(
                agent_name=agent_name_match.name,
                agent_role=agent_name_match.role,
                agent_stored_token=agent_name_match.slack_bot_token
            )
            
            if bot_token:
                # Determine token source for logging
                agent_name_lower = agent_name_match.name.lower()
                agent_role_lower = agent_name_match.role.lower() if agent_name_match.role else ""
                if agent_name_match.slack_bot_token:
                    token_source = "stored (encrypted)"
                elif ("morgan" in agent_name_lower or "qa" in agent_role_lower) and os.getenv("SLACK_BOT_TOKEN_MORGAN"):
                    token_source = "SLACK_BOT_TOKEN_MORGAN"
                elif ("alex" in agent_name_lower or "alexandra" in agent_name_lower) and os.getenv("SLACK_BOT_TOKEN_ALEX"):
                    token_source = "SLACK_BOT_TOKEN_ALEX"
                else:
                    token_source = "SLACK_BOT_TOKEN"
                print(f"✅ Retrieved bot token ({token_source})")
            else:
                print("⚠️  No Slack bot token available")
                print(f"   Agent: {agent_name_match.name} ({agent_name_match.role})")
                print(f"   Agent has stored token: {'YES' if agent_name_match.slack_bot_token else 'NO'}")
                
                # Check which tokens are set
                agent_name_lower = agent_name_match.name.lower()
                agent_role_lower = agent_name_match.role.lower() if agent_name_match.role else ""
                if "morgan" in agent_name_lower or "qa" in agent_role_lower:
                    print(f"   Environment variable SLACK_BOT_TOKEN_MORGAN: {'SET' if os.getenv('SLACK_BOT_TOKEN_MORGAN') else 'NOT SET'}")
                elif "alex" in agent_name_lower or "alexandra" in agent_name_lower:
                    print(f"   Environment variable SLACK_BOT_TOKEN_ALEX: {'SET' if os.getenv('SLACK_BOT_TOKEN_ALEX') else 'NOT SET'}")
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
            
            # Get the bot user ID for this specific agent's bot
            # Each agent has their own bot, so we need to use the correct bot user ID
            bot_user_id = agent_name_match.slack_user_id
            if not bot_user_id:
                # If not stored in DB, get it from the SlackService (which uses the agent's token)
                bot_user_id = slack_service.bot_user_id
                # Store it in the database for future use
                if bot_user_id:
                    try:
                        agent_name_match.slack_user_id = bot_user_id
                        db.commit()
                        print(f"💾 Stored bot user ID {bot_user_id} for {agent_name_match.name}")
                    except Exception as e:
                        print(f"⚠️  Could not store bot user ID: {e}")
            
            # CRITICAL: If a specific agent was mentioned by user ID, verify this bot is the correct one
            # This prevents Alex's bot from responding when Morgan is mentioned (and vice versa)
            if mentioned_user_ids and used_secret_name:
                # Determine which bot received this event based on the signing secret used
                # Get this bot's actual user ID (not the matched agent's token user ID)
                receiving_bot_user_id = None
                if "ALEX" in used_secret_name.upper():
                    # This event was received by Alex's bot - get Alex's bot user ID
                    alex_token = os.getenv("SLACK_BOT_TOKEN_ALEX")
                    if alex_token:
                        alex_service = SlackService(alex_token)
                        receiving_bot_user_id = alex_service.bot_user_id
                elif "MORGAN" in used_secret_name.upper():
                    # This event was received by Morgan's bot - get Morgan's bot user ID
                    morgan_token = os.getenv("SLACK_BOT_TOKEN_MORGAN")
                    if morgan_token:
                        morgan_service = SlackService(morgan_token)
                        receiving_bot_user_id = morgan_service.bot_user_id
                
                # If we know which bot received this, verify it's the one that was mentioned
                if receiving_bot_user_id:
                    if receiving_bot_user_id not in mentioned_user_ids:
                        # This bot was NOT the one mentioned - another bot should handle this
                        print(f"⏭️  Skipping: This bot ({used_secret_name}, user_id: {receiving_bot_user_id}) was not mentioned. Mentioned IDs: {mentioned_user_ids}")
                        print(f"   Another bot (the one with ID matching {mentioned_user_ids}) should respond instead")
                        return
            
            # Check if message is from this specific bot (prevent recursive responses)
            if bot_user_id and user_id == bot_user_id:
                print(f"⏭️  Skipping message from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id} for {agent_name_match.name})")
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


async def handle_slack_dm(event: Dict[str, Any], team_id: Optional[str], used_secret_name: Optional[str] = None):
    """
    Handle direct messages to the bot.
    
    Args:
        event: Slack event data
        team_id: Slack workspace team ID
        used_secret_name: Which signing secret was used (identifies which bot received the event)
    """
    try:
        # Similar to handle_slack_mention but for DMs
        # For now, redirect to mention handler
        await handle_slack_mention(event, team_id, used_secret_name=used_secret_name)
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
            
            # Try to match agent name from text (similar to mention handler)
            # Extract Slack user mentions BEFORE cleaning to match by display name
            import re
            mentioned_user_ids = []
            mentioned_display_names = []
            # Extract Slack user mentions: <@U123456> or <@U123456|Display Name>
            mention_pattern = r'<@([A-Z0-9]+)(?:\|([^>]+))?>'
            for match in re.finditer(mention_pattern, text):
                mentioned_user_id = match.group(1)  # Don't overwrite user_id (the actual message sender)
                display_name = match.group(2) if match.group(2) else None
                mentioned_user_ids.append(mentioned_user_id)
                if display_name:
                    mentioned_display_names.append(display_name.lower())
            
            text_lower = text.lower()
            agent = None
            
            # First, try to match by Slack user ID (most reliable)
            # IMPORTANT: Check ALL agents, not just those for this channel, because
            # the mentioned agent might be in a different channel or have no channel set
            if mentioned_user_ids:
                print(f"🔍 Attempting to match by user IDs: {mentioned_user_ids}")
                # First check agents in channel
                for candidate_agent in agents:
                    if candidate_agent.slack_user_id and candidate_agent.slack_user_id in mentioned_user_ids:
                        agent = candidate_agent
                        print(f"✅ Thread reply matched agent by Slack user ID (in channel): {agent.name} (ID: {candidate_agent.slack_user_id})")
                        break
                
                # If not found in channel agents, check ALL agents
                if not agent:
                    print(f"   Not found in channel agents, checking all agents...")
                    all_agents = db.query(AgentEmployee).all()
                    print(f"   Checking {len(all_agents)} total agent(s):")
                    for candidate_agent in all_agents:
                        print(f"      - {candidate_agent.name}: slack_user_id={candidate_agent.slack_user_id if candidate_agent.slack_user_id else 'None'}")
                        if candidate_agent.slack_user_id and candidate_agent.slack_user_id in mentioned_user_ids:
                            agent = candidate_agent
                            print(f"✅ Thread reply matched agent by Slack user ID (all agents): {agent.name} (ID: {candidate_agent.slack_user_id})")
                            print(f"   Note: Agent {agent.name} is not in channel {channel_id} but was mentioned by user ID")
                            break
                    
                    # If still no match and we have mentioned user IDs, try to resolve by fetching bot_user_id from Slack
                    if not agent and mentioned_user_ids:
                        print(f"   ⚠️  No match found. Attempting to resolve user ID {mentioned_user_ids[0]} by checking agents' bot tokens...")
                        for candidate_agent in all_agents:
                            # Skip if agent already has a slack_user_id that doesn't match
                            if candidate_agent.slack_user_id:
                                continue
                            
                            # Try to get bot token and fetch bot_user_id from Slack
                            try:
                                from src.auth.crypto_utils import decrypt_token
                                bot_token = get_bot_token_for_agent(
                                    agent_name=candidate_agent.name,
                                    agent_role=candidate_agent.role,
                                    agent_stored_token=candidate_agent.slack_bot_token
                                )
                                if bot_token:
                                    from src.services.slack.service import SlackService
                                    slack_service = SlackService(bot_token)
                                    auth_test = slack_service.test_connection()
                                    if auth_test.get("ok"):
                                        bot_user_id = auth_test.get("user_id")
                                        print(f"      📡 Fetched bot_user_id for {candidate_agent.name}: {bot_user_id}")
                                        
                                        # Update agent's slack_user_id in database
                                        if bot_user_id:
                                            candidate_agent.slack_user_id = bot_user_id
                                            db.commit()
                                            print(f"      💾 Updated {candidate_agent.name}'s slack_user_id to {bot_user_id}")
                                        
                                        # Check if this matches the mentioned user ID
                                        if bot_user_id in mentioned_user_ids:
                                            agent = candidate_agent
                                            print(f"✅ Thread reply matched agent by resolving bot_user_id: {agent.name} (ID: {bot_user_id})")
                                            break
                            except Exception as e:
                                print(f"      ⚠️  Could not resolve bot_user_id for {candidate_agent.name}: {e}")
                                continue
            
            # If no match by user ID, try to match by display name from Slack mention
            if not agent and mentioned_display_names:
                print(f"🔍 Attempting to match by display names: {mentioned_display_names}")
                # First check agents in channel
                for candidate_agent in agents:
                    if not candidate_agent.name:
                        continue
                    agent_full_name = candidate_agent.name.lower()
                    agent_first_name = candidate_agent.name.split()[0].lower() if candidate_agent.name else ""
                    
                    print(f"   Checking agent: {candidate_agent.name} (full: '{agent_full_name}', first: '{agent_first_name}')")
                    
                    # Check if any mentioned display name matches the agent's name
                    for display_name in mentioned_display_names:
                        print(f"      Comparing display_name '{display_name}' with agent '{candidate_agent.name}'")
                        # Exact match on full name (highest priority)
                        if display_name == agent_full_name:
                            agent = candidate_agent
                            print(f"✅ Thread reply matched agent by exact display name: {agent.name} (display: '{display_name}')")
                            break
                        # Match on first name (e.g., "morgan" matches "Morgan Taylor")
                        elif agent_first_name and display_name == agent_first_name:
                            agent = candidate_agent
                            print(f"✅ Thread reply matched agent by first name from display: {agent.name} (display: '{display_name}')")
                            break
                        # Partial match (e.g., "morgan taylor" in display name)
                        elif agent_full_name in display_name or display_name in agent_full_name:
                            agent = candidate_agent
                            print(f"✅ Thread reply matched agent by partial display name: {agent.name} (display: '{display_name}')")
                            break
                    
                    if agent:
                        break
                
                # If not found in channel agents, check ALL agents
                if not agent:
                    print(f"   Not found in channel agents, checking all agents for display name match...")
                    all_agents = db.query(AgentEmployee).all()
                    for candidate_agent in all_agents:
                        if not candidate_agent.name:
                            continue
                        agent_full_name = candidate_agent.name.lower()
                        agent_first_name = candidate_agent.name.split()[0].lower() if candidate_agent.name else ""
                        
                        for display_name in mentioned_display_names:
                            if display_name == agent_full_name:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by exact display name (all agents): {agent.name} (display: '{display_name}')")
                                break
                            elif agent_first_name and display_name == agent_first_name:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by first name from display (all agents): {agent.name} (display: '{display_name}')")
                                break
                            elif agent_full_name in display_name or display_name in agent_full_name:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by partial display name (all agents): {agent.name} (display: '{display_name}')")
                                break
                        
                        if agent:
                            break
            
            # If still no match, try to match from text content (prioritize exact matches)
            if not agent:
                # Build match scores for each agent (higher score = better match)
                agent_scores = []
                
                for candidate_agent in agents:
                    if not candidate_agent.name:
                        continue
                    
                    score = 0
                    matched_pattern = None
                    
                    agent_first_name = candidate_agent.name.split()[0].lower() if candidate_agent.name else ""
                    agent_full_name = candidate_agent.name.lower()
                    
                    # Priority 1: Exact full name match (highest priority)
                    if agent_full_name in text_lower:
                        # Check for word boundaries to avoid partial matches
                        full_name_pattern = r'\b' + re.escape(agent_full_name) + r'\b'
                        if re.search(full_name_pattern, text_lower):
                            score = 100
                            matched_pattern = f"exact full name: '{agent_full_name}'"
                    
                    # Priority 2: Full name with "morgan taylor" format (for Morgan)
                    if score == 0 and agent_first_name == "morgan" and "morgan taylor" in text_lower:
                        score = 90
                        matched_pattern = "full name format: 'morgan taylor'"
                    
                    # Priority 3: First name match (but lower priority to avoid false matches)
                    if score == 0:
                        first_name_pattern = r'\b' + re.escape(agent_first_name) + r'\b'
                        if re.search(first_name_pattern, text_lower):
                            score = 50
                            matched_pattern = f"first name: '{agent_first_name}'"
                    
                    # Priority 4: Nickname matches (for Alex)
                    if score == 0 and agent_first_name == "alexandra":
                        if re.search(r'\balex\b', text_lower):
                            score = 60
                            matched_pattern = "nickname: 'alex'"
                    
                    if score > 0:
                        agent_scores.append((score, candidate_agent, matched_pattern))
                
                # Sort by score (highest first) and pick the best match
                if agent_scores:
                    agent_scores.sort(key=lambda x: x[0], reverse=True)
                    best_score, agent, matched_pattern = agent_scores[0]
                    print(f"✅ Thread reply matched agent by text: {agent.name} (score: {best_score}, pattern: {matched_pattern})")
                    
                    # If there's a tie or close scores, prefer exact matches
                    if len(agent_scores) > 1 and agent_scores[1][0] >= best_score * 0.8:
                        # Check if we have an exact full name match
                        exact_matches = [a for a in agent_scores if a[0] >= 90]
                        if exact_matches:
                            agent = exact_matches[0][1]
                            print(f"✅ Preferring exact match: {agent.name}")
            
            # Only use fallback to first agent if NO mentions were detected at all
            # If mentions were detected but didn't match, that's an error - don't default
            if not agent:
                if mentioned_user_ids or mentioned_display_names:
                    # We detected mentions but couldn't match them - this is an error
                    print(f"❌ ERROR: Detected agent mentions in thread reply but couldn't match to any agent!")
                    print(f"   Mentioned user IDs: {mentioned_user_ids}")
                    print(f"   Mentioned display names: {mentioned_display_names}")
                    print(f"   Available agents: {[(a.name, a.slack_user_id) for a in agents]}")
                    print(f"   This thread reply will NOT be processed to avoid wrong agent responding")
                    return  # Don't process - avoid wrong agent responding
                elif agents:
                    # No mentions detected at all - safe to use first agent as fallback
                    agent = agents[0]
                    print(f"✅ No agent mentioned in thread reply, using first agent in channel: {agent.name}")
            
            # Get bot token using helper function (checks agent-specific tokens)
            bot_token = get_bot_token_for_agent(
                agent_name=agent.name,
                agent_role=agent.role,
                agent_stored_token=agent.slack_bot_token
            )
            
            if not bot_token:
                print("⚠️  No Slack bot token available for thread reply")
                print(f"   Agent: {agent.name} ({agent.role})")
                return
            
            slack_service = SlackService(bot_token)
            
            # Get the bot user ID for this specific agent's bot
            # Each agent has their own bot, so we need to use the correct bot user ID
            bot_user_id = agent.slack_user_id
            if not bot_user_id:
                # If not stored in DB, get it from the SlackService (which uses the agent's token)
                bot_user_id = slack_service.bot_user_id
                # Store it in the database for future use
                if bot_user_id:
                    try:
                        agent.slack_user_id = bot_user_id
                        db.commit()
                        print(f"💾 Stored bot user ID {bot_user_id} for {agent.name}")
                    except Exception as e:
                        print(f"⚠️  Could not store bot user ID: {e}")
            
            # Check if message is from this specific bot (prevent recursive responses)
            if bot_user_id and user_id == bot_user_id:
                print(f"⏭️  Skipping thread reply from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id} for {agent.name})")
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

def get_bot_user_id_from_db(channel_id: str, agent_name: Optional[str] = None) -> Optional[str]:
    """
    Get bot user ID from database for a specific channel or agent.
    
    When we have separate bots for each agent, we need to get the correct bot user ID.
    If agent_name is provided, we prioritize that agent's bot user ID.
    
    Args:
        channel_id: Slack channel ID
        agent_name: Optional agent name to get specific bot user ID
    
    Returns:
        Bot user ID string or None if not found
    """
    try:
        from src.database.models import AgentEmployee
        from src.database.database import SessionLocal
        
        db = SessionLocal()
        try:
            # If agent name is provided, try to find that specific agent first
            if agent_name:
                agent = db.query(AgentEmployee).filter(
                    AgentEmployee.name == agent_name
                ).first()
                if agent and agent.slack_user_id:
                    return agent.slack_user_id
            
            # Try to find agent by channel
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
    """Ingest logs - delegates to LogsController."""
    return await LogsController.ingest_log(log, request, background_tasks, db)

@app.post("/ingest/logs/batch")
async def ingest_logs_batch(batch: LogBatchRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Ingest logs batch - delegates to LogsController."""
    return await LogsController.ingest_logs_batch(batch, request, background_tasks, db)
    code: int
    message: Optional[str] = None

    apiKey: str
    serviceName: str
    spans: List[OTelSpan]

@app.post("/otel/errors")
async def ingest_otel_errors(payload: OTelErrorPayload, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Ingest OpenTelemetry errors - delegates to LogsController."""
    return await LogsController.ingest_otel_errors(payload, background_tasks, request, db)

@app.post("/api-keys/generate")
def create_api_key(request: ApiKeyRequest, http_request: Request, db: Session = Depends(get_db)):
    """Generate API key - delegates to APIKeysController."""
    return APIKeysController.create_api_key(request, http_request, db)

@app.get("/api-keys")
def list_api_keys(request: Request, db: Session = Depends(get_db)):
    """List API keys - delegates to APIKeysController."""
    return APIKeysController.list_api_keys(request, db)

@app.get("/logs")
def list_logs(limit: int = 50, request: Request = None, db: Session = Depends(get_db)):
    """List logs - delegates to LogsController."""
    return LogsController.list_logs(limit, request, db)
# ============================================================================
# Source Maps Upload
# ============================================================================

@app.post("/api/sourcemaps/upload")
async def upload_sourcemaps(
    request: SourceMapUploadRequest,
    http_request: Request,
    db: Session = Depends(get_db)
):
    """Upload source maps - delegates to SourceMapsController."""
    return await SourceMapsController.upload_sourcemaps(request, http_request, db)


# ============================================================================
# GitHub Integration
# ============================================================================

@app.get("/integrations/github/reconnect")
def github_reconnect(
    request: Request,
    integration_id: int = Query(..., description="The integration ID to reconnect"),
    db: Session = Depends(get_db)
):
    """GitHub reconnect - delegates to IntegrationsController."""
    return IntegrationsController.github_reconnect(request, integration_id, db)

@app.get("/integrations/github/authorize")
def github_authorize(request: Request, reconnect: Optional[str] = None, integration_id: Optional[int] = None, db: Session = Depends(get_db)):
    """GitHub authorize - delegates to IntegrationsController."""
    return IntegrationsController.github_authorize(request, reconnect, integration_id, db)

@app.get("/integrations/github/callback")
def github_callback(request: Request, installation_id: Optional[str] = None, setup_action: Optional[str] = None, state: Optional[str] = None, db: Session = Depends(get_db)):
    """GitHub callback - delegates to IntegrationsController."""
    return IntegrationsController.github_callback(request, installation_id, setup_action, state, db)

@app.post("/integrations/github/connect")
def github_connect(config: GithubConfig, request: Request, db: Session = Depends(get_db)):
    """GitHub connect - delegates to IntegrationsController."""
    return IntegrationsController.github_connect(config, request, db)

@app.get("/integrations")
def list_integrations(request: Request, db: Session = Depends(get_db)):
    """List integrations - delegates to IntegrationsController."""
    return IntegrationsController.list_integrations(request, db)

@app.get("/integrations/providers")
def list_providers():
    """List providers - delegates to IntegrationsController."""
    return IntegrationsController.list_providers()

@app.get("/integrations/{integration_id}/config")
def get_integration_config(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """Get integration config - delegates to IntegrationsController."""
    return IntegrationsController.get_integration_config(integration_id, request, db)

@app.post("/integrations/{integration_id}/service-mapping")
def add_service_mapping(integration_id: int, mapping: ServiceMappingRequest, request: Request, db: Session = Depends(get_db)):
    """Add service mapping - delegates to IntegrationsController."""
    return IntegrationsController.add_service_mapping(integration_id, mapping, request, db)

@app.put("/integrations/{integration_id}/service-mappings")
def update_service_mappings(integration_id: int, update: ServiceMappingsUpdateRequest, request: Request, db: Session = Depends(get_db)):
    """Update service mappings - delegates to IntegrationsController."""
    return IntegrationsController.update_service_mappings(integration_id, update, request, db)

@app.delete("/integrations/{integration_id}/service-mapping/{service_name}")
def remove_service_mapping(integration_id: int, service_name: str, request: Request, db: Session = Depends(get_db)):
    """Remove service mapping - delegates to IntegrationsController."""
    return IntegrationsController.remove_service_mapping(integration_id, service_name, request, db)

@app.post("/integrations/github/webhook")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    """GitHub webhook - delegates to IntegrationsController."""
    return await IntegrationsController.github_webhook(request, db)

@app.get("/services")
def list_services(request: Request = None, db: Session = Depends(get_db)):
    """List services - delegates to ServicesController."""
    return ServicesController.list_services(request, db)

@app.get("/integrations/{integration_id}/repositories")
def list_repositories(integration_id: int, request: Request, db: Session = Depends(get_db)):
    """List repositories - delegates to IntegrationsController."""
    return IntegrationsController.list_repositories(integration_id, request, db)

@app.put("/integrations/{integration_id}")
def update_integration(
    integration_id: int,
    update_data: dict,
    request: Request,
    db: Session = Depends(get_db)
):
    """Update integration - delegates to IntegrationsController."""
    return IntegrationsController.update_integration(integration_id, update_data, request, db)

@app.post("/integrations/{integration_id}/setup")
def complete_integration_setup(
    integration_id: int,
    setup_data: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Complete integration setup and trigger CocoIndex indexing in background."""
    return IntegrationsController.complete_integration_setup_with_indexing(
        integration_id, setup_data, request, background_tasks, db
    )

@app.get("/integrations/{integration_id}")
def get_integration_details(
    integration_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get integration details - delegates to IntegrationsController."""
    return IntegrationsController.get_integration_details(integration_id, request, db)

@app.get("/stats")
def get_system_stats(request: Request = None, db: Session = Depends(get_db)):
    """Get system stats - delegates to StatsController."""
    return StatsController.get_system_stats(request, db)

@app.get("/incidents")
def list_incidents(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    service: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    request: Request = None,
    db: Session = Depends(get_db)
):
    """List incidents - delegates to IncidentsController."""
    return IncidentsController.list_incidents(status, severity, source, service, page, page_size, request, db)

@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Get incident - delegates to IncidentsController."""
    return await IncidentsController.get_incident(incident_id, background_tasks, request, db)

@app.post("/incidents/{incident_id}/analyze")
async def analyze_incident(incident_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Analyze incident - delegates to IncidentsController."""
    return await IncidentsController.analyze_incident(incident_id, background_tasks, request, db)
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
    """Update incident - delegates to IncidentsController."""
    return IncidentsController.update_incident(incident_id, update_data, request, db)

@app.post("/incidents/{incident_id}/test-agent")
async def test_agent_endpoint(
    incident_id: int, 
    request: Request, 
    db: Session = Depends(get_db)
):
    """Test agent - delegates to IncidentsController."""
    return await IncidentsController.test_agent(incident_id, request, db)
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
