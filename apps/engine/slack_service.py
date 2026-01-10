"""
Slack Service for Agent Employee Communication

Handles all Slack API interactions for agent employees including:
- Sending messages to channels
- Receiving events (mentions, DMs)
- Channel management
- Bot user operations
"""
import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    SLACK_SDK_AVAILABLE = True
except ImportError:
    SLACK_SDK_AVAILABLE = False
    print("⚠️  slack-sdk not installed. Install it with: pip install slack-sdk")


class SlackService:
    """Service for interacting with Slack API on behalf of agent employees."""
    
    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize Slack service.
        
        Args:
            bot_token: Slack bot token (xoxb-...). If None, reads from SLACK_BOT_TOKEN env var.
        """
        if not SLACK_SDK_AVAILABLE:
            raise ImportError("slack-sdk is not installed. Install it with: pip install slack-sdk")
        
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        if not self.bot_token:
            raise ValueError("Slack bot token is required. Set SLACK_BOT_TOKEN environment variable or pass bot_token parameter.")
        
        self.client = WebClient(token=self.bot_token)
        self.bot_user_id = None
        self.bot_user_name = None
        self._cache_info()
    
    def _cache_info(self):
        """Cache bot user information."""
        try:
            response = self.test_connection()
            if response.get("ok"):
                self.bot_user_id = response.get("user_id")
                self.bot_user_name = response.get("user")
        except Exception as e:
            print(f"⚠️  Warning: Failed to cache bot info: {e}")
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test Slack API connection and return bot information.
        
        Returns:
            Dictionary with connection status and bot info
        """
        try:
            response = self.client.auth_test()
            return {
                "ok": True,
                "user": response.get("user"),
                "user_id": response.get("user_id"),
                "team": response.get("team"),
                "team_id": response.get("team_id"),
                "url": response.get("url")
            }
        except SlackApiError as e:
            return {
                "ok": False,
                "error": e.response["error"] if e.response else str(e)
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e)
            }
    
    def get_channel_id(self, channel_name: str) -> Optional[str]:
        """
        Get Slack channel ID from channel name.
        
        Args:
            channel_name: Channel name (e.g., "#engineering" or "engineering")
        
        Returns:
            Channel ID or None if not found
        """
        try:
            # Remove # prefix if present
            channel_name = channel_name.lstrip("#")
            
            # Try to get channel by name
            response = self.client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True
            )
            
            if response.get("ok"):
                for channel in response.get("channels", []):
                    if channel.get("name") == channel_name:
                        return channel.get("id")
            
            # If not found, try to find by ID (if channel_name is already an ID)
            if channel_name.startswith("C") and len(channel_name) == 9:
                return channel_name
            
            return None
        except SlackApiError as e:
            print(f"⚠️  Error getting channel ID: {e.response.get('error', str(e))}")
            return None
        except Exception as e:
            print(f"⚠️  Error getting channel ID: {str(e)}")
            return None
    
    def join_channel(self, channel_id: str) -> bool:
        """
        Join a Slack channel.
        
        Args:
            channel_id: Channel ID to join
        
        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.client.conversations_join(channel=channel_id)
            return response.get("ok", False)
        except SlackApiError as e:
            # If already in channel, that's okay
            if e.response.get("error") == "already_in_channel":
                return True
            print(f"⚠️  Error joining channel: {e.response.get('error', str(e))}")
            return False
        except Exception as e:
            print(f"⚠️  Error joining channel: {str(e)}")
            return False
    
    def invite_bot_to_channel(self, channel_id: str) -> bool:
        """
        Invite bot to a channel (same as join, but uses conversations.invite).
        
        Args:
            channel_id: Channel ID
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # First try to join
            if self.join_channel(channel_id):
                return True
            
            # If bot user ID is available, try invite
            if self.bot_user_id:
                response = self.client.conversations_invite(
                    channel=channel_id,
                    users=[self.bot_user_id]
                )
                return response.get("ok", False)
            
            return False
        except SlackApiError as e:
            error = e.response.get("error", "")
            if error in ["already_in_channel", "already_invited"]:
                return True
            print(f"⚠️  Error inviting bot to channel: {error}")
            return False
        except Exception as e:
            print(f"⚠️  Error inviting bot to channel: {str(e)}")
            return False
    
    def post_message(
        self,
        channel_id: str,
        text: str,
        agent_name: Optional[str] = None,
        agent_department: Optional[str] = None,
        blocks: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Post a message to a Slack channel as an agent employee.
        
        Args:
            channel_id: Channel ID to post to
            text: Message text (fallback if blocks fail)
            agent_name: Agent's name (e.g., "Alexandra Chen")
            agent_department: Agent's department (e.g., "Engineering")
            blocks: Slack Block Kit blocks for rich formatting
        
        Returns:
            Dictionary with success status and message timestamp
        """
        try:
            # Build message with agent identity in text if provided
            message_text = text
            if agent_name:
                prefix = f"[{agent_name}]"
                if agent_department:
                    prefix += f" ({agent_department})"
                message_text = f"{prefix} {text}"
            
            # Use blocks if provided, otherwise use text
            if blocks:
                response = self.client.chat_postMessage(
                    channel=channel_id,
                    text=message_text,  # Fallback text
                    blocks=blocks
                )
            else:
                response = self.client.chat_postMessage(
                    channel=channel_id,
                    text=message_text
                )
            
            if response.get("ok"):
                return {
                    "success": True,
                    "ts": response.get("ts"),
                    "channel": response.get("channel"),
                    "message": response.get("message")
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to post message",
                    "response": response
                }
        except SlackApiError as e:
            error = e.response.get("error", str(e))
            print(f"❌ Error posting message to Slack: {error}")
            return {
                "success": False,
                "error": error
            }
        except Exception as e:
            print(f"❌ Error posting message to Slack: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def post_agent_status_update(
        self,
        channel_id: str,
        agent_name: str,
        agent_department: str,
        status: str,
        task_description: Optional[str] = None,
        completed_tasks: Optional[List[str]] = None,
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Post an agent status update with rich formatting.
        
        Args:
            channel_id: Channel ID
            agent_name: Agent's name
            agent_department: Agent's department
            status: Status type (started, completed, error, idle)
            task_description: Description of current/completed task
            completed_tasks: List of recently completed tasks
            error_message: Error message if status is "error"
        
        Returns:
            Dictionary with success status
        """
        # Determine emoji and color based on status
        status_config = {
            "started": {"emoji": "🚀", "color": "#36a64f", "title": "Started Work"},
            "completed": {"emoji": "✅", "color": "#2eb886", "title": "Task Completed"},
            "error": {"emoji": "⚠️", "color": "#ff0000", "title": "Error Occurred"},
            "idle": {"emoji": "💤", "color": "#757575", "title": "Idle"},
            "working": {"emoji": "⚙️", "color": "#ffa500", "title": "Working"}
        }
        
        config = status_config.get(status, status_config["idle"])
        
        # Build blocks for rich formatting
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{config['emoji']} {config['title']}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Agent:*\n{agent_name}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Department:*\n{agent_department}"
                    }
                ]
            }
        ]
        
        # Add task description if provided
        if task_description:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Task:*\n{task_description}"
                }
            })
        
        # Add error message if error status
        if status == "error" and error_message:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error:*\n```{error_message[:500]}```"  # Truncate long errors
                }
            })
        
        # Add completed tasks if provided
        if completed_tasks and len(completed_tasks) > 0:
            tasks_text = "\n".join([f"• {task}" for task in completed_tasks[:5]])  # Show max 5
            if len(completed_tasks) > 5:
                tasks_text += f"\n• ... and {len(completed_tasks) - 5} more"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recently Completed:*\n{tasks_text}"
                }
            })
        
        # Add timestamp
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Updated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                }
            ]
        })
        
        # Fallback text
        text = f"{config['emoji']} {agent_name} ({agent_department}): {config['title']}"
        if task_description:
            text += f" - {task_description}"
        if error_message:
            text += f" - Error: {error_message}"
        
        return self.post_message(
            channel_id=channel_id,
            text=text,
            agent_name=agent_name,
            agent_department=agent_department,
            blocks=blocks
        )
    
    def post_welcome_message(
        self,
        channel_id: str,
        agent_name: str,
        agent_role: str,
        agent_department: str,
        capabilities: List[str]
    ) -> Dict[str, Any]:
        """
        Post a welcome message introducing the agent to the channel.
        
        Args:
            channel_id: Channel ID
            agent_name: Agent's name
            agent_role: Agent's role/title
            agent_department: Agent's department
            capabilities: List of agent capabilities
        
        Returns:
            Dictionary with success status
        """
        capabilities_text = "\n".join([f"• {cap.replace('_', ' ').title()}" for cap in capabilities])
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"👋 Hello! I'm {agent_name}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{agent_role}* | *{agent_department}*\n\nI'm your AI agent employee! I'll be posting updates here about my work, including task progress, code fixes, and incident resolutions."
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*What I can do:*\n{capabilities_text}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Feel free to @mention me to ask about my work, current tasks, or completed work!"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Onboarded on {datetime.utcnow().strftime('%B %d, %Y at %I:%M %p UTC')}"
                    }
                ]
            }
        ]
        
        text = f"👋 Hello! I'm {agent_name}, your {agent_role} from {agent_department}. I'll be posting updates about my work here!"
        
        return self.post_message(
            channel_id=channel_id,
            text=text,
            agent_name=agent_name,
            agent_department=agent_department,
            blocks=blocks
        )
    
    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a Slack user.
        
        Args:
            user_id: Slack user ID
        
        Returns:
            User information dictionary or None
        """
        try:
            response = self.client.users_info(user=user_id)
            if response.get("ok"):
                return response.get("user")
            return None
        except SlackApiError as e:
            print(f"⚠️  Error getting user info: {e.response.get('error', str(e))}")
            return None
        except Exception as e:
            print(f"⚠️  Error getting user info: {str(e)}")
            return None
    
    def send_dm(self, user_id: str, text: str, blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Send a direct message to a user.
        
        Args:
            user_id: Slack user ID
            text: Message text
            blocks: Optional Block Kit blocks
        
        Returns:
            Dictionary with success status
        """
        try:
            # Open DM conversation
            response = self.client.conversations_open(users=[user_id])
            if not response.get("ok"):
                return {
                    "success": False,
                    "error": "Failed to open DM conversation"
                }
            
            channel_id = response.get("channel", {}).get("id")
            if not channel_id:
                return {
                    "success": False,
                    "error": "No channel ID in response"
                }
            
            # Send message
            if blocks:
                msg_response = self.client.chat_postMessage(
                    channel=channel_id,
                    text=text,
                    blocks=blocks
                )
            else:
                msg_response = self.client.chat_postMessage(
                    channel=channel_id,
                    text=text
                )
            
            return {
                "success": msg_response.get("ok", False),
                "ts": msg_response.get("ts") if msg_response.get("ok") else None,
                "error": None if msg_response.get("ok") else msg_response.get("error")
            }
        except SlackApiError as e:
            return {
                "success": False,
                "error": e.response.get("error", str(e))
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
