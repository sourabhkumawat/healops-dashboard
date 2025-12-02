from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session
from database import SessionLocal
from models import ApiKey, Integration
import hashlib

class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip API key validation for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)
        
        # Only enforce for /ingest/logs
        if request.url.path.startswith("/ingest/logs"):
            # Support both X-HealOps-Key header and Authorization Bearer token
            api_key_header = request.headers.get("X-HealOps-Key")
            if not api_key_header:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    api_key_header = auth_header.replace("Bearer ", "").strip()
            
            if not api_key_header:
                # Return 401 directly
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing API key. Use X-HealOps-Key header or Authorization Bearer token"}
                )
            
            # Hash the key to look it up
            key_hash = hashlib.sha256(api_key_header.encode()).hexdigest()
            
            db: Session = SessionLocal()
            try:
                api_key = db.query(ApiKey).filter(
                    ApiKey.key_hash == key_hash,
                    ApiKey.is_active == 1
                ).first()
                
                if not api_key:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Invalid or inactive API key"}
                    )
                
                # Attach integration context to request state
                # If the key is linked to a specific integration, use that
                # Otherwise, it might be a global key (future proofing), but for now we assume 1:1 or 1:N
                # Actually, the PRD implies we need to know WHICH integration sent this.
                # If the API key is generic, we might need to infer from payload, but usually keys are per-integration or per-user.
                # For now, let's attach the api_key object and let the endpoint decide.
                
                request.state.api_key = api_key
                request.state.user_id = api_key.user_id
                
            finally:
                db.close()
                
        response = await call_next(request)
        return response
