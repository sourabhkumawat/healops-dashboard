"""
Linear integration for issue creation and management.
Uses OAuth 2.0 for authentication and GraphQL API for operations.
"""
from typing import Dict, Any, Optional, List
import os
import requests
from datetime import datetime, timedelta, timezone
from src.database.database import SessionLocal
from src.database.models import Integration
from src.integrations.linear.oauth import LINEAR_API_URL, refresh_access_token
from src.auth.crypto_utils import decrypt_token, encrypt_token


class LinearIntegration:
    """Handles Linear API interactions via GraphQL."""
    
    def __init__(self, access_token: Optional[str] = None, integration_id: Optional[int] = None):
        self.access_token = access_token
        self.refresh_token = None
        self.token_expires_at = None
        self.integration_id = integration_id
        
        if not self.access_token and integration_id:
            # Fetch from DB
            db = SessionLocal()
            try:
                integration = db.query(Integration).filter(Integration.id == integration_id).first()
                if integration and integration.provider == "LINEAR":
                    if integration.access_token:
                        try:
                            self.access_token = decrypt_token(integration.access_token)
                        except Exception:
                            # Fallback for legacy plain text tokens
                            self.access_token = integration.access_token
                    
                    if integration.refresh_token:
                        try:
                            self.refresh_token = decrypt_token(integration.refresh_token)
                        except Exception:
                            self.refresh_token = integration.refresh_token
                    
                    if integration.token_expiry:
                        self.token_expires_at = integration.token_expiry
            finally:
                db.close()
        
        # Ensure we have a valid token
        self._ensure_valid_token()
    
    def _ensure_valid_token(self):
        """Ensure access token is valid, refresh if needed."""
        if not self.access_token:
            return
        
        # Check if token is expired or will expire soon (within 5 minutes)
        if self.token_expires_at:
            if datetime.now(timezone.utc) >= self.token_expires_at - timedelta(minutes=5):
                self._refresh_token()
        else:
            # If no expiry info, try to use token (will fail if invalid)
            pass
    
    def _refresh_token(self):
        """Refresh the access token using refresh token."""
        if not self.refresh_token:
            print("⚠️  No refresh token available for Linear integration")
            return
        
        try:
            token_data = refresh_access_token(self.refresh_token)
            
            if token_data.get("access_token"):
                self.access_token = token_data["access_token"]
                
                # Update refresh token if provided
                if token_data.get("refresh_token"):
                    self.refresh_token = token_data["refresh_token"]
                
                # Calculate expiry
                expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
                self.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                
                # Update database if we have integration_id
                if self.integration_id:
                    db = SessionLocal()
                    try:
                        integration = db.query(Integration).filter(Integration.id == self.integration_id).first()
                        if integration:
                            integration.access_token = encrypt_token(self.access_token)
                            if self.refresh_token:
                                integration.refresh_token = encrypt_token(self.refresh_token)
                            integration.token_expiry = self.token_expires_at
                            db.commit()
                    finally:
                        db.close()
                
                print("✅ Linear access token refreshed successfully")
            else:
                print("⚠️  Token refresh response missing access_token")
        except Exception as e:
            print(f"❌ Error refreshing Linear token: {e}")
            import traceback
            traceback.print_exc()
    
    def _make_graphql_request(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a GraphQL request to Linear API.
        
        Args:
            query: GraphQL query string
            variables: Optional variables for the query
            
        Returns:
            Response JSON data
        """
        if not self.access_token:
            raise ValueError("No access token available for Linear API")
        
        self._ensure_valid_token()
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "variables": variables or {}
        }
        
        try:
            response = requests.post(LINEAR_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if "errors" in data:
                error_messages = [err.get("message", "Unknown error") for err in data["errors"]]
                raise Exception(f"Linear GraphQL errors: {', '.join(error_messages)}")
            
            return data.get("data", {})
        except requests.exceptions.RequestException as e:
            print(f"Error making Linear GraphQL request: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            raise
    
    def verify_connection(self) -> Dict[str, Any]:
        """Verify Linear token validity and get user info."""
        if not self.access_token:
            return {"status": "error", "message": "No access token available"}
        
        query = """
        query {
            viewer {
                id
                name
                email
            }
        }
        """
        
        try:
            data = self._make_graphql_request(query)
            viewer = data.get("viewer", {})
            
            if viewer:
                return {
                    "status": "verified",
                    "user_id": viewer.get("id"),
                    "name": viewer.get("name"),
                    "email": viewer.get("email")
                }
            else:
                return {"status": "error", "message": "Failed to get user info"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_workspace(self) -> Dict[str, Any]:
        """Get workspace information."""
        query = """
        query {
            workspace {
                id
                name
                urlKey
            }
        }
        """
        
        try:
            data = self._make_graphql_request(query)
            return data.get("workspace", {})
        except Exception as e:
            print(f"Error getting workspace: {e}")
            return {}
    
    def get_teams(self) -> List[Dict[str, Any]]:
        """List available teams in the workspace."""
        query = """
        query {
            teams {
                nodes {
                    id
                    name
                    key
                }
            }
        }
        """
        
        try:
            data = self._make_graphql_request(query)
            teams = data.get("teams", {}).get("nodes", [])
            return teams
        except Exception as e:
            print(f"Error getting teams: {e}")
            return []
    
    def create_issue(
        self,
        title: str,
        description: Optional[str] = None,
        team_id: Optional[str] = None,
        priority: Optional[int] = None,
        labels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Create a Linear issue.
        
        Args:
            title: Issue title
            description: Issue description
            team_id: Team ID (required if not set in config)
            priority: Priority (0-4, where 0 is urgent)
            labels: List of label IDs
            
        Returns:
            Created issue data with id, identifier, url
        """
        # If no team_id provided, try to get from config or use first team
        if not team_id:
            teams = self.get_teams()
            if teams:
                team_id = teams[0]["id"]
            else:
                raise ValueError("No team_id provided and no teams available")
        
        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                    description
                    team {
                        id
                        name
                        key
                    }
                }
            }
        }
        """
        
        variables = {
            "input": {
                "title": title,
                "teamId": team_id
            }
        }
        
        if description:
            variables["input"]["description"] = description
        
        if priority is not None:
            variables["input"]["priority"] = priority
        
        if labels:
            variables["input"]["labelIds"] = labels
        
        try:
            data = self._make_graphql_request(mutation, variables)
            issue_create = data.get("issueCreate", {})
            
            if issue_create.get("success") and issue_create.get("issue"):
                issue = issue_create["issue"]
                return {
                    "id": issue["id"],
                    "identifier": issue["identifier"],
                    "title": issue["title"],
                    "url": issue["url"],
                    "description": issue.get("description"),
                    "team": issue.get("team", {})
                }
            else:
                raise Exception("Failed to create Linear issue")
        except Exception as e:
            print(f"Error creating Linear issue: {e}")
            raise
    
    def get_issue(self, issue_id: str) -> Dict[str, Any]:
        """
        Get Linear issue by ID.
        
        Args:
            issue_id: Linear issue ID (UUID) or identifier (e.g., "ID-123")
            
        Returns:
            Issue data
        """
        # Check if it's an identifier (ID-123) or UUID
        if issue_id.startswith("ID-") or "-" in issue_id and not issue_id.startswith("id"):
            # It's an identifier, need to query by identifier
            query = """
            query GetIssueByIdentifier($identifier: String!) {
                issue(id: $identifier) {
                    id
                    identifier
                    title
                    url
                    description
                    state {
                        name
                        type
                    }
                    team {
                        id
                        name
                        key
                    }
                }
            }
            """
            variables = {"identifier": issue_id}
        else:
            # It's a UUID
            query = """
            query GetIssue($id: String!) {
                issue(id: $id) {
                    id
                    identifier
                    title
                    url
                    description
                    state {
                        name
                        type
                    }
                    team {
                        id
                        name
                        key
                    }
                }
            }
            """
            variables = {"id": issue_id}
        
        try:
            data = self._make_graphql_request(query, variables)
            return data.get("issue", {})
        except Exception as e:
            print(f"Error getting Linear issue: {e}")
            return {}
    
    def get_issue_identifier(self, issue_id: str) -> str:
        """
        Get formatted issue identifier (e.g., "ID-123") from issue ID.
        
        Args:
            issue_id: Linear issue UUID
            
        Returns:
            Formatted identifier like "ID-123"
        """
        issue = self.get_issue(issue_id)
        return issue.get("identifier", issue_id)
    
    def update_issue(self, issue_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a Linear issue.
        
        Args:
            issue_id: Issue UUID
            updates: Dictionary with fields to update (title, description, priority, stateId, etc.)
            
        Returns:
            Updated issue data
        """
        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                }
            }
        }
        """
        
        variables = {
            "id": issue_id,
            "input": updates
        }
        
        try:
            data = self._make_graphql_request(mutation, variables)
            issue_update = data.get("issueUpdate", {})
            
            if issue_update.get("success"):
                return issue_update.get("issue", {})
            else:
                raise Exception("Failed to update Linear issue")
        except Exception as e:
            print(f"Error updating Linear issue: {e}")
            raise
    
    def add_comment_to_issue(self, issue_id: str, body: str) -> Dict[str, Any]:
        """
        Add a comment to a Linear issue.
        
        Args:
            issue_id: Issue UUID
            body: Comment body (supports markdown)
            
        Returns:
            Created comment data
        """
        mutation = """
        mutation CreateComment($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment {
                    id
                    body
                }
            }
        }
        """
        
        variables = {
            "input": {
                "issueId": issue_id,
                "body": body
            }
        }
        
        try:
            data = self._make_graphql_request(mutation, variables)
            comment_create = data.get("commentCreate", {})
            
            if comment_create.get("success"):
                return comment_create.get("comment", {})
            else:
                raise Exception("Failed to create comment")
        except Exception as e:
            print(f"Error adding comment to Linear issue: {e}")
            raise
    
    def get_workflow_states(self, team_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get workflow states for a team.
        
        Args:
            team_id: Team ID (optional, will use first team if not provided)
            
        Returns:
            List of workflow states with id, name, and type
        """
        # If no team_id provided, try to get from config or use first team
        if not team_id:
            teams = self.get_teams()
            if teams:
                team_id = teams[0]["id"]
            else:
                raise ValueError("No team_id provided and no teams available")
        
        query = """
        query GetWorkflowStates($teamId: String!) {
            team(id: $teamId) {
                states {
                    nodes {
                        id
                        name
                        type
                        position
                    }
                }
            }
        }
        """
        
        try:
            data = self._make_graphql_request(query, {"teamId": team_id})
            team = data.get("team", {})
            states = team.get("states", {}).get("nodes", [])
            return states
        except Exception as e:
            print(f"Error getting workflow states: {e}")
            return []
    
    def find_state_by_name(self, state_name: str, team_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Find a workflow state by name (case-insensitive partial match).
        
        Args:
            state_name: State name to search for (e.g., "In Progress", "Done", "Todo")
            team_id: Team ID (optional)
            
        Returns:
            State dictionary with id, name, type, or None if not found
        """
        states = self.get_workflow_states(team_id)
        state_name_lower = state_name.lower()
        
        # Try exact match first
        for state in states:
            if state.get("name", "").lower() == state_name_lower:
                return state
        
        # Try partial match
        for state in states:
            if state_name_lower in state.get("name", "").lower():
                return state
        
        # Try common variations
        variations = {
            "in progress": ["in progress", "in-progress", "inprogress", "working", "active"],
            "done": ["done", "completed", "complete", "resolved", "closed"],
            "todo": ["todo", "backlog", "open", "new"]
        }
        
        for key, variants in variations.items():
            if state_name_lower in variants:
                for state in states:
                    state_type = state.get("type", "").lower()
                    if key == "in progress" and state_type == "started":
                        return state
                    elif key == "done" and state_type == "completed":
                        return state
                    elif key == "todo" and state_type == "unstarted":
                        return state
        
        return None
    
    def update_issue_state(
        self,
        issue_id: str,
        state_name: str,
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update Linear issue state by state name.
        
        Args:
            issue_id: Issue UUID
            state_name: State name to transition to (e.g., "In Progress", "Done")
            team_id: Team ID (optional, will be inferred from issue if not provided)
            
        Returns:
            Updated issue data
        """
        # Get current issue to find team_id if not provided
        if not team_id:
            issue = self.get_issue(issue_id)
            if issue and issue.get("team"):
                team_id = issue["team"].get("id")
        
        # Find the state
        state = self.find_state_by_name(state_name, team_id)
        if not state:
            raise ValueError(f"State '{state_name}' not found for team")
        
        # Update issue with state ID
        return self.update_issue(issue_id, {"stateId": state["id"]})
    
    def update_issue_description(
        self,
        issue_id: str,
        description: str
    ) -> Dict[str, Any]:
        """
        Update Linear issue description.
        
        Args:
            issue_id: Issue UUID
            description: New description (supports markdown)
            
        Returns:
            Updated issue data
        """
        return self.update_issue(issue_id, {"description": description})
    
    def update_issue_with_resolution(
        self,
        issue_id: str,
        resolution: str,
        state_name: str = "Done",
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update Linear issue with resolution details and move to done state.
        
        Args:
            issue_id: Issue UUID
            resolution: Resolution description to append to issue description
            state_name: State name to transition to (default: "Done")
            team_id: Team ID (optional)
            
        Returns:
            Updated issue data
        """
        # Get current issue to preserve existing description
        issue = self.get_issue(issue_id)
        current_description = issue.get("description", "") or ""
        
        # Append resolution section
        resolution_section = f"\n\n---\n\n## Resolution\n\n{resolution}"
        new_description = current_description + resolution_section
        
        # Update description and state
        updates = {"description": new_description}
        
        # Get team_id if not provided
        if not team_id and issue.get("team"):
            team_id = issue["team"].get("id")
        
        # Find and set state
        state = self.find_state_by_name(state_name, team_id)
        if state:
            updates["stateId"] = state["id"]
        
        return self.update_issue(issue_id, updates)
