"""
Redpanda-powered WebSocket connection manager for real-time log broadcasting.
Replaces Redis pub/sub with Redpanda for better persistence and reliability.
"""
import json
import asyncio
from typing import List, Dict, Any
from fastapi import WebSocket

from src.services.redpanda_service import redpanda_service


class RedpandaConnectionManager:
    """Manages WebSocket connections with Redpanda consumer for log broadcasting."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.initialized = False

    def initialize(self, loop):
        """Initialize with FastAPI's event loop and setup Redpanda consumer."""
        if self.initialized:
            return

        # Set up Redpanda consumer for log broadcasting
        redpanda_service.setup_log_consumer(self._broadcast_to_websockets)
        redpanda_service.start_consumers()

        self.initialized = True
        print("✓ Redpanda WebSocket manager initialized")

    async def _broadcast_to_websockets(self, message: dict):
        """Broadcast message to all active WebSocket connections with timeout protection."""
        if not self.active_connections:
            return

        disconnected = []
        # Use asyncio.gather with timeout to prevent blocking on slow connections
        async def send_all():
            tasks = []
            for connection in self.active_connections:
                tasks.append(self._send_to_connection(connection, message))
            return await asyncio.gather(*tasks, return_exceptions=True)
        
        try:
            results = await asyncio.wait_for(send_all(), timeout=0.5)
            # Check results and mark failed connections as disconnected
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    disconnected.append(self.active_connections[i])
        except asyncio.TimeoutError:
            print("Warning: WebSocket broadcast timeout, some connections may be slow")
            # On timeout, we can't determine which connections failed, so continue
        
        # Remove disconnected connections
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)
                print(f"Removed disconnected WebSocket. Active connections: {len(self.active_connections)}")
    
    async def _send_to_connection(self, connection: WebSocket, message: dict):
        """Send message to a single connection with error handling."""
        try:
            await connection.send_json(message)
        except Exception:
            raise  # Re-raise so gather can catch it

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """
        Publish message to Redpanda topic (replaces Redis pub/sub).
        The message will be consumed and broadcast to WebSockets via Redpanda consumer.
        Includes timeout protection to prevent blocking.
        """
        try:
            # Publish to Redpanda with timeout (1 second max)
            async def _do_broadcast():
                success = redpanda_service.producer.publish_log(message)
                if not success:
                    # Fallback: broadcast directly to WebSockets if Redpanda is unavailable
                    print("Warning: Redpanda publish failed, falling back to direct WebSocket broadcast")
                    await self._broadcast_to_websockets(message)
            
            await asyncio.wait_for(_do_broadcast(), timeout=1.0)
        except asyncio.TimeoutError:
            # Broadcast timeout - log but don't fail (broadcast is non-critical)
            print("Warning: Broadcast timeout, continuing without broadcast")
        except Exception as e:
            print(f"Error broadcasting via Redpanda: {e}")
            # Fallback: broadcast directly to WebSockets with timeout
            try:
                await asyncio.wait_for(self._broadcast_to_websockets(message), timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                pass  # Broadcast is non-critical, continue

    def health_check(self) -> Dict[str, Any]:
        """Get health status of the connection manager."""
        return {
            "active_connections": len(self.active_connections),
            "redpanda_healthy": redpanda_service.is_healthy(),
            "initialized": self.initialized
        }


class AgentEventManager:
    """Manages WebSocket connections for agent events (unchanged from original)."""

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
connection_manager = RedpandaConnectionManager()
agent_event_manager = AgentEventManager()