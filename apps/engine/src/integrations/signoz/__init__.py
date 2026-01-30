"""SigNoz integration - fetch error logs and error traces only."""
from src.integrations.signoz.client import fetch_error_events

__all__ = ["fetch_error_events"]
