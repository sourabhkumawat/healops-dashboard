"""
API Keys Controller - Handles API key generation and management.
"""
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.database.models import ApiKey
from src.integrations import generate_api_key
from src.api.controllers.base import get_user_id_from_request


class ApiKeyRequest(BaseModel):
    name: str


class APIKeysController:
    """Controller for API key management."""
    
    @staticmethod
    def create_api_key(request: ApiKeyRequest, http_request: Request, db: Session):
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
    
    @staticmethod
    def list_api_keys(request: Request, db: Session):
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
