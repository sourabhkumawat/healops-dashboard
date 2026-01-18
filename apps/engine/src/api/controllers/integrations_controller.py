"""
Integrations Controller - Handles GitHub and other integration management.
"""
import os
import json
import time
import secrets
import base64
from fastapi import Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

from src.database.models import Integration, AgentPR, AgentEmployee
from src.integrations import GithubIntegration
from src.integrations.github import get_installation_info, get_installation_repositories
from src.auth import encrypt_token
from src.utils.integrations import backfill_integration_to_incidents
from src.api.controllers.base import get_user_id_from_request
from src.utils.indexing_manager import indexing_manager

# Configuration constants
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG")
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")


class GithubConfig(BaseModel):
    access_token: str


class ServiceMappingRequest(BaseModel):
    service_name: str
    repo_name: str  # Format: "owner/repo"


class ServiceMappingsUpdateRequest(BaseModel):
    service_mappings: Dict[str, str]  # Dict of {service_name: repo_name}
    default_repo: Optional[str] = None  # Default repo for services without mapping


class IntegrationsController:
    """Controller for integration management."""
    
    @staticmethod
    def github_reconnect(request: Request, integration_id: int, db: Session):
        """Handle reconnection by redirecting to GitHub App installation page."""
        if not GITHUB_APP_SLUG:
            raise HTTPException(status_code=500, detail="GitHub App Slug not configured")
        
        # Get the integration to reconnect
        user_id = get_user_id_from_request(request, db=db)
        
        # SECURITY: Verify the integration belongs to the authenticated user
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id,  # SECURITY: Verify integration belongs to user
            Integration.provider == "GITHUB"
        ).first()
        
        if not integration:
            raise HTTPException(
                status_code=404, 
                detail=f"GitHub integration with ID {integration_id} not found or does not belong to your account"
            )
        
        # Mark as disconnected to indicate we're reconnecting
        integration.status = "DISCONNECTED"
        db.commit()
        
        # Redirect to GitHub App installation with reconnect flag
        # Use a unique timestamp and nonce to ensure GitHub treats it as a new installation request
        state_data = {
            "timestamp": int(time.time() * 1000),  # Use milliseconds for uniqueness
            "user_id": user_id,  # SECURITY: Include user_id to ensure integration is associated with correct user
            "reconnect": True,
            "integration_id": integration_id,
            "nonce": secrets.token_urlsafe(16)  # Add random nonce to force new installation
        }
        state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
        
        # GitHub App installation URL
        install_url = (
            f"https://github.com/apps/{GITHUB_APP_SLUG}/installations/new"
            f"?state={state}"
        )
        
        return RedirectResponse(install_url)
    
    @staticmethod
    def github_authorize(request: Request, reconnect: Optional[str], integration_id: Optional[int], db: Session):
        """Redirect user to GitHub App installation page.
        
        SECURITY: This endpoint requires authentication to ensure integrations are
        created for the correct user. User ID is included in state parameter.
        """
        if not GITHUB_APP_SLUG:
            raise HTTPException(status_code=500, detail="GitHub App Slug not configured")
        
        # Get authenticated user_id (middleware ensures this is set)
        user_id = get_user_id_from_request(request, db=db)
        
        # Generate state parameter for security
        # Include user_id, integration_id if reconnecting, and a nonce
        state_data = {
            "timestamp": int(time.time() * 1000),  # Use milliseconds for uniqueness
            "user_id": user_id,  # SECURITY: Include user_id to associate integration with correct user
            "reconnect": reconnect == "true",
            "nonce": secrets.token_urlsafe(16)  # Add random nonce to ensure uniqueness
        }
        if integration_id:
            state_data["integration_id"] = integration_id
        
        # Encode state as base64 JSON for passing through installation flow
        state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()
        
        # GitHub App installation URL
        # Users can select organizations and repositories during installation
        install_url = (
            f"https://github.com/apps/{GITHUB_APP_SLUG}/installations/new"
            f"?state={state}"
        )
        
        return RedirectResponse(install_url)
    
    @staticmethod
    def github_callback(request: Request, installation_id: Optional[str], setup_action: Optional[str], state: Optional[str], db: Session):
        """Handle GitHub App installation callback.
        
        GitHub redirects here after installation with installation_id in query params.
        """
        try:
            if not GITHUB_APP_ID:
                print("ERROR: GITHUB_APP_ID not configured")
                raise HTTPException(status_code=500, detail="GitHub App ID not configured")
            
            if not installation_id:
                print("ERROR: installation_id parameter missing")
                raise HTTPException(status_code=400, detail="installation_id parameter is required")
            
            try:
                installation_id_int = int(installation_id)
            except ValueError:
                print(f"ERROR: Invalid installation_id format: {installation_id}")
                raise HTTPException(status_code=400, detail="Invalid installation_id format")
            
            # Decode state parameter if provided
            reconnect = False
            integration_id = None
            user_id = None
            if state:
                try:
                    state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
                    reconnect = state_data.get("reconnect", False)
                    integration_id = state_data.get("integration_id")
                    user_id = state_data.get("user_id")  # SECURITY: Extract user_id from state
                    print(f"DEBUG: Decoded state - user_id={user_id}, reconnect={reconnect}, integration_id={integration_id}")
                except Exception as e:
                    print(f"ERROR: Failed to decode state parameter: {e}")
                    import traceback
                    traceback.print_exc()
                    raise HTTPException(status_code=400, detail=f"Invalid state parameter: {str(e)}")
            
            # SECURITY: user_id is required - try to get from state, or from request state (if authenticated)
            if not user_id:
                # Fallback: try to get user_id from request state (if user is authenticated via session)
                try:
                    if hasattr(request.state, 'user_id') and request.state.user_id:
                        user_id = request.state.user_id
                        print(f"DEBUG: Got user_id from request.state: {user_id}")
                except Exception as e:
                    print(f"DEBUG: Could not get user_id from request.state: {e}")
                    pass
            
            # If still no user_id, this is an invalid request
            if not user_id:
                print("ERROR: No user_id found in state or request")
                error_msg = "Please initiate the GitHub installation from the application settings page."
                return RedirectResponse(f"{FRONTEND_URL}/settings?tab=integrations&error={error_msg}")
            
            print(f"DEBUG: Getting installation info for installation_id={installation_id_int}")
            # Get installation info to get account details
            installation_info = get_installation_info(installation_id_int)
            if not installation_info:
                print(f"ERROR: Failed to retrieve installation info for installation_id={installation_id_int}")
                raise HTTPException(status_code=400, detail="Failed to retrieve installation information from GitHub. Please check your GitHub App configuration.")
            
            account = installation_info.get("account", {})
            account_login = account.get("login", "GitHub App")
            account_type = account.get("type", "User")
            print(f"DEBUG: Installation account: {account_login} (type: {account_type})")
        
            # Handle reconnection: if integration_id is provided and reconnecting, update that specific integration
            # SECURITY: Verify the integration belongs to the user_id from state
            if reconnect and integration_id:
                print(f"DEBUG: Reconnecting integration_id={integration_id}")
                integration = db.query(Integration).filter(
                    Integration.id == integration_id,
                    Integration.user_id == user_id,  # SECURITY: Verify integration belongs to user
                    Integration.provider == "GITHUB"
                ).first()
                if not integration:
                    print(f"ERROR: Integration {integration_id} not found for user {user_id}")
                    raise HTTPException(
                        status_code=404, 
                        detail=f"Integration not found or does not belong to your account"
                    )
                integration.installation_id = installation_id_int
                integration.access_token = None  # Clear OAuth token if present
                integration.status = "CONFIGURING"  # Set to CONFIGURING for repository selection
                integration.last_verified = datetime.utcnow()
                integration.name = f"GitHub ({account_login})"
                # Store installation metadata in config
                if not integration.config:
                    integration.config = {}
                integration.config["installation_account"] = account_login
                integration.config["installation_account_type"] = account_type
                db.commit()
                db.refresh(integration)
                print(f"DEBUG: Reconnected integration {integration.id}, redirecting to setup")
                # Redirect to setup page for repository selection (though repos are already selected during installation)
                return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}&reconnected=true")

            # Check if integration already exists for this user
            print(f"DEBUG: Checking for existing integration for user_id={user_id}")
            integration = db.query(Integration).filter(
                Integration.user_id == user_id,
                Integration.provider == "GITHUB"
            ).first()

            if not integration:
                print(f"DEBUG: Creating new integration for user_id={user_id}")
                integration = Integration(
                    user_id=user_id,
                    provider="GITHUB",
                    name=f"GitHub ({account_login})",
                    status="CONFIGURING",  # Start in CONFIGURING state
                    installation_id=installation_id_int,
                    access_token=None,  # GitHub Apps don't use OAuth tokens
                    last_verified=datetime.utcnow(),
                    config={
                        "installation_account": account_login,
                        "installation_account_type": account_type
                    }
                )
                db.add(integration)
                db.commit()
                db.refresh(integration)
                print(f"DEBUG: Created integration {integration.id}, redirecting to setup")
                # Redirect to setup page for initial configuration
                return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}&new=true")
            else:
                print(f"DEBUG: Updating existing integration {integration.id}")
                # Update existing integration with new installation_id
                integration.installation_id = installation_id_int
                integration.access_token = None  # Clear OAuth token if present
                integration.status = "CONFIGURING"  # Set to CONFIGURING for reconfiguration
                integration.last_verified = datetime.utcnow()
                integration.name = f"GitHub ({account_login})"
                if not integration.config:
                    integration.config = {}
                integration.config["installation_account"] = account_login
                integration.config["installation_account_type"] = account_type
                db.commit()
                db.refresh(integration)
                print(f"DEBUG: Updated integration {integration.id}, redirecting to setup")
                # Redirect to setup page
                return RedirectResponse(f"{FRONTEND_URL}/integrations/github/setup?integration_id={integration.id}")
        
        except HTTPException:
            # Re-raise HTTPExceptions as-is
            raise
        except Exception as e:
            # Log unexpected errors
            print(f"ERROR: Unexpected error in github_callback: {e}")
            import traceback
            traceback.print_exc()
            # Redirect to frontend with error
            error_msg = f"Internal server error: {str(e)}"
            return RedirectResponse(f"{FRONTEND_URL}/settings?tab=integrations&error={error_msg}")
    
    @staticmethod
    def github_connect(config: GithubConfig, request: Request, db: Session):
        """Connect GitHub integration."""
        # Verify token
        gh = GithubIntegration(access_token=config.access_token)
        verification = gh.verify_connection()
        
        if verification["status"] == "error":
            raise HTTPException(status_code=400, detail=verification["message"])
        
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        # Encrypt token
        encrypted_token = encrypt_token(config.access_token)
        
        # Check if integration already exists
        integration = db.query(Integration).filter(
            Integration.user_id == user_id,
            Integration.provider == "GITHUB"
        ).first()
        
        if not integration:
            integration = Integration(
                user_id=user_id,
                provider="GITHUB",
                name=f"GitHub ({verification.get('username', 'User')})",
                status="ACTIVE",
                access_token=encrypted_token,
                last_verified=datetime.utcnow()
            )
            db.add(integration)
        else:
            integration.access_token = encrypted_token
            integration.status = "ACTIVE"
            integration.last_verified = datetime.utcnow()
            integration.name = f"GitHub ({verification.get('username', 'User')})"
        
        db.commit()
        db.refresh(integration)
        
        # Backfill integration_id and repo_name to existing incidents for this user
        backfill_integration_to_incidents(db, integration.id, user_id, integration.config)
        
        return {
            "status": "connected",
            "username": verification.get("username"),
            "message": "GitHub connected successfully"
        }
    
    @staticmethod
    def list_integrations(request: Request, db: Session):
        """List all user integrations."""
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        integrations = db.query(Integration).filter(Integration.user_id == user_id).all()
        
        return {
            "integrations": [
                {
                    "id": i.id,
                    "provider": i.provider,
                    "name": i.name,
                    "status": i.status,
                    "project_id": i.project_id,
                    "created_at": i.created_at,
                    "last_verified": i.last_verified
                }
                for i in integrations
            ]
        }
    
    @staticmethod
    def list_providers():
        """List available integration providers."""
        from src.integrations import IntegrationRegistry
        
        return IntegrationRegistry.list_providers()
    
    @staticmethod
    def get_integration_config(integration_id: int, request: Request, db: Session):
        """Get integration configuration including service mappings."""
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()
        
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        
        config = integration.config or {}
        service_mappings = config.get("service_mappings", {})
        default_repo = config.get("repo_name") or config.get("repository") or integration.project_id
        
        return {
            "integration_id": integration.id,
            "provider": integration.provider,
            "default_repo": default_repo,
            "service_mappings": service_mappings
        }
    
    @staticmethod
    def add_service_mapping(integration_id: int, mapping: ServiceMappingRequest, request: Request, db: Session):
        """Add or update a service-to-repo mapping."""
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()
        
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        
        if integration.provider != "GITHUB":
            raise HTTPException(status_code=400, detail="Service mappings are only supported for GitHub integrations")
        
        # Initialize config if needed
        if not integration.config:
            integration.config = {}
        
        # Initialize service_mappings if needed
        if "service_mappings" not in integration.config:
            integration.config["service_mappings"] = {}
        
        # Add or update the mapping
        integration.config["service_mappings"][mapping.service_name] = mapping.repo_name
        integration.updated_at = datetime.utcnow()
        
        # Flag the config column as modified so SQLAlchemy detects the change
        flag_modified(integration, "config")
        
        db.commit()
        db.refresh(integration)
        
        return {
            "status": "success",
            "message": f"Service mapping added: {mapping.service_name} -> {mapping.repo_name}",
            "service_mappings": integration.config.get("service_mappings", {})
        }
    
    @staticmethod
    def update_service_mappings(integration_id: int, update: ServiceMappingsUpdateRequest, request: Request, db: Session):
        """Update all service-to-repo mappings at once."""
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()
        
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        
        if integration.provider != "GITHUB":
            raise HTTPException(status_code=400, detail="Service mappings are only supported for GitHub integrations")
        
        # Initialize config if needed
        if not integration.config:
            integration.config = {}
        
        # Update service mappings
        integration.config["service_mappings"] = update.service_mappings
        
        # Update default repo if provided
        if update.default_repo:
            integration.config["repo_name"] = update.default_repo
        
        integration.updated_at = datetime.utcnow()
        
        # Flag the config column as modified so SQLAlchemy detects the change
        flag_modified(integration, "config")
        
        db.commit()
        db.refresh(integration)
        
        return {
            "status": "success",
            "message": "Service mappings updated",
            "service_mappings": integration.config.get("service_mappings", {}),
            "default_repo": integration.config.get("repo_name")
        }
    
    @staticmethod
    def remove_service_mapping(integration_id: int, service_name: str, request: Request, db: Session):
        """Remove a service-to-repo mapping."""
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()
        
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        
        if not integration.config or "service_mappings" not in integration.config:
            raise HTTPException(status_code=404, detail="Service mapping not found")
        
        if service_name not in integration.config["service_mappings"]:
            raise HTTPException(status_code=404, detail="Service mapping not found")
        
        # Remove the mapping
        del integration.config["service_mappings"][service_name]
        integration.updated_at = datetime.utcnow()
        
        # Flag the config column as modified so SQLAlchemy detects the change
        flag_modified(integration, "config")
        
        db.commit()
        
        return {
            "status": "success",
            "message": f"Service mapping removed: {service_name}",
            "service_mappings": integration.config.get("service_mappings", {})
        }
    
    @staticmethod
    async def github_webhook(request: Request, db: Session):
        """
        Handle GitHub webhook events, particularly PR events from Alex.
        Triggers QA review when Alex creates or updates a PR.
        """
        try:
            import hmac
            import hashlib
            
            # Read body
            body_bytes = await request.body()
            body_str = body_bytes.decode('utf-8')
            
            # Verify GitHub webhook signature (optional but recommended)
            github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
            if github_webhook_secret:
                signature_header = request.headers.get("X-Hub-Signature-256")
                if signature_header:
                    expected_signature = hmac.new(
                        github_webhook_secret.encode(),
                        body_bytes,
                        hashlib.sha256
                    ).hexdigest()
                    actual_signature = signature_header.replace("sha256=", "")
                    if not hmac.compare_digest(expected_signature, actual_signature):
                        print("⚠️  GitHub webhook signature verification failed")
                        raise HTTPException(status_code=401, detail="Invalid signature")
            
            # Parse webhook payload
            try:
                payload = json.loads(body_str)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON payload")
            
            event_type = request.headers.get("X-GitHub-Event")
            print(f"📥 Received GitHub webhook: event_type={event_type}")
            
            # Handle pull request events
            if event_type == "pull_request":
                action = payload.get("action")
                pr_data = payload.get("pull_request", {})
                
                if action in ["opened", "synchronize"]:  # PR created or updated
                    pr_number = pr_data.get("number")
                    repo_name = payload.get("repository", {}).get("full_name")
                    
                    print(f"🔔 PR #{pr_number} {action} in {repo_name}")
                    
                    # Check if PR was created by Alex using database tracking
                    # Since Alex doesn't have a GitHub account, PRs are created by HealOps app
                    # We track this in the AgentPR table
                    agent_pr = db.query(AgentPR).filter(
                        AgentPR.pr_number == pr_number,
                        AgentPR.repo_name == repo_name
                    ).first()
                    
                    if not agent_pr:
                        print(f"   PR #{pr_number} is not tracked as created by Alex. Skipping review.")
                        return {"status": "ok", "message": "PR not tracked as created by Alex, skipping review"}
                    
                    # Verify it's by Alex
                    alex_agent = db.query(AgentEmployee).filter(
                        AgentEmployee.id == agent_pr.agent_employee_id,
                        AgentEmployee.email == "alexandra.chen@healops.work"
                    ).first()
                    
                    if not alex_agent:
                        print(f"   PR #{pr_number} is not by Alex. Skipping review.")
                        return {"status": "ok", "message": "PR not by Alex, skipping review"}
                    
                    print(f"✅ PR #{pr_number} confirmed to be created by {alex_agent.name}")
                    
                    # Find integration for this repository
                    integration = db.query(Integration).filter(
                        Integration.provider == "GITHUB",
                        Integration.status == "ACTIVE"
                    ).first()
                    
                    if not integration:
                        print("⚠️  No active GitHub integration found")
                        return {"status": "ok", "message": "No active GitHub integration"}
                    
                    # Trigger QA review asynchronously
                    print(f"🚀 Triggering QA review for PR #{pr_number} by {alex_agent.name}")
                    from src.agents.qa_orchestrator import review_pr_for_alex
                    import asyncio
                    
                    # Run review in background
                    asyncio.create_task(
                        review_pr_for_alex(
                            repo_name=repo_name,
                            pr_number=pr_number,
                            integration_id=integration.id,
                            user_id=integration.user_id,
                            db=db
                        )
                    )
                    
                    return {
                        "status": "ok",
                        "message": f"QA review triggered for PR #{pr_number}",
                        "pr_number": pr_number,
                        "repo": repo_name
                    }
            
            # Handle push events - trigger reindexing for connected repositories
            elif event_type == "push":
                repo_name = payload.get("repository", {}).get("full_name")
                ref = payload.get("ref", "").replace("refs/heads/", "")
                commits = payload.get("commits", [])
                
                if not repo_name:
                    print("⚠️  Push event received but no repository name found")
                    return {"status": "ok", "message": "No repository name in push event"}
                
                print(f"📦 Push event received for {repo_name} on branch {ref}")
                print(f"   Commits: {len(commits)}")
                
                # Find all active GitHub integrations that match this repository
                active_integrations = db.query(Integration).filter(
                    Integration.provider == "GITHUB",
                    Integration.status == "ACTIVE"
                ).all()
                
                matching_integrations = []
                
                for integration in active_integrations:
                    # Check if this repo matches the integration
                    config = integration.config or {}
                    
                    # Check default repo
                    default_repo = config.get("repo_name") or config.get("repository")
                    if default_repo == repo_name:
                        matching_integrations.append((integration, default_repo))
                        continue
                    
                    # Check project_id
                    if integration.project_id == repo_name:
                        matching_integrations.append((integration, integration.project_id))
                        continue
                    
                    # Check service mappings
                    service_mappings = config.get("service_mappings", {})
                    if isinstance(service_mappings, dict):
                        for service_name, mapped_repo in service_mappings.items():
                            if mapped_repo == repo_name:
                                matching_integrations.append((integration, mapped_repo))
                                break
                
                if not matching_integrations:
                    print(f"   No active integrations found for repository {repo_name}")
                    return {
                        "status": "ok",
                        "message": f"No active integrations found for {repo_name}",
                        "repo": repo_name
                    }
                
                # Only index main/master branches by default (can be configured)
                branches_to_index = ["main", "master"]
                if ref not in branches_to_index:
                    print(f"   Branch {ref} is not in indexable branches {branches_to_index}, skipping")
                    return {
                        "status": "ok",
                        "message": f"Branch {ref} not configured for indexing",
                        "repo": repo_name,
                        "branch": ref
                    }
                
                # Schedule reindexing for each matching integration
                indexed_count = 0
                for integration, matched_repo in matching_integrations:
                    try:
                        print(f"   Scheduling reindex for integration {integration.id} ({integration.name})")
                        # Schedule reindex (non-blocking, uses asyncio.create_task internally)
                        await indexing_manager.schedule_reindex(
                            repo_name=repo_name,
                            integration_id=integration.id,
                            ref=ref
                        )
                        indexed_count += 1
                    except Exception as e:
                        print(f"   ⚠️  Failed to schedule reindex for integration {integration.id}: {e}")
                        import traceback
                        traceback.print_exc()
                
                return {
                    "status": "ok",
                    "message": f"Reindexing scheduled for {indexed_count} integration(s)",
                    "repo": repo_name,
                    "branch": ref,
                    "integrations_count": indexed_count
                }
            
            # Handle ping event (webhook setup)
            elif event_type == "ping":
                print("✅ GitHub webhook ping received - webhook is configured correctly")
                return {"status": "ok", "message": "Webhook is active"}
            
            return {"status": "ok", "message": f"Event {event_type} received but not handled"}
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Error handling GitHub webhook: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error processing webhook: {str(e)}")
    
    @staticmethod
    def list_repositories(integration_id: int, request: Request, db: Session):
        """Get list of repositories accessible by the GitHub integration."""
        # Get user_id from request if available, otherwise default to 1
        user_id = get_user_id_from_request(request, db=db)
        
        try:
            integration = db.query(Integration).filter(
                Integration.id == integration_id,
                Integration.user_id == user_id
            ).first()
            
            if not integration:
                print(f"DEBUG: Integration {integration_id} not found for user {user_id}")
                raise HTTPException(status_code=404, detail="Integration not found")
            
            if integration.provider != "GITHUB":
                raise HTTPException(status_code=400, detail="This endpoint is only for GitHub integrations")
            
            print(f"DEBUG: Found integration {integration_id}, provider: {integration.provider}")
            
            # Check if this is a GitHub App installation
            if integration.installation_id:
                print("DEBUG: Using GitHub App installation")
                repos_data = get_installation_repositories(integration.installation_id)
                repos = [
                    {
                        "full_name": repo.get("full_name"),
                        "name": repo.get("name"),
                        "private": repo.get("private", False)
                    }
                    for repo in repos_data
                ]
                print(f"DEBUG: Found {len(repos)} repositories from GitHub App installation")
                return {
                    "repositories": repos
                }
            else:
                # Legacy OAuth token flow
                github_integration = GithubIntegration(integration_id=integration.id)
                
                # Get user's repositories
                if not github_integration.client:
                    print("DEBUG: GitHub client is None")
                    return {"repositories": []}
                
                print("DEBUG: Fetching repositories from GitHub (OAuth)...")
                user = github_integration.client.get_user()
                repos = []
                
                # Get user's repos (limit to 100 for performance)
                repo_list = list(user.get_repos(type="all", sort="updated")[:100])
                print(f"DEBUG: Found {len(repo_list)} repositories from GitHub")
                
                for repo in repo_list:
                    repos.append({
                        "full_name": repo.full_name,
                        "name": repo.name,
                        "private": repo.private
                    })
                    print(f"DEBUG: Added repo: {repo.full_name}")
                
                print(f"DEBUG: Returning {len(repos)} repositories")
                return {
                    "repositories": repos
                }
        except HTTPException:
            raise
        except Exception as e:
            print(f"ERROR fetching repositories: {e}")
            import traceback
            traceback.print_exc()
            return {
                "repositories": [],
                "error": str(e)
            }
    
    @staticmethod
    def update_integration(integration_id: int, update_data: dict, request: Request, db: Session):
        """
        Update integration configuration (default repo, service mappings, etc.).
        This endpoint allows editing the integration after initial connection.
        """
        user_id = get_user_id_from_request(request, db=db)

        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()

        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        # Update allowed fields
        if "name" in update_data:
            integration.name = update_data["name"]

        if "default_repo" in update_data or "repository" in update_data:
            # Initialize config if needed
            if not integration.config:
                integration.config = {}

            # Set default repository
            default_repo = update_data.get("default_repo") or update_data.get("repository")
            integration.config["repo_name"] = default_repo
            integration.project_id = default_repo  # Also set project_id for backward compatibility

        if "service_mappings" in update_data:
            # Initialize config if needed
            if not integration.config:
                integration.config = {}

            integration.config["service_mappings"] = update_data["service_mappings"]

        # Update status if provided
        if "status" in update_data:
            integration.status = update_data["status"]

        integration.updated_at = datetime.utcnow()

        # Flag config as modified for SQLAlchemy
        if integration.config:
            flag_modified(integration, "config")

        db.commit()
        db.refresh(integration)

        # Backfill integration to incidents if repo was set
        if integration.config and integration.config.get("repo_name"):
            backfill_integration_to_incidents(db, integration.id, user_id, integration.config)

        return {
            "status": "success",
            "message": "Integration updated successfully",
            "integration": {
                "id": integration.id,
                "provider": integration.provider,
                "name": integration.name,
                "status": integration.status,
                "default_repo": integration.config.get("repo_name") if integration.config else None,
                "service_mappings": integration.config.get("service_mappings", {}) if integration.config else {}
            }
        }
    
    @staticmethod
    def complete_integration_setup(integration_id: int, setup_data: dict, request: Request, db: Session):
        """
        Complete the initial setup of a GitHub integration.
        Called after OAuth connection to set default repository and optionally service mappings.

        Expected setup_data:
        {
            "default_repo": "owner/repo-name",
            "service_mappings": {
                "service1": "owner/repo1",
                "service2": "owner/repo2"
            }
        }
        """
        user_id = get_user_id_from_request(request, db=db)

        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()

        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        if integration.provider != "GITHUB":
            raise HTTPException(
                status_code=400,
                detail="Setup is only supported for GitHub integrations"
            )

        # Validate that we have at least a default repo
        default_repo = setup_data.get("default_repo") or setup_data.get("repository")
        if not default_repo:
            raise HTTPException(
                status_code=400,
                detail="default_repo is required to complete setup"
            )

        # Initialize config if needed
        if not integration.config:
            integration.config = {}

        # Set default repository
        integration.config["repo_name"] = default_repo
        integration.project_id = default_repo

        # Set service mappings if provided
        if "service_mappings" in setup_data:
            integration.config["service_mappings"] = setup_data["service_mappings"]
        elif "service_mappings" not in integration.config:
            integration.config["service_mappings"] = {}

        # Mark integration as active (setup complete)
        integration.status = "ACTIVE"
        integration.updated_at = datetime.utcnow()

        # Flag config as modified
        flag_modified(integration, "config")

        db.commit()
        db.refresh(integration)

        # Backfill integration to existing incidents
        backfill_integration_to_incidents(db, integration.id, user_id, integration.config)

        return {
            "status": "success",
            "message": "Integration setup completed successfully",
            "integration": {
                "id": integration.id,
                "provider": integration.provider,
                "name": integration.name,
                "status": integration.status,
                "default_repo": integration.config.get("repo_name"),
                "service_mappings": integration.config.get("service_mappings", {})
            }
        }
    
    @staticmethod
    def complete_integration_setup_with_indexing(integration_id: int, setup_data: dict, request: Request, background_tasks: BackgroundTasks, db: Session):
        """
        Complete the initial setup of a GitHub integration and trigger CocoIndex indexing.
        This is the version that includes background indexing.
        """
        # First complete the setup
        result = IntegrationsController.complete_integration_setup(integration_id, setup_data, request, db)
        
        # Get the integration again to ensure we have the latest data
        integration = db.query(Integration).filter(Integration.id == integration_id).first()
        if not integration:
            return result
        
        # Trigger CocoIndex repository indexing in background (async, non-blocking)
        default_repo = setup_data.get("default_repo") or setup_data.get("repository")
        if default_repo and integration.status == "ACTIVE":
            try:
                from src.memory.cocoindex_flow import execute_flow_update
                
                def index_repository_background():
                    """Background task to index repository with CocoIndex."""
                    try:
                        print(f"🔄 Starting CocoIndex indexing for repository: {default_repo}")
                        execute_flow_update(
                            repo_name=default_repo,
                            integration_id=integration.id,
                            ref="main"
                        )
                        print(f"✅ CocoIndex indexing completed for repository: {default_repo}")
                    except Exception as e:
                        print(f"⚠️  CocoIndex indexing failed for {default_repo}: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Add to FastAPI background tasks (non-blocking)
                background_tasks.add_task(index_repository_background)
            except Exception as e:
                # Don't fail setup if indexing trigger fails
                print(f"Warning: Failed to trigger CocoIndex indexing: {e}")
                import traceback
                traceback.print_exc()
        
        return result
    
    @staticmethod
    def get_integration_details(integration_id: int, request: Request, db: Session):
        """Get detailed information about a specific integration."""
        user_id = get_user_id_from_request(request, db=db)

        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.user_id == user_id
        ).first()

        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        config = integration.config or {}

        return {
            "id": integration.id,
            "provider": integration.provider,
            "name": integration.name,
            "status": integration.status,
            "default_repo": config.get("repo_name") or integration.project_id,
            "service_mappings": config.get("service_mappings", {}),
            "created_at": integration.created_at.isoformat() if integration.created_at else None,
            "updated_at": integration.updated_at.isoformat() if integration.updated_at else None,
            "last_verified": integration.last_verified.isoformat() if integration.last_verified else None
        }
