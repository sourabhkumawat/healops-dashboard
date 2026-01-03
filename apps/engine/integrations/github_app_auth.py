"""
GitHub App authentication utilities.
Handles JWT token generation and installation token management.
"""
import os
import time
import jwt
import requests
from typing import Optional, Dict, Any
from datetime import datetime, timedelta


# GitHub App configuration from environment
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY")
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG")


def get_private_key() -> Optional[str]:
    """Get the private key, handling both raw and PEM format."""
    private_key = GITHUB_APP_PRIVATE_KEY
    if not private_key:
        return None
    
    # If the key contains newlines, it's already formatted
    if '\n' in private_key:
        return private_key
    
    # Otherwise, it might be escaped newlines - try to format it
    # Common formats: \\n or escaped string
    private_key = private_key.replace('\\n', '\n')
    
    return private_key


def generate_jwt_token() -> Optional[str]:
    """
    Generate a JWT token for GitHub App authentication.
    
    The JWT token is valid for 10 minutes and is used to authenticate
    as the GitHub App (not a specific installation).
    
    Returns:
        JWT token string, or None if configuration is invalid
    """
    if not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY:
        return None
    
    private_key = get_private_key()
    if not private_key:
        return None
    
    try:
        # GitHub Apps use RS256 algorithm
        # JWT payload requires:
        # - iat: issued at time (seconds since epoch)
        # - exp: expiration time (must be within 10 minutes of iat)
        # - iss: issuer (GitHub App ID)
        
        now = int(time.time())
        payload = {
            'iat': now - 60,  # Issued 60 seconds ago to account for clock skew
            'exp': now + (10 * 60) - 60,  # Expires in 10 minutes (minus 60 seconds buffer)
            'iss': int(GITHUB_APP_ID)  # GitHub App ID as integer
        }
        
        token = jwt.encode(payload, private_key, algorithm='RS256')
        return token
    except Exception as e:
        print(f"Error generating JWT token: {e}")
        return None


def get_installation_token(installation_id: int) -> Optional[Dict[str, Any]]:
    """
    Get an installation access token for a GitHub App installation.
    
    Installation tokens are short-lived (1 hour) and provide access
    to the repositories that were selected during installation.
    
    Args:
        installation_id: GitHub App installation ID
        
    Returns:
        Dict with 'token' and 'expires_at', or None if error
    """
    jwt_token = generate_jwt_token()
    if not jwt_token:
        return None
    
    try:
        # GitHub API endpoint for installation tokens
        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.post(url, headers=headers)
        
        if response.status_code != 201:
            print(f"Error getting installation token: {response.status_code} - {response.text}")
            return None
        
        data = response.json()
        token = data.get("token")
        expires_at_str = data.get("expires_at")
        
        if not token:
            return None
        
        # Parse expires_at to datetime
        expires_at = None
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
            except:
                pass
        
        return {
            "token": token,
            "expires_at": expires_at
        }
    except Exception as e:
        print(f"Error requesting installation token: {e}")
        return None


def get_installation_info(installation_id: int) -> Optional[Dict[str, Any]]:
    """
    Get information about a GitHub App installation.
    
    Args:
        installation_id: GitHub App installation ID
        
    Returns:
        Dict with installation info, or None if error
    """
    jwt_token = generate_jwt_token()
    if not jwt_token:
        return None
    
    try:
        url = f"https://api.github.com/app/installations/{installation_id}"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"Error getting installation info: {response.status_code} - {response.text}")
            return None
        
        return response.json()
    except Exception as e:
        print(f"Error getting installation info: {e}")
        return None


def get_installation_repositories(installation_id: int) -> list[Dict[str, Any]]:
    """
    Get list of repositories accessible by an installation.
    
    Args:
        installation_id: GitHub App installation ID
        
    Returns:
        List of repository dicts with 'full_name', 'name', 'private', etc.
    """
    token_data = get_installation_token(installation_id)
    if not token_data:
        return []
    
    token = token_data["token"]
    
    try:
        url = f"https://api.github.com/installation/repositories"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"Error getting installation repositories: {response.status_code} - {response.text}")
            return []
        
        data = response.json()
        repositories = data.get("repositories", [])
        
        return [
            {
                "full_name": repo.get("full_name"),
                "name": repo.get("name"),
                "private": repo.get("private", False),
                "html_url": repo.get("html_url")
            }
            for repo in repositories
        ]
    except Exception as e:
        print(f"Error getting installation repositories: {e}")
        return []


def verify_installation_token(token: str, installation_id: int) -> bool:
    """
    Verify that an installation token is valid for a given installation.
    
    Args:
        token: Installation token to verify
        installation_id: Expected installation ID
        
    Returns:
        True if token is valid, False otherwise
    """
    try:
        # Try to make an API call with the token
        url = "https://api.github.com/installation/repositories"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        return response.status_code == 200
    except:
        return False

