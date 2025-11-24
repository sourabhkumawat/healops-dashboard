"""
Integration registry and utilities for one-click onboarding.
"""
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.
    
    Returns:
        tuple: (full_key, key_hash, key_prefix)
    """
    # Generate random key: healops_live_<32 random chars>
    random_part = secrets.token_urlsafe(32)
    full_key = f"healops_live_{random_part}"
    
    # Hash for storage
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    
    # Prefix for display (first 12 chars)
    key_prefix = full_key[:12]
    
    return full_key, key_hash, key_prefix

def verify_api_key(provided_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored hash."""
    computed_hash = hashlib.sha256(provided_key.encode()).hexdigest()
    return secrets.compare_digest(computed_hash, stored_hash)

class IntegrationRegistry:
    """Registry of available integration providers."""
    
    PROVIDERS = {
        "GCP": {
            "name": "Google Cloud Platform",
            "oauth_required": True,
            "setup_time": "~20 seconds",
            "features": ["Log Sink", "Pub/Sub", "Cloud Functions"]
        },
        "AWS": {
            "name": "Amazon Web Services",
            "oauth_required": False,
            "setup_time": "~15 seconds",
            "features": ["CloudWatch", "Lambda", "CloudFormation"]
        },
        "KUBERNETES": {
            "name": "Kubernetes",
            "oauth_required": False,
            "setup_time": "~10 seconds",
            "features": ["DaemonSet", "Fluent Bit", "Auto-discovery"]
        },
        "AGENT": {
            "name": "Universal Agent",
            "oauth_required": False,
            "setup_time": "~5 seconds",
            "features": ["VM Support", "Bare Metal", "On-Prem"]
        }
    }
    
    @classmethod
    def get_provider_info(cls, provider: str) -> Optional[Dict[str, Any]]:
        """Get information about a provider."""
        return cls.PROVIDERS.get(provider.upper())
    
    @classmethod
    def list_providers(cls) -> Dict[str, Dict[str, Any]]:
        """List all available providers."""
        return cls.PROVIDERS
