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

    def _is_bot_or_automation_user(self, assignee: Optional[Dict[str, Any]]) -> bool:
        """
        Determine if an assignee is a bot or automation user.

        Args:
            assignee: Assignee object with id, name, email

        Returns:
            True if assignee appears to be a bot/automation user
        """
        if not assignee:
            return False

        name = (assignee.get("name", "") or "").lower()
        email = (assignee.get("email", "") or "").lower()

        # Common bot/automation indicators
        bot_indicators = [
            "bot", "automation", "ci", "cd", "deploy", "github", "linear",
            "service", "system", "auto", "robot", "agent", "webhook",
            "integration", "sync", "api", "script"
        ]

        # Check name and email for bot indicators
        for indicator in bot_indicators:
            if indicator in name or indicator in email:
                return True

        # Check for typical bot email patterns
        bot_email_patterns = [
            "@noreply", "@bot.", "noreply@", "bot@", "automation@",
            "ci@", "cd@", "deploy@", "system@", "service@"
        ]

        for pattern in bot_email_patterns:
            if pattern in email:
                return True

        return False

    def get_issues(
        self,
        team_id: Optional[str] = None,
        status: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignee: Optional[str] = None,
        page_size: int = 50,
        cursor: Optional[str] = None,
        include_completed: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch issues from Linear with filtering options.

        Args:
            team_id: Filter by specific team (default: all teams user has access to)
            status: Filter by workflow state name (e.g., "Todo", "In Progress")
            labels: Filter by label names
            assignee: Filter by assignee ID or "unassigned"
            page_size: Number of issues per page (1-250)
            cursor: Pagination cursor for next page
            include_completed: Include completed/canceled issues

        Returns:
            Dictionary with issues list and pagination info
        """
        # Build filter conditions
        filters = []

        if team_id:
            filters.append(f'team: {{ id: {{ eq: "{team_id}" }} }}')

        if status:
            filters.append(f'state: {{ name: {{ eq: "{status}" }} }}')

        if not include_completed:
            filters.append('state: { type: { nin: ["completed", "canceled"] } }')

        if assignee:
            if assignee.lower() == "unassigned":
                filters.append('assignee: { null: true }')
            else:
                filters.append(f'assignee: {{ id: {{ eq: "{assignee}" }} }}')

        if labels:
            label_conditions = [f'{{ name: {{ eq: "{label}" }} }}' for label in labels]
            filters.append(f'labels: {{ some: {{ or: [{", ".join(label_conditions)}] }} }}')

        # Build filter string
        filter_str = ""
        if filters:
            filter_str = f'filter: {{ {", ".join(filters)} }}'

        # Build pagination
        pagination = f"first: {min(max(1, page_size), 250)}"  # Clamp between 1 and 250
        if cursor:
            pagination += f', after: "{cursor}"'

        query = f"""
        query GetIssues {{
            issues({pagination}, {filter_str}) {{
                nodes {{
                    id
                    identifier
                    title
                    description
                    url
                    priority
                    estimate
                    createdAt
                    updatedAt
                    state {{
                        id
                        name
                        type
                        position
                    }}
                    labels {{
                        nodes {{
                            id
                            name
                            color
                        }}
                    }}
                    assignee {{
                        id
                        name
                        email
                    }}
                    team {{
                        id
                        name
                        key
                    }}
                }}
                pageInfo {{
                    hasNextPage
                    endCursor
                }}
            }}
        }}
        """

        try:
            data = self._make_graphql_request(query)
            issues_data = data.get("issues", {})

            # Flatten labels structure for easier access
            issues = issues_data.get("nodes", [])
            for issue in issues:
                if issue.get("labels", {}).get("nodes"):
                    issue["labels"] = issue["labels"]["nodes"]
                else:
                    issue["labels"] = []

            return {
                "issues": issues,
                "pageInfo": issues_data.get("pageInfo", {})
            }
        except Exception as e:
            print(f"Error fetching Linear issues: {e}")
            return {"issues": [], "pageInfo": {"hasNextPage": False}}

    def get_open_resolvable_issues(
        self,
        team_ids: Optional[List[str]] = None,
        exclude_labels: Optional[List[str]] = None,
        max_priority: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get open issues that might be resolvable by coding agents.

        OPTIMIZED: Uses single API call instead of nested loops to prevent rate limiting.

        Filters for:
        - Status: Todo, Backlog, Triage (not In Progress, Done, Canceled)
        - No current assignee OR assignee is a bot/automation user
        - Excludes issues with certain labels (e.g., "manual-only", "design")
        - Optional priority filtering

        Args:
            team_ids: List of team IDs to search (default: all teams)
            exclude_labels: Labels to exclude from results
            max_priority: Maximum priority level (0=urgent, 4=no priority)

        Returns:
            List of issues sorted by priority and creation date
        """
        try:
            # Build comprehensive filter for single API call
            filters = []

            # Team filtering
            if team_ids:
                if len(team_ids) == 1:
                    filters.append(f'team: {{ id: {{ eq: "{team_ids[0]}" }} }}')
                else:
                    team_conditions = [f'{{ id: {{ eq: "{team_id}" }} }}' for team_id in team_ids]
                    filters.append(f'team: {{ or: [{", ".join(team_conditions)}] }}')
            else:
                # Get user's teams if none specified
                teams = self.get_teams()
                if teams:
                    team_conditions = [f'{{ id: {{ eq: "{team["id"]}" }} }}' for team in teams]
                    filters.append(f'team: {{ or: [{", ".join(team_conditions)}] }}')

            # State filtering - get open/resolvable states only
            open_state_names = ["Todo", "Backlog", "Triage", "Ready", "To Do"]
            state_conditions = [f'{{ name: {{ eq: "{state}" }} }}' for state in open_state_names]
            filters.append(f'state: {{ or: [{", ".join(state_conditions)}] }}')

            # Assignee filtering - unassigned OR bot-assigned tickets
            # Note: GraphQL doesn't allow complex bot detection, so we get all assigned tickets
            # and filter bot assignments in post-processing
            filters.append('assignee: { null: true }')  # Keep unassigned tickets

            # Priority filtering
            if max_priority is not None:
                filters.append(f'priority: {{ lte: {max_priority} }}')

            # Exclude completed/canceled states
            filters.append('state: { type: { nin: ["completed", "canceled"] } }')

            # Build the GraphQL query with combined filters
            filter_str = f'filter: {{ {", ".join(filters)} }}'
            pagination = "first: 100"  # Get more results in single call

            query = f"""
            query GetResolvableIssues {{
                issues({pagination}, {filter_str}) {{
                    nodes {{
                        id
                        identifier
                        title
                        description
                        url
                        priority
                        estimate
                        createdAt
                        updatedAt
                        state {{
                            id
                            name
                            type
                            position
                        }}
                        labels {{
                            nodes {{
                                id
                                name
                                color
                            }}
                        }}
                        assignee {{
                            id
                            name
                            email
                        }}
                        team {{
                            id
                            name
                            key
                        }}
                    }}
                    pageInfo {{
                        hasNextPage
                        endCursor
                    }}
                }}
            }}
            """

            print(f"🔍 Fetching resolvable issues with optimized single API call")

            # Make single API call
            data = self._make_graphql_request(query)
            issues_data = data.get("issues", {})
            issues = issues_data.get("nodes", [])

            # Flatten labels structure for easier access
            for issue in issues:
                if issue.get("labels", {}).get("nodes"):
                    issue["labels"] = issue["labels"]["nodes"]
                else:
                    issue["labels"] = []

            print(f"📋 Retrieved {len(issues)} issues from Linear API")

            print(f"📋 Retrieved {len(issues)} unassigned issues from Linear API")

            # ENHANCEMENT: Also fetch bot-assigned tickets
            try:
                # Get a sample of assigned tickets to check for bots
                assigned_query = query.replace('assignee: { null: true }', '')
                assigned_data = self._make_graphql_request(assigned_query)
                assigned_issues_data = assigned_data.get("issues", {})
                assigned_issues = assigned_issues_data.get("nodes", [])

                # Flatten labels for assigned issues too
                for issue in assigned_issues:
                    if issue.get("labels", {}).get("nodes"):
                        issue["labels"] = issue["labels"]["nodes"]
                    else:
                        issue["labels"] = []

                # Filter for bot-assigned tickets
                bot_assigned_issues = []
                for issue in assigned_issues:
                    assignee = issue.get("assignee")
                    if assignee and self._is_bot_or_automation_user(assignee):
                        bot_assigned_issues.append(issue)

                # Combine unassigned + bot-assigned
                combined_issues = issues + bot_assigned_issues
                print(f"🤖 Found {len(bot_assigned_issues)} bot-assigned tickets")
                issues = combined_issues

                # Remove duplicates (in case of overlap)
                seen_ids = set()
                unique_issues = []
                for issue in issues:
                    if issue["id"] not in seen_ids:
                        seen_ids.add(issue["id"])
                        unique_issues.append(issue)
                issues = unique_issues
                print(f"📋 Total after bot detection: {len(issues)} issues")

            except Exception as e:
                print(f"⚠️  Could not fetch bot-assigned tickets: {e}")
                # Continue with just unassigned tickets

            # Post-process filtering that couldn't be done in GraphQL
            if exclude_labels:
                filtered_issues = []
                for issue in issues:
                    issue_labels = [label.get("name", "").lower() for label in issue.get("labels", [])]
                    excluded = any(
                        excluded_label.lower() in issue_labels
                        for excluded_label in exclude_labels
                    )
                    if not excluded:
                        filtered_issues.append(issue)
                issues = filtered_issues
                print(f"🏷️  After label filtering: {len(issues)} issues")

            # Sort by priority (lower number = higher priority), then by creation date
            issues.sort(
                key=lambda x: (
                    x.get("priority", 999),  # Treat None/missing priority as lowest
                    x.get("createdAt", "")
                )
            )

            print(f"✅ Returning {len(issues)} resolvable issues (single API call)")
            return issues

        except Exception as e:
            print(f"❌ Error fetching resolvable issues: {e}")
            # Fallback to empty list rather than nested loops
            return []

    def analyze_issue_for_resolution(self, issue_id: str) -> Dict[str, Any]:
        """
        Get detailed issue information for resolution analysis.

        Args:
            issue_id: Linear issue UUID

        Returns:
            Dictionary with full issue details, comments, and relations
        """
        query = f"""
        query GetIssueDetails {{
            issue(id: "{issue_id}") {{
                id
                identifier
                title
                description
                url
                priority
                estimate
                createdAt
                updatedAt
                state {{
                    id
                    name
                    type
                    position
                }}
                labels {{
                    nodes {{
                        id
                        name
                        color
                    }}
                }}
                assignee {{
                    id
                    name
                    email
                }}
                team {{
                    id
                    name
                    key
                }}
                attachments {{
                    nodes {{
                        id
                        title
                        url
                        metadata
                    }}
                }}
                relations {{
                    nodes {{
                        id
                        type
                        relatedIssue {{
                            id
                            identifier
                            title
                        }}
                    }}
                }}
            }}
        }}
        """

        # Query for comments separately (they're not directly available in issue query)
        comments_query = f"""
        query GetIssueComments {{
            issue(id: "{issue_id}") {{
                comments {{
                    nodes {{
                        id
                        body
                        createdAt
                        user {{
                            id
                            name
                            email
                        }}
                    }}
                }}
            }}
        }}
        """

        try:
            # Get issue details
            issue_data = self._make_graphql_request(query)
            issue = issue_data.get("issue", {})

            if not issue:
                print(f"⚠️  Issue {issue_id} not found")
                return {}

            # Flatten labels structure
            if issue.get("labels", {}).get("nodes"):
                issue["labels"] = issue["labels"]["nodes"]
            else:
                issue["labels"] = []

            # Flatten attachments structure
            if issue.get("attachments", {}).get("nodes"):
                issue["attachments"] = issue["attachments"]["nodes"]
            else:
                issue["attachments"] = []

            # Flatten relations structure
            if issue.get("relations", {}).get("nodes"):
                issue["relations"] = issue["relations"]["nodes"]
            else:
                issue["relations"] = []

            # Get comments
            try:
                comments_data = self._make_graphql_request(comments_query)
                comments_info = comments_data.get("issue", {}).get("comments", {})
                comments = comments_info.get("nodes", [])
            except Exception as e:
                print(f"⚠️  Error fetching comments for issue {issue_id}: {e}")
                comments = []

            return {
                "issue": issue,
                "comments": comments,
                "attachments": issue.get("attachments", []),
                "relations": issue.get("relations", [])
            }

        except Exception as e:
            print(f"Error getting issue details for {issue_id}: {e}")
            return {}

    def search_issues(
        self,
        query: str,
        team_ids: Optional[List[str]] = None,
        include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Search issues by text query.

        Args:
            query: Search query (searches title, description, comments)
            team_ids: Limit to specific teams
            include_archived: Include archived issues

        Returns:
            List of matching issues
        """
        # Build filter conditions
        filters = []

        if team_ids:
            team_conditions = [f'{{ id: {{ eq: "{team_id}" }} }}' for team_id in team_ids]
            filters.append(f'team: {{ or: [{", ".join(team_conditions)}] }}')

        if not include_archived:
            filters.append('state: { type: { nin: ["completed", "canceled"] } }')

        filter_str = ""
        if filters:
            filter_str = f', filter: {{ {", ".join(filters)} }}'

        graphql_query = f"""
        query SearchIssues {{
            searchIssues(query: "{query}", first: 50{filter_str}) {{
                nodes {{
                    id
                    identifier
                    title
                    description
                    url
                    priority
                    createdAt
                    state {{
                        id
                        name
                        type
                    }}
                    labels {{
                        nodes {{
                            id
                            name
                            color
                        }}
                    }}
                    team {{
                        id
                        name
                        key
                    }}
                }}
            }}
        }}
        """

        try:
            data = self._make_graphql_request(graphql_query)
            issues = data.get("searchIssues", {}).get("nodes", [])

            # Flatten labels structure
            for issue in issues:
                if issue.get("labels", {}).get("nodes"):
                    issue["labels"] = issue["labels"]["nodes"]
                else:
                    issue["labels"] = []

            return issues
        except Exception as e:
            print(f"Error searching Linear issues: {e}")
            return []
