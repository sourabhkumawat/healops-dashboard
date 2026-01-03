"""
GitHub integration for PR creation and code management.
Supports both OAuth tokens (legacy) and GitHub App installations.
"""
from typing import Dict, Any, Optional
import os
from datetime import datetime, timedelta
from github import Github, GithubException
from database import SessionLocal
from models import Integration
from integrations.github_app_auth import get_installation_token

class GithubIntegration:
    """Handles GitHub interactions."""
    
    def __init__(self, access_token: Optional[str] = None, integration_id: Optional[int] = None):
        self.access_token = access_token
        self.installation_id = None
        self._cached_token = None
        self._token_expires_at = None
        
        if not self.access_token and integration_id:
            # Fetch from DB
            db = SessionLocal()
            try:
                integration = db.query(Integration).filter(Integration.id == integration_id).first()
                if integration:
                    # Check for GitHub App installation_id first
                    if integration.installation_id:
                        self.installation_id = integration.installation_id
                        # Installation tokens are generated on-demand, not stored
                    elif integration.access_token:
                        # Fall back to OAuth token (legacy)
                        from crypto_utils import decrypt_token
                        try:
                            self.access_token = decrypt_token(integration.access_token)
                        except Exception:
                            # Fallback for legacy plain text tokens
                            self.access_token = integration.access_token
            finally:
                db.close()
        
        # Initialize client - will be set when token is available
        self.client = None
        self._ensure_client()
    
    def _ensure_client(self):
        """Ensure GitHub client is initialized with a valid token."""
        if self.client:
            return
        
        if self.installation_id:
            # Generate or use cached installation token
            token = self._get_installation_token()
            if token:
                self.client = Github(token)
        elif self.access_token:
            # Use OAuth token (legacy)
            self.client = Github(self.access_token)
    
    def _get_installation_token(self) -> Optional[str]:
        """Get installation token, using cache if valid."""
        # Check if we have a valid cached token
        if self._cached_token and self._token_expires_at:
            if datetime.utcnow() < self._token_expires_at - timedelta(minutes=5):  # Refresh 5 min before expiry
                return self._cached_token
        
        # Generate new installation token
        if not self.installation_id:
            return None
        
        token_data = get_installation_token(self.installation_id)
        if token_data and token_data.get("token"):
            self._cached_token = token_data["token"]
            self._token_expires_at = token_data.get("expires_at")
            return self._cached_token
        
        return None
    
    def verify_connection(self) -> Dict[str, Any]:
        """Verify GitHub token validity."""
        self._ensure_client()
        if not self.client:
            return {"status": "error", "message": "No access token or installation ID provided"}
            
        try:
            # For GitHub Apps, get_user() returns the app, not the installation account
            # Try to get installation info instead
            if self.installation_id:
                from integrations.github_app_auth import get_installation_info
                installation_info = get_installation_info(self.installation_id)
                if installation_info:
                    account = installation_info.get("account", {})
                    return {
                        "status": "verified",
                        "username": account.get("login", "GitHub App Installation"),
                        "name": account.get("login"),
                        "email": None,  # GitHub Apps don't have email
                        "installation_id": self.installation_id,
                        "account_type": installation_info.get("account", {}).get("type", "User")
                    }
                else:
                    return {"status": "error", "message": "Failed to get installation info"}
            else:
                # OAuth token (legacy)
                user = self.client.get_user()
                return {
                    "status": "verified",
                    "username": user.login,
                    "name": user.name,
                    "email": user.email
                }
        except GithubException as e:
            return {"status": "error", "message": str(e)}

    def get_repo_info(self, repo_name: str) -> Dict[str, Any]:
        """
        Get repository information.
        
        Args:
            repo_name: "owner/repo"
            
        Returns:
            Repository information
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
            
        try:
            repo = self.client.get_repo(repo_name)
            return {
                "status": "success",
                "name": repo.full_name,
                "default_branch": repo.default_branch,
                "description": repo.description,
                "url": repo.html_url
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_file_contents(self, repo_name: str, file_path: str, ref: str = "main") -> Optional[str]:
        """
        Get file contents from repository.
        
        Args:
            repo_name: "owner/repo"
            file_path: Path to file in repo
            ref: Branch or commit SHA
            
        Returns:
            File contents as string, or None if error
        """
        if not self.client:
            return None
            
        try:
            repo = self.client.get_repo(repo_name)
            contents = repo.get_contents(file_path, ref=ref)
            if contents.encoding == "base64":
                import base64
                return base64.b64decode(contents.content).decode("utf-8")
            return contents.decoded_content.decode("utf-8")
        except Exception as e:
            print(f"Error fetching file {file_path}: {e}")
            return None
    
    def search_code(self, repo_name: str, query: str, language: Optional[str] = None) -> list[Dict[str, Any]]:
        """
        Search for code in repository.
        
        Args:
            repo_name: "owner/repo"
            query: Search query
            language: Optional language filter (e.g., "python", "javascript")
            
        Returns:
            List of matching files with snippets
        """
        if not self.client:
            return []
            
        try:
            repo = self.client.get_repo(repo_name)
            search_query = f"{query} repo:{repo_name}"
            if language:
                search_query += f" language:{language}"
            
            results = self.client.search_code(search_query)
            matches = []
            
            # Handle pagination and empty results
            try:
                result_list = list(results[:10])  # Limit to 10 results
                for content_file in result_list:
                    matches.append({
                        "path": content_file.path,
                        "name": content_file.name,
                        "url": content_file.html_url,
                        "repository": content_file.repository.full_name
                    })
            except Exception as e:
                print(f"Error iterating search results: {e}")
                
            return matches
        except Exception as e:
            print(f"Error searching code: {e}")
            return []
    
    def get_repo_structure(self, repo_name: str, path: str = "", ref: str = "main", max_depth: int = 2) -> list[str]:
        """
        Get repository file structure.
        
        Args:
            repo_name: "owner/repo"
            path: Starting path (empty for root)
            ref: Branch or commit SHA
            max_depth: Maximum depth to traverse
            
        Returns:
            List of file paths
        """
        if not self.client or max_depth <= 0:
            return []
            
        try:
            repo = self.client.get_repo(repo_name)
            contents = repo.get_contents(path, ref=ref)
            files = []
            
            if isinstance(contents, list):
                for item in contents:
                    if item.type == "file":
                        files.append(item.path)
                    elif item.type == "dir":
                        # Recursively get subdirectory contents
                        files.extend(self.get_repo_structure(repo_name, item.path, ref, max_depth - 1))
            else:
                if contents.type == "file":
                    files.append(contents.path)
            
            return files
        except Exception as e:
            print(f"Error getting repo structure: {e}")
            return []

    def create_pr(self, repo_name: str, title: str, body: str, head_branch: str, base_branch: str = "main", changes: Dict[str, str] = None, draft: bool = False) -> Dict[str, Any]:
        """
        Create a Pull Request with changes.
        
        Args:
            repo_name: "owner/repo"
            title: PR Title
            body: PR Description
            head_branch: Name of the new branch
            base_branch: Target branch (default: main)
            changes: Dict of {file_path: new_content}
            draft: If True, create as draft PR
            
        Returns:
            PR details
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
            
        try:
            repo = self.client.get_repo(repo_name)
            
            # Get base branch SHA
            sb = repo.get_branch(base_branch)
            base_sha = sb.commit.sha
            
            # Create new branch
            try:
                repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=base_sha)
            except GithubException as e:
                # Branch might already exist, try to delete and recreate
                if "already exists" in str(e).lower():
                    try:
                        ref = repo.get_git_ref(f"heads/{head_branch}")
                        ref.delete()
                        repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=base_sha)
                    except:
                        return {"status": "error", "message": f"Could not create branch: {e}"}
                else:
                    return {"status": "error", "message": str(e)}
                
            # Commit changes
            if changes:
                for file_path, content in changes.items():
                    try:
                        contents = repo.get_contents(file_path, ref=head_branch)
                        repo.update_file(contents.path, f"Fix: {file_path}", content, contents.sha, branch=head_branch)
                    except GithubException:
                        # File doesn't exist, create it
                        repo.create_file(file_path, f"Create: {file_path}", content, branch=head_branch)
            
            # Create PR (draft or regular)
            pr = repo.create_pull(title=title, body=body, head=head_branch, base=base_branch, draft=draft)
            
            return {
                "status": "success",
                "pr_url": pr.html_url,
                "pr_number": pr.number,
                "draft": draft
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}






