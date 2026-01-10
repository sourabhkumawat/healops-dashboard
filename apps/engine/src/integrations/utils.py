"""
Integration utility functions.
"""
import secrets
import hashlib
from typing import Tuple


def generate_api_key() -> Tuple[str, str, str]:
    """
    Generate a new API key for integrations.
    
    Returns:
        Tuple of (full_key, key_hash, key_prefix)
        - full_key: The complete API key (only shown once)
        - key_hash: SHA-256 hash of the key for storage
        - key_prefix: First 8 characters for display
    
    Example:
        >>> full_key, key_hash, key_prefix = generate_api_key()
        >>> assert len(full_key) > 0
        >>> assert len(key_hash) == 64  # SHA-256 hex digest
    """
    # Generate a secure random token
    token = secrets.token_urlsafe(32)
    full_key = f"healops_live_{token}"
    
    # Create SHA-256 hash for secure storage
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    
    # Extract prefix for display (first 8 chars after prefix)
    # Format: healops_live_XXXXXXXX...
    parts = full_key.split('_')
    if len(parts) >= 3:
        key_prefix = f"{parts[0]}_{parts[1]}_{parts[2][:8]}"
    else:
        key_prefix = full_key[:20]  # Fallback to first 20 chars
    
    return full_key, key_hash, key_prefix
