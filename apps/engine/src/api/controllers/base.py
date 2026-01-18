"""
Base controller utilities and helper functions.
"""
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from typing import Optional


def get_user_id_from_request(request: Request, db: Session = None) -> int:
    """
    Get user_id from request state (set by AuthenticationMiddleware).

    SECURITY: This function now REQUIRES authentication. The AuthenticationMiddleware
    ensures request.state.user_id is always set for protected endpoints.

    Args:
        request: FastAPI Request object
        db: Optional database session (unused, kept for backward compatibility)

    Returns:
        int: The authenticated user's ID

    Raises:
        HTTPException: If user_id not found (should never happen if middleware works correctly)
    """
    # User ID should have been set by middleware
    if hasattr(request.state, 'user_id') and request.state.user_id:
        return request.state.user_id

    # If we reach here, middleware failed - this is a critical error
    raise HTTPException(
        status_code=401,
        detail="Authentication required but user_id not found in request state. This indicates a middleware configuration error."
    )
