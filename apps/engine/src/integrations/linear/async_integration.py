"""
Async Linear integration for non-blocking issue creation and management.
Converts synchronous Linear API calls to async using aiohttp.

This eliminates the 2-5 second blocking calls in the consumer thread.
"""
from typing import Dict, Any, Optional, List
import os
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
from src.database.database import SessionLocal
from src.database.models import Integration
from src.integrations.linear.oauth import LINEAR_API_URL, refresh_access_token
from src.auth.crypto_utils import decrypt_token, encrypt_token
from src.integrations.linear.integration import LinearIntegration


class AsyncLinearIntegration:
    """Async wrapper for Linear API interactions via GraphQL."""

    def __init__(self, integration_id: int, db=None):
        """
        Initialize async Linear integration.

        Args:
            integration_id: Linear integration ID from database
            db: Optional database session (if not provided, creates new one)
        """
        self.integration_id = integration_id
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.session = None  # aiohttp session

        # Get tokens from database
        self._load_tokens_from_db(db)

        # Initialize sync integration for fallback operations
        self.sync_integration = LinearIntegration(integration_id=integration_id)

    def _load_tokens_from_db(self, db=None):
        """Load access and refresh tokens from database."""
        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            integration = db.query(Integration).filter(Integration.id == self.integration_id).first()
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
            if close_db:
                db.close()

    async def _ensure_session(self):
        """Ensure aiohttp session is available."""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def _close_session(self):
        """Close aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def _ensure_valid_token(self):
        """Ensure access token is valid, refresh if needed."""
        if not self.access_token:
            return

        # Check if token is expired or will expire soon (within 5 minutes)
        if self.token_expires_at:
            if datetime.now(timezone.utc) >= self.token_expires_at - timedelta(minutes=5):
                await self._refresh_token_async()

    async def _refresh_token_async(self):
        """Refresh the access token using refresh token (async version)."""
        if not self.refresh_token:
            print("⚠️  No refresh token available for Linear integration")
            return

        try:
            # Note: refresh_access_token is still sync, but that's okay as it's a quick operation
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
                await self._update_tokens_in_db_async()

                print("✅ Linear access token refreshed successfully (async)")
            else:
                print("⚠️  Token refresh response missing access_token")
        except Exception as e:
            print(f"❌ Error refreshing Linear token (async): {e}")
            import traceback
            traceback.print_exc()

    async def _update_tokens_in_db_async(self):
        """Update tokens in database (async-friendly)."""
        # Note: SQLAlchemy operations are still sync, but this is a quick DB operation
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

    async def _make_graphql_request_async(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make async GraphQL request to Linear API using aiohttp.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query

        Returns:
            Response JSON data
        """
        if not self.access_token:
            raise ValueError("No access token available for Linear API")

        await self._ensure_valid_token()
        await self._ensure_session()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "query": query,
            "variables": variables or {}
        }

        try:
            async with self.session.post(LINEAR_API_URL, json=payload, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()

                if "errors" in data:
                    error_messages = [err.get("message", "Unknown error") for err in data["errors"]]
                    raise Exception(f"Linear GraphQL errors: {', '.join(error_messages)}")

                return data.get("data", {})
        except aiohttp.ClientError as e:
            print(f"Error making async Linear GraphQL request: {e}")
            raise
        except Exception as e:
            print(f"Unexpected error in async Linear GraphQL request: {e}")
            raise

    async def get_teams_async(self) -> List[Dict[str, Any]]:
        """List available teams in the workspace (async version)."""
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
            data = await self._make_graphql_request_async(query)
            teams = data.get("teams", {}).get("nodes", [])
            return teams
        except Exception as e:
            print(f"Error getting teams (async): {e}")
            return []

    async def create_issue_async(
        self,
        title: str,
        description: Optional[str] = None,
        team_id: Optional[str] = None,
        priority: Optional[int] = None,
        labels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Create a Linear issue asynchronously.

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
            teams = await self.get_teams_async()
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
            data = await self._make_graphql_request_async(mutation, variables)
            issue_create = data.get("issueCreate", {})

            if issue_create.get("success") and issue_create.get("issue"):
                issue = issue_create["issue"]
                result = {
                    "id": issue["id"],
                    "identifier": issue["identifier"],
                    "title": issue["title"],
                    "url": issue["url"],
                    "description": issue.get("description"),
                    "team": issue.get("team", {})
                }
                print(f"✅ Created Linear issue {issue['identifier']} asynchronously")
                return result
            else:
                raise Exception("Failed to create Linear issue (async)")
        except Exception as e:
            print(f"Error creating Linear issue (async): {e}")
            raise
        finally:
            # Clean up session when done
            await self._close_session()

    def __del__(self):
        """Cleanup when object is destroyed."""
        if self.session and not self.session.closed:
            # Try to close session, but don't block if event loop is already closed
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule cleanup
                    loop.create_task(self._close_session())
            except:
                # Event loop might be closed, ignore
                pass


async def create_linear_issue_async_wrapper(
    integration_id: int,
    title: str,
    description: Optional[str] = None,
    team_id: Optional[str] = None,
    priority: Optional[int] = None,
    labels: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Convenience wrapper function for creating Linear issues asynchronously.

    This can be called from sync code using asyncio.run_coroutine_threadsafe().
    """
    async_linear = AsyncLinearIntegration(integration_id)
    try:
        return await async_linear.create_issue_async(
            title=title,
            description=description,
            team_id=team_id,
            priority=priority,
            labels=labels
        )
    finally:
        await async_linear._close_session()