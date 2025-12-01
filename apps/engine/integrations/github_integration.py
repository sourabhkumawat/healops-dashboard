"""
GitHub integration for PR creation and code management.
"""
from typing import Dict, Any, Optional
import os
from github import Github, GithubException
from database import SessionLocal
from models import Integration

class GithubIntegration:
    """Handles GitHub interactions."""
    
    def __init__(self, access_token: Optional[str] = None, integration_id: Optional[int] = None):
        self.access_token = access_token
        if not self.access_token and integration_id:
            # Fetch from DB
            db = SessionLocal()
            try:
                integration = db.query(Integration).filter(Integration.id == integration_id).first()
                if integration and integration.access_token:
                    from crypto_utils import decrypt_token
                    try:
                        self.access_token = decrypt_token(integration.access_token)
                    except Exception:
                        # Fallback for legacy plain text tokens
                        self.access_token = integration.access_token
            finally:
                db.close()
                
        self.client = Github(self.access_token) if self.access_token else None
    
    def verify_connection(self) -> Dict[str, Any]:
        """Verify GitHub token validity."""
        if not self.client:
            return {"status": "error", "message": "No access token provided"}
            
        try:
            user = self.client.get_user()
            return {
                "status": "verified",
                "username": user.login,
                "name": user.name,
                "email": user.email
            }
        except GithubException as e:
            return {"status": "error", "message": str(e)}

    def create_pr(self, repo_name: str, title: str, body: str, head_branch: str, base_branch: str = "main", changes: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Create a Pull Request with changes.
        
        Args:
            repo_name: "owner/repo"
            title: PR Title
            body: PR Description
            head_branch: Name of the new branch
            base_branch: Target branch (default: main)
            changes: Dict of {file_path: new_content}
            
        Returns:
            PR details
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
            
        try:
            repo = self.client.get_repo(repo_name)
            
            # Get base branch SHA
            sb = repo.get_branch(base_branch)
            
            # Create new branch
            try:
                repo.create_git_ref(ref=f"refs/heads/{head_branch}", sha=sb.commit.sha)
            except GithubException:
                # Branch might already exist
                pass
                
            # Commit changes
            if changes:
                for file_path, content in changes.items():
                    try:
                        contents = repo.get_contents(file_path, ref=head_branch)
                        repo.update_file(contents.path, f"Update {file_path}", content, contents.sha, branch=head_branch)
                    except GithubException:
                        # File doesn't exist, create it
                        repo.create_file(file_path, f"Create {file_path}", content, branch=head_branch)
            
            # Create PR
            pr = repo.create_pull(title=title, body=body, head=head_branch, base=base_branch)
            
            return {
                "status": "success",
                "pr_url": pr.html_url,
                "pr_number": pr.number
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}





