"""
Timeout Middleware - Handles long-running requests gracefully.

This middleware ensures that requests don't exceed Cloudflare tunnel timeout limits
by canceling requests that take too long and returning appropriate error responses.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import asyncio
import os
from typing import Optional

# Default timeout: 15 seconds (reduced to prevent Cloudflare cancellations)
# This gives us a buffer to return an error response before Cloudflare cancels
DEFAULT_REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))


class TimeoutMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces request timeouts to prevent Cloudflare tunnel cancellations.
    
    For ingestion endpoints, we want to return quickly even if processing continues
    in the background. This prevents "context canceled" errors.
    """
    
    def __init__(self, app, timeout_seconds: Optional[int] = None):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds or DEFAULT_REQUEST_TIMEOUT
        
        # Endpoints that should have shorter timeouts (ingestion endpoints)
        self.ingestion_endpoints = {
            "/ingest/logs",
            "/ingest/logs/batch",
            "/otel/errors"
        }
        
        # Timeout for ingestion endpoints: 10 seconds (reduced for faster response)
        # Since we now return 202 immediately, this is mainly a safety net
        self.ingestion_timeout = int(os.getenv("INGESTION_TIMEOUT_SECONDS", "10"))
    
    async def dispatch(self, request: Request, call_next):
        # Skip timeout for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)
        
        # Determine timeout based on endpoint
        path = request.url.path
        is_ingestion = any(path.startswith(ep) for ep in self.ingestion_endpoints)
        timeout = self.ingestion_timeout if is_ingestion else self.timeout_seconds
        
        try:
            # Wrap the request handling with a timeout
            response = await asyncio.wait_for(
                call_next(request),
                timeout=timeout
            )
            return response
        except asyncio.TimeoutError:
            # Request exceeded timeout - return error before Cloudflare cancels
            return JSONResponse(
                status_code=504,
                content={
                    "detail": f"Request timeout: The request took longer than {timeout} seconds to process. "
                             "This may be due to high load. Please try again or contact support if the issue persists.",
                    "timeout_seconds": timeout,
                    "endpoint": path
                }
            )
        except Exception as e:
            # Re-raise other exceptions to be handled by FastAPI's error handlers
            raise
