"""
GitHub integration for PR creation and code management.
Supports both OAuth tokens (legacy) and GitHub App installations.
"""
from typing import Dict, Any, Optional, List
import os
from datetime import datetime, timedelta
from github import Github, GithubException
from src.database.database import SessionLocal
from src.database.models import Integration
from src.integrations.github.app_auth import get_installation_token
import hashlib

# Simple in-memory cache for repository structure (key: (repo_name, ref, max_depth), value: (files, timestamp))
_repo_structure_cache: Dict[str, tuple] = {}
CACHE_TTL_SECONDS = 300  # Cache for 5 minutes

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
                        from src.auth.crypto_utils import decrypt_token
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
        
        print(f"   🔧 Initializing GitHub client...")
        print(f"      installation_id: {self.installation_id}")
        print(f"      access_token: {'***' if self.access_token else None}")
        
        if self.installation_id:
            # Generate or use cached installation token
            print(f"      Attempting to get installation token for ID: {self.installation_id}")
            token = self._get_installation_token()
            if token:
                print(f"      ✅ Installation token obtained")
                self.client = Github(token)
                print(f"      ✅ GitHub client initialized with installation token")
            else:
                print(f"      ❌ Failed to get installation token")
        elif self.access_token:
            # Use OAuth token (legacy)
            print(f"      Using OAuth token (legacy)")
            self.client = Github(self.access_token)
            print(f"      ✅ GitHub client initialized with OAuth token")
        else:
            print(f"      ❌ No installation_id or access_token available")
    
    def _get_installation_token(self) -> Optional[str]:
        """Get installation token, using cache if valid."""
        # Check if we have a valid cached token
        if self._cached_token and self._token_expires_at:
            if datetime.utcnow() < self._token_expires_at - timedelta(minutes=5):  # Refresh 5 min before expiry
                print(f"         Using cached installation token")
                return self._cached_token
        
        # Generate new installation token
        if not self.installation_id:
            print(f"         ❌ No installation_id set")
            return None
        
        print(f"         Generating new installation token...")
        try:
            token_data = get_installation_token(self.installation_id)
            if token_data and token_data.get("token"):
                self._cached_token = token_data["token"]
                self._token_expires_at = token_data.get("expires_at")
                print(f"         ✅ Installation token generated successfully")
                return self._cached_token
            else:
                print(f"         ❌ Token data is None or missing token field")
                print(f"            token_data: {token_data}")
        except Exception as e:
            print(f"         ❌ Exception getting installation token: {e}")
            import traceback
            traceback.print_exc()
        
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
                from src.integrations.github.app_auth import get_installation_info
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
    
    def create_or_update_file(self, repo_name: str, file_path: str, content: str, commit_message: str, branch: str = "main") -> Dict[str, Any]:
        """
        Create or update a file in the repository.
        
        Args:
            repo_name: Repository name in format "owner/repo"
            file_path: Path to the file
            content: File content
            commit_message: Commit message
            branch: Branch name (default: "main")
            
        Returns:
            Dictionary with status and message
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
        
        try:
            self._ensure_client()
            repo = self.client.get_repo(repo_name)
            
            try:
                # Try to get existing file
                contents = repo.get_contents(file_path, ref=branch)
                # File exists, update it
                repo.update_file(contents.path, commit_message, content, contents.sha, branch=branch)
                return {"status": "success", "message": f"Updated {file_path}", "action": "updated"}
            except GithubException as e:
                if e.status == 404:
                    # File doesn't exist, create it
                    repo.create_file(file_path, commit_message, content, branch=branch)
                    return {"status": "success", "message": f"Created {file_path}", "action": "created"}
                else:
                    return {"status": "error", "message": str(e)}
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
        
        # Skip Node.js internal modules and other system files
        if file_path.startswith("node:internal/") or file_path.startswith("node:"):
            return None
        
        try:
            repo = self.client.get_repo(repo_name)
            contents = repo.get_contents(file_path, ref=ref)
            if contents.encoding == "base64":
                import base64
                return base64.b64decode(contents.content).decode("utf-8")
            return contents.decoded_content.decode("utf-8")
        except Exception as e:
            # Handle 404 errors gracefully (file doesn't exist)
            error_str = str(e).lower()
            is_404 = "404" in error_str or "not found" in error_str
            
            # Only log non-404 errors or 404s for files that should exist
            # Skip logging for Node.js internals and other expected missing files
            if not is_404 or (is_404 and not file_path.startswith("node:")):
                # Check if this is a GitHub API 404 error
                if hasattr(e, 'status') and e.status == 404:
                    # File doesn't exist - this is expected for some files
                    # Only log at debug level
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.debug(f"File not found in repository: {file_path} (expected for some files)")
                else:
                    # Other errors should be logged
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Error fetching file {file_path}: {e}")
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
        Get repository file structure efficiently using GitHub tree API when possible.
        Includes caching to avoid re-fetching the same structure.
        
        Args:
            repo_name: "owner/repo"
            path: Starting path (empty for root)
            ref: Branch or commit SHA
            max_depth: Maximum depth to traverse
            
        Returns:
            List of file paths
        """
        if not self.client:
            print(f"Error: GitHub client not initialized in get_repo_structure")
            return []
        
        if max_depth <= 0:
            return []
        
        # Check cache (only for root path to keep cache simple)
        if path == "":
            cache_key = self._get_cache_key(repo_name, ref, max_depth)
            if cache_key in _repo_structure_cache:
                cached_files, cache_time = _repo_structure_cache[cache_key]
                age_seconds = (datetime.utcnow() - cache_time).total_seconds()
                if age_seconds < CACHE_TTL_SECONDS:
                    print(f"   ✓ Using cached repository structure ({len(cached_files)} files, cached {age_seconds:.1f}s ago)")
                    return cached_files.copy()  # Return a copy to avoid mutation
                else:
                    # Cache expired
                    del _repo_structure_cache[cache_key]
        
        # Directories to skip (common build/cache directories)
        skip_dirs = {
            'node_modules', '__pycache__', '.git', 'venv', 'env', '.venv', 
            'dist', 'build', '.next', '.nuxt', 'target', 'bin', 'obj',
            '.gradle', '.idea', '.vscode', 'coverage', '.pytest_cache'
        }
        
        try:
            repo = self.client.get_repo(repo_name)
            files = []
            
            # For root path and max_depth >= 2, use tree API for better performance
            if path == "" and max_depth >= 2:
                try:
                    files = self._get_repo_structure_via_tree(repo, ref, max_depth, skip_dirs)
                except Exception as tree_error:
                    print(f"   Tree API failed, falling back to recursive method: {tree_error}")
                    # Fall through to recursive method
                    files = self._get_repo_structure_recursive(repo, repo_name, path, ref, max_depth, skip_dirs)
            else:
                # Recursive method (original implementation) as fallback
                files = self._get_repo_structure_recursive(repo, repo_name, path, ref, max_depth, skip_dirs)
            
            # Cache the result (only for root path)
            if path == "":
                cache_key = self._get_cache_key(repo_name, ref, max_depth)
                _repo_structure_cache[cache_key] = (files.copy(), datetime.utcnow())
                # Limit cache size to prevent memory issues (keep last 50 entries)
                if len(_repo_structure_cache) > 50:
                    # Remove oldest entry
                    oldest_key = min(_repo_structure_cache.keys(), 
                                   key=lambda k: _repo_structure_cache[k][1])
                    del _repo_structure_cache[oldest_key]
            
            return files
            
        except GithubException as e:
            print(f"Error getting repo structure: {e} (status: {e.status})")
            if e.status == 404:
                print(f"   Path '{path}' not found in repository '{repo_name}' on branch '{ref}'")
            return []
        except Exception as e:
            print(f"Error getting repo structure: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _get_cache_key(self, repo_name: str, ref: str, max_depth: int) -> str:
        """Generate a cache key for repository structure."""
        key_str = f"{repo_name}:{ref}:{max_depth}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _get_repo_structure_via_tree(self, repo, ref: str, max_depth: int, skip_dirs: set) -> list[str]:
        """
        Get repository structure using GitHub tree API (much faster for large repos).
        Gets entire tree in 1-2 API calls instead of hundreds.
        """
        try:
            # Get the branch/commit SHA
            try:
                branch = repo.get_branch(ref)
                commit_sha = branch.commit.sha
            except:
                # Try as commit SHA directly
                commit_sha = ref
            
            # Use direct API call for tree (PyGithub doesn't support recursive trees well)
            # Get the token from the client
            token = None
            if self._cached_token:
                token = self._cached_token
            elif self.access_token:
                token = self.access_token
            else:
                # Try to get token from installation
                token = self._get_installation_token()
            
            if not token:
                raise Exception("No authentication token available")
            
            # Get the tree recursively via direct API call
            tree_url = f"https://api.github.com/repos/{repo.full_name}/git/trees/{commit_sha}?recursive=1"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            import requests
            print(f"   📥 Fetching repository tree via API (this may take a moment for large repos)...")
            response = requests.get(tree_url, headers=headers, timeout=60)
            
            if response.status_code == 200:
                tree_data = response.json()
                
                # Check if tree was truncated (GitHub limits to 100,000 entries)
                truncated = tree_data.get("truncated", False)
                if truncated:
                    print(f"   ⚠️  Tree was truncated (repo too large). Using recursive fallback method.")
                    raise Exception("Tree truncated - repository too large for single API call")
                
                all_paths = []
                tree_items = tree_data.get("tree", [])
                print(f"   📊 Processing {len(tree_items)} tree items...")
                
                for item in tree_items:
                    item_path = item.get("path", "")
                    item_type = item.get("type", "")
                    
                    # Only include files (not directories)
                    if item_type == "blob":  # blob = file in Git
                        # Check depth
                        depth = item_path.count("/")
                        if depth < max_depth:
                            # Skip files in ignored directories
                            path_parts = item_path.split("/")
                            if not any(part in skip_dirs for part in path_parts):
                                all_paths.append(item_path)
                
                print(f"   ✓ Retrieved {len(all_paths)} files via tree API (filtered from {len(tree_items)} items)")
                return sorted(all_paths)
            elif response.status_code == 404:
                raise Exception(f"Commit {commit_sha} not found")
            else:
                raise Exception(f"Tree API returned status {response.status_code}: {response.text[:200]}")
                
        except Exception as e:
            print(f"   Tree API error: {e}, falling back to recursive method")
            raise
    
    def _get_repo_structure_recursive(self, repo, repo_name: str, path: str, ref: str, max_depth: int, skip_dirs: set) -> list[str]:
        """
        Original recursive method (fallback when tree API doesn't work).
        Optimized to skip irrelevant directories early.
        """
        if max_depth <= 0:
            return []
        
        # Skip ignored directories
        if path:
            path_parts = path.split("/")
            if any(part in skip_dirs for part in path_parts):
                return []
        
        try:
            contents = repo.get_contents(path, ref=ref)
            files = []
            
            if isinstance(contents, list):
                for item in contents:
                    if item.type == "file":
                        files.append(item.path)
                    elif item.type == "dir" and max_depth > 1:
                        # Skip ignored directories
                        dir_name = item.path.split("/")[-1]
                        if dir_name not in skip_dirs:
                            sub_files = self._get_repo_structure_recursive(
                                repo, repo_name, item.path, ref, max_depth - 1, skip_dirs
                            )
                            files.extend(sub_files)
            else:
                if contents.type == "file":
                    files.append(contents.path)
                elif contents.type == "dir" and max_depth > 1:
                    dir_name = contents.path.split("/")[-1]
                    if dir_name not in skip_dirs:
                        sub_files = self._get_repo_structure_recursive(
                            repo, repo_name, contents.path, ref, max_depth - 1, skip_dirs
                        )
                        files.extend(sub_files)
            
            return files
            
        except GithubException as e:
            if e.status != 404:  # Don't print for expected 404s
                print(f"   Error getting contents for '{path}': {e.status}")
            return []
        except Exception as e:
            print(f"   Error in recursive fetch for '{path}': {e}")
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

    def get_pr_details(self, repo_name: str, pr_number: int) -> Dict[str, Any]:
        """
        Get details of a pull request.
        
        Args:
            repo_name: "owner/repo"
            pr_number: PR number
            
        Returns:
            PR details including files changed, diffs, author, etc.
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
        
        try:
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            
            # Get files changed
            files = []
            for file in pr.get_files():
                files.append({
                    "filename": file.filename,
                    "status": file.status,  # added, removed, modified, renamed
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "changes": file.changes,
                    "patch": file.patch,  # Unified diff
                    "raw_url": file.raw_url,
                    "blob_url": file.blob_url
                })
            
            # Get PR reviews (comments from reviewers)
            reviews = []
            for review in pr.get_reviews():
                reviews.append({
                    "id": review.id,
                    "user": review.user.login if review.user else None,
                    "state": review.state,  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
                    "body": review.body,
                    "commit_id": review.commit_id
                })
            
            # Get PR comments
            comments = []
            for comment in pr.get_issue_comments():
                comments.append({
                    "id": comment.id,
                    "user": comment.user.login if comment.user else None,
                    "body": comment.body,
                    "created_at": comment.created_at.isoformat() if comment.created_at else None
                })
            
            return {
                "status": "success",
                "pr_number": pr.number,
                "title": pr.title,
                "body": pr.body,
                "state": pr.state,  # open, closed
                "merged": pr.merged,
                "author": pr.user.login if pr.user else None,
                "created_at": pr.created_at.isoformat() if pr.created_at else None,
                "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
                "head_branch": pr.head.ref,
                "base_branch": pr.base.ref,
                "url": pr.html_url,
                "files": files,
                "reviews": reviews,
                "comments": comments
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_pr_file_contents(self, repo_name: str, pr_number: int, file_path: str) -> Optional[str]:
        """
        Get file contents from a PR (from the head branch).
        
        Args:
            repo_name: "owner/repo"
            pr_number: PR number
            file_path: Path to file in repo
            
        Returns:
            File contents as string, or None if error
        """
        if not self.client:
            return None
        
        try:
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            head_sha = pr.head.sha
            
            contents = repo.get_contents(file_path, ref=head_sha)
            if contents.encoding == "base64":
                import base64
                return base64.b64decode(contents.content).decode("utf-8")
            return contents.decoded_content.decode("utf-8")
        except Exception as e:
            print(f"Error fetching PR file {file_path}: {e}")
            return None
    
    def comment_on_pr(self, repo_name: str, pr_number: int, body: str, commit_id: Optional[str] = None, path: Optional[str] = None, line: Optional[int] = None, side: Optional[str] = None) -> Dict[str, Any]:
        """
        Comment on a pull request.
        Can be a general PR comment or an inline review comment.
        
        Args:
            repo_name: "owner/repo"
            pr_number: PR number
            body: Comment text
            commit_id: Optional commit SHA for inline comments
            path: Optional file path for inline comments
            line: Optional line number for inline comments
            side: Optional side ("LEFT" for original, "RIGHT" for modified) for inline comments
            
        Returns:
            Comment details if successful
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
        
        try:
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            
            # If all inline comment parameters are provided, create inline review comment
            if commit_id and path and line is not None:
                # Create review with inline comment
                review = pr.create_review(
                    body=body,
                    event="COMMENT",  # COMMENT, APPROVE, REQUEST_CHANGES
                    commit_id=commit_id
                )
                
                # Add inline comment to review
                # Note: PyGithub doesn't directly support inline comments in create_review
                # We need to use the review comment endpoint
                # For now, create a general review comment
                # Full inline comment support would require direct API calls
                
                return {
                    "status": "success",
                    "review_id": review.id,
                    "message": "Review comment created",
                    "type": "review"
                }
            else:
                # Create general PR comment
                comment = pr.create_issue_comment(body)
                return {
                    "status": "success",
                    "comment_id": comment.id,
                    "message": "Comment created",
                    "type": "general"
                }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def request_pr_changes(self, repo_name: str, pr_number: int, body: str) -> Dict[str, Any]:
        """
        Request changes on a PR (reject and ask for fixes).
        
        Args:
            repo_name: "owner/repo"
            pr_number: PR number
            body: Review comment body
            
        Returns:
            Review details if successful
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
        
        try:
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            
            # Get the latest commit SHA
            commits = list(pr.get_commits())
            if not commits:
                return {"status": "error", "message": "PR has no commits"}
            
            latest_commit = commits[-1]
            
            # Create review requesting changes
            review = pr.create_review(
                body=body,
                event="REQUEST_CHANGES",
                commit_id=latest_commit.sha
            )
            
            return {
                "status": "success",
                "review_id": review.id,
                "message": "Changes requested",
                "state": review.state
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def approve_pr(self, repo_name: str, pr_number: int, body: Optional[str] = None) -> Dict[str, Any]:
        """
        Approve a pull request.
        
        Args:
            repo_name: "owner/repo"
            pr_number: PR number
            body: Optional approval comment
            
        Returns:
            Review details if successful
        """
        if not self.client:
            return {"status": "error", "message": "Not authenticated"}
        
        try:
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            
            # Get the latest commit SHA
            commits = list(pr.get_commits())
            if not commits:
                return {"status": "error", "message": "PR has no commits"}
            
            latest_commit = commits[-1]
            
            # Create review approving PR
            review = pr.create_review(
                body=body or "✅ Approved by QA review",
                event="APPROVE",
                commit_id=latest_commit.sha
            )
            
            return {
                "status": "success",
                "review_id": review.id,
                "message": "PR approved",
                "state": review.state
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def list_prs_by_author(self, repo_name: str, author: str, state: str = "open") -> List[Dict[str, Any]]:
        """
        List pull requests by author.
        
        Args:
            repo_name: "owner/repo"
            author: GitHub username of the author
            state: PR state ("open", "closed", "all")
            
        Returns:
            List of PRs created by the author
        """
        if not self.client:
            return []
        
        try:
            repo = self.client.get_repo(repo_name)
            prs = repo.get_pulls(state=state, sort="created", direction="desc")
            
            author_prs = []
            for pr in prs:
                if pr.user and pr.user.login.lower() == author.lower():
                    author_prs.append({
                        "number": pr.number,
                        "title": pr.title,
                        "state": pr.state,
                        "url": pr.html_url,
                        "created_at": pr.created_at.isoformat() if pr.created_at else None,
                        "author": pr.user.login if pr.user else None
                    })
            
            return author_prs
        except Exception as e:
            print(f"Error listing PRs by author: {e}")
            return []






