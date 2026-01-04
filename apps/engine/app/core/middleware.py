"""
SECURE MIDDLEWARE - Authentication and Authorization

This version:
1. Keeps existing APIKeyMiddleware for /ingest/logs and /api/sourcemaps
2. Adds new AuthenticationMiddleware to protect ALL other endpoints
3. Properly extracts and validates user_id from JWT or API key
"""

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.models import ApiKey, User
from app.core.config import settings
import hashlib
import os

class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Validates API keys for ingestion endpoints.
    Sets request.state.api_key and request.state.user_id for authenticated requests.
    """
    async def dispatch(self, request: Request, call_next):
        # Skip API key validation for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Handle /otel/errors endpoint - API key is in request body
        if request.url.path == "/otel/errors":
            # Read request body to extract API key
            body = await request.body()
            if not body:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing request body with API key"}
                )

            try:
                import json
                payload = json.loads(body)
                api_key_from_body = payload.get("apiKey")
            except json.JSONDecodeError:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid request body. Expected JSON with 'apiKey' field"}
                )

            if not api_key_from_body:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing API key in request body"}
                )

            # Hash the key to look it up
            key_hash = hashlib.sha256(api_key_from_body.encode()).hexdigest()

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

                # Attach API key and user_id to request state
                request.state.api_key = api_key
                request.state.user_id = api_key.user_id

            finally:
                db.close()

            # Restore body for endpoint handler
            # FastAPI needs the body to be available for the endpoint handler
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive

        # Handle /ingest/logs and /api/sourcemaps - API key is in headers
        elif request.url.path.startswith("/ingest/logs") or request.url.path.startswith("/api/sourcemaps"):
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

                # Attach API key and user_id to request state
                request.state.api_key = api_key
                request.state.user_id = api_key.user_id

            finally:
                db.close()

        response = await call_next(request)
        return response


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Enforces authentication on ALL endpoints except public ones.
    Validates JWT tokens and sets request.state.user_id for authenticated users.

    SECURITY: This middleware MUST be added before APIKeyMiddleware in main.py
    """

    # Endpoints that don't require authentication (public)
    PUBLIC_ENDPOINTS = {
        "/",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/auth/register",
        "/auth/login",
        # GitHub OAuth callback (user is authenticated by GitHub, not us yet)
        # Note: /integrations/github/authorize is now protected - user must be authenticated
        "/integrations/github/callback",
    }

    async def dispatch(self, request: Request, call_next):
        # Skip OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public endpoints without authentication
        if request.url.path in self.PUBLIC_ENDPOINTS:
            return await call_next(request)

        # Skip /otel/errors - it's handled by APIKeyMiddleware (API key in body)
        # Skip /ingest/logs and /api/sourcemaps - they're handled by APIKeyMiddleware (API key in headers)
        if (request.url.path == "/otel/errors" or
            request.url.path.startswith("/ingest/logs") or
            request.url.path.startswith("/api/sourcemaps")):
            return await call_next(request)

        # Check if user_id was already set by APIKeyMiddleware
        # (for /ingest/logs and /api/sourcemaps endpoints)
        if hasattr(request.state, 'user_id') and request.state.user_id:
            # Already authenticated by API key
            return await call_next(request)

        # Try to authenticate via JWT token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Authentication required. Provide Authorization Bearer token or X-HealOps-Key header."
                }
            )

        token = auth_header.replace("Bearer ", "").strip()

        # Validate JWT token
        try:
            from jose import jwt, JWTError

            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            email = payload.get("sub")

            if not email:
                raise JWTError("No email in token")

            # Look up user by email
            db: Session = SessionLocal()
            try:
                user = db.query(User).filter(User.email == email).first()
                if not user:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "User not found"}
                    )

                # Set user_id in request state for downstream handlers
                request.state.user_id = user.id
                request.state.user_email = email

            finally:
                db.close()

        except JWTError as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": f"Invalid or expired token: {str(e)}"}
            )
        except Exception as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"detail": f"Authentication error: {str(e)}"}
            )

        # User is authenticated - proceed with request
        response = await call_next(request)
        return response
