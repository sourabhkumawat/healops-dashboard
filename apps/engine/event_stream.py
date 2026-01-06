"""
Event Stream System for Manus-style structured event logging.
Tracks all agent actions, observations, and decisions chronologically.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
import json
import os

class EventType(Enum):
    """Types of events in the event stream."""
    USER_REQUEST = "user_request"
    AGENT_ACTION = "agent_action"
    OBSERVATION = "observation"
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    PLAN_STEP_STARTED = "plan_step_started"
    PLAN_STEP_COMPLETED = "plan_step_completed"
    PLAN_STEP_FAILED = "plan_step_failed"
    ERROR = "error"
    MEMORY_RETRIEVED = "memory_retrieved"
    KNOWLEDGE_RETRIEVED = "knowledge_retrieved"
    VALIDATION_RESULT = "validation_result"
    FILE_OPERATION = "file_operation"
    WORKSPACE_UPDATED = "workspace_updated"
    COMPRESSION = "compression"


class EventStream:
    """
    Manus-style event stream for tracking agent operations.
    
    Maintains a chronological log of all events during agent execution.
    Events can be converted to context strings for LLM consumption.
    """
    
    def __init__(self, incident_id: int, max_events: int = None):
        """
        Initialize event stream.
        
        Args:
            incident_id: ID of the incident being processed
            max_events: Maximum number of events to keep (default from env or 100)
        """
        self.incident_id = incident_id
        self.max_events = max_events or int(os.getenv("MAX_EVENT_STREAM_SIZE", "100"))
        self.events: List[Dict[str, Any]] = []
        self._websocket_broadcast_callback: Optional[callable] = None
    
    def set_websocket_broadcast(self, callback: callable):
        """
        Set callback for broadcasting events to WebSocket clients.
        
        Args:
            callback: Function that takes (incident_id, event_dict) and broadcasts
        """
        self._websocket_broadcast_callback = callback
    
    def add_event(
        self, 
        event_type: EventType, 
        data: Dict[str, Any], 
        agent_name: Optional[str] = None
    ):
        """
        Add an event to the stream.
        
        Args:
            event_type: Type of event
            data: Event data payload
            agent_name: Optional name of agent that generated the event
        """
        event = {
            "type": event_type.value,
            "timestamp": datetime.utcnow().isoformat(),
            "agent": agent_name,
            "data": data,
            "incident_id": self.incident_id
        }
        self.events.append(event)
        
        # Maintain max size (keep most recent)
        if len(self.events) > self.max_events:
            # Compress old events before dropping
            self._compress_old_events()
        
        # Broadcast to WebSocket if callback is set
        if self._websocket_broadcast_callback:
            try:
                self._websocket_broadcast_callback(self.incident_id, event)
            except Exception as e:
                print(f"Warning: Failed to broadcast event to WebSocket: {e}")
    
    def get_recent_events(self, n: int = 20) -> List[Dict[str, Any]]:
        """
        Get last N events for context.
        
        Args:
            n: Number of recent events to return
            
        Returns:
            List of recent event dictionaries
        """
        return self.events[-n:] if len(self.events) > n else self.events
    
    def get_events_by_type(self, event_type: EventType) -> List[Dict[str, Any]]:
        """
        Get all events of a specific type.
        
        Args:
            event_type: Type of events to filter
            
        Returns:
            List of matching events
        """
        return [e for e in self.events if e["type"] == event_type.value]
    
    def to_context_string(self, max_events: int = 20) -> str:
        """
        Convert event stream to context string for LLM.
        
        Args:
            max_events: Maximum number of events to include
            
        Returns:
            Formatted string representation of events
        """
        recent = self.get_recent_events(max_events)
        if not recent:
            return "No events yet."
        
        context_lines = []
        for event in recent:
            event_str = f"[{event['timestamp']}] {event['type'].upper()}"
            if event.get('agent'):
                event_str += f" by {event['agent']}"
            event_str += f": {json.dumps(event['data'], indent=2, default=str)}"
            context_lines.append(event_str)
        
        return "\n".join(context_lines)
    
    def summarize_old_events(self) -> str:
        """
        Summarize older events to save context space.
        
        Returns:
            Summary string of compressed events
        """
        if len(self.events) <= self.max_events:
            return ""
        
        # Count events by type
        old_count = len(self.events) - self.max_events
        event_counts = {}
        for event in self.events[:-self.max_events]:
            event_type = event["type"]
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        summary_parts = [f"Total events: {len(self.events)}. {old_count} older events summarized."]
        if event_counts:
            summary_parts.append("Event breakdown:")
            for event_type, count in event_counts.items():
                summary_parts.append(f"  - {event_type}: {count}")
        
        return "\n".join(summary_parts)
    
    def _compress_old_events(self):
        """
        Compress events beyond max_events into a summary event.
        """
        if len(self.events) <= self.max_events:
            return
        
        old_events = self.events[:-self.max_events]
        recent_events = self.events[-self.max_events:]
        
        # Create summary of old events
        summary = self.summarize_old_events()
        
        # Replace old events with compression event
        compression_event = {
            "type": EventType.COMPRESSION.value,
            "timestamp": datetime.utcnow().isoformat(),
            "agent": None,
            "data": {"summary": summary, "compressed_count": len(old_events)},
            "incident_id": self.incident_id
        }
        
        # Keep compression event + recent events
        self.events = [compression_event] + recent_events
    
    def get_all_events(self) -> List[Dict[str, Any]]:
        """
        Get all events in the stream.
        
        Returns:
            Complete list of events
        """
        return self.events.copy()
    
    def clear(self):
        """Clear all events from the stream."""
        self.events = []
    
    def get_event_count(self) -> int:
        """Get total number of events."""
        return len(self.events)
    
    def get_last_event(self) -> Optional[Dict[str, Any]]:
        """Get the most recent event."""
        return self.events[-1] if self.events else None

