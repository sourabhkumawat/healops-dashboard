"""
WebSocket connection managers for real-time communication.
"""
import json
import asyncio
import threading
import redis
import os
from typing import List, Dict, Any
from fastapi import WebSocket

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


class ConnectionManager:
    """Manages WebSocket connections with Redis pub/sub for log broadcasting."""
    
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


# Global instances (will be initialized in main.py)
connection_manager = ConnectionManager()
agent_event_manager = AgentEventManager()
