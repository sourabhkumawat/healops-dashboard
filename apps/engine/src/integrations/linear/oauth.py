"""
Linear OAuth helper functions for authentication flow.
"""
import os
import requests
from typing import Dict, Any, Optional
from urllib.parse import urlencode


LINEAR_OAUTH_BASE = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
LINEAR_API_URL = "https://api.linear.app/graphql"


def get_authorization_url(state: str, redirect_uri: str, scopes: str = "read,write,issues:create,comments:create") -> str:
    """
    Generate Linear OAuth authorization URL.
    
    Args:
        state: State parameter for CSRF protection (should include user_id, nonce, etc.)
        redirect_uri: Callback URL where Linear will redirect after authorization
        scopes: Comma-separated list of OAuth scopes
        
    Returns:
        Authorization URL
    """
    client_id = os.getenv("LINEAR_CLIENT_ID")
    if not client_id:
        raise ValueError("LINEAR_CLIENT_ID environment variable not set")
    
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state
    }
    
    return f"{LINEAR_OAUTH_BASE}?{urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access token and refresh token.
    
    Args:
        code: Authorization code from Linear OAuth callback
        redirect_uri: Same redirect URI used in authorization request
        
    Returns:
        Dictionary with access_token, refresh_token, expires_in, token_type, etc.
    """
    client_id = os.getenv("LINEAR_CLIENT_ID")
    client_secret = os.getenv("LINEAR_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise ValueError("LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must be set")
    
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret
    }
    
    try:
        response = requests.post(LINEAR_TOKEN_URL, data=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error exchanging code for token: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        raise


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """
    Refresh an expired access token using refresh token.
    
    Args:
        refresh_token: Refresh token from previous OAuth flow
        
    Returns:
        Dictionary with new access_token, refresh_token, expires_in, etc.
    """
    client_id = os.getenv("LINEAR_CLIENT_ID")
    client_secret = os.getenv("LINEAR_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise ValueError("LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must be set")
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }
    
    try:
        response = requests.post(LINEAR_TOKEN_URL, data=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error refreshing token: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        raise
