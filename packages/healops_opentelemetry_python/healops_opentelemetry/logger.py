import requests
import json
from datetime import datetime
from typing import Optional, Dict, Any


class HealOpsLogger:
    """HealOps Logger for sending logs directly to the backend"""
    
    def __init__(self, api_key: str, service_name: str, endpoint: str = "http://localhost:8000", source: str = "healops-sdk"):
        self.api_key = api_key
        self.service_name = service_name
        self.endpoint = endpoint
        self.source = source
    
    def info(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send an INFO level log"""
        self._send_log("INFO", message, metadata)
    
    def warn(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send a WARNING level log"""
        self._send_log("WARNING", message, metadata)
    
    def error(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send an ERROR level log (will be persisted and may create incident)"""
        self._send_log("ERROR", message, metadata)
    
    def critical(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send a CRITICAL level log (will be persisted and may create incident)"""
        self._send_log("CRITICAL", message, metadata)
    
    def _send_log(self, severity: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Internal method to send log to backend"""
        payload = {
            "service_name": self.service_name,
            "severity": severity,
            "message": message,
            "source": self.source,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata or {}
        }
        
        try:
            response = requests.post(
                f"{self.endpoint}/ingest/logs",
                json=payload,
                headers={
                    "X-HealOps-Key": self.api_key,
                    "Content-Type": "application/json"
                },
                timeout=3
            )
            response.raise_for_status()
        except Exception as e:
            # Silent fail - don't break application if logging fails
            import os
            if os.getenv("HEALOPS_DEBUG"):
                print(f"HealOps Logger failed to send log: {e}")
