#!/usr/bin/env python3
"""
Onboard an Agent Employee (AI Agent as Company Employee)

This script creates a new agent employee with:
- Personal identity (name, email)
- Slack Bot integration
- Work tracking capabilities
- Mapping to CrewAI agent role

Usage:
    python onboard_agent_employee.py

For custom configuration:
    python onboard_agent_employee.py --name "Alex Code" --email "alex-code@healops.work" --role "coding_agent" --slack-channel "#general"
"""

import os
import sys
import argparse
from datetime import datetime
from typing import Optional, Dict, Any, List

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sys
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.database import SessionLocal, engine, Base
from src.auth.crypto_utils import encrypt_token, decrypt_token

# Ensure tables are created
Base.metadata.create_all(bind=engine)

# Agent role configurations with realistic human names and departments
AGENT_ROLES = {
    "coding_agent": {
        "name": "Alexandra Chen",
        "email": "alexandra.chen@healops.work",
        "display_name": "Alexandra Chen",
        "role": "Senior Software Engineer",
        "department": "Engineering",
        "crewai_role": "code_fixer_primary",  # Maps to agent_definitions.py
        "agent_type": "coding",
        "capabilities": [
            "code_generation",
            "bug_fixing",
            "code_review",
            "pr_creation",
            "incremental_edits",
            "code_validation"
        ],
        "description": "Expert at implementing code fixes, writing clean code, and creating pull requests"
    },
    "rca_analyst": {
        "name": "Samuel Rodriguez",
        "email": "samuel.rodriguez@healops.work",
        "display_name": "Samuel Rodriguez",
        "role": "Senior Site Reliability Engineer",
        "department": "Platform Engineering",
        "crewai_role": "rca_analyst",
        "agent_type": "analysis",
        "capabilities": [
            "log_analysis",
            "root_cause_analysis",
            "incident_investigation",
            "pattern_recognition"
        ],
        "description": "Specializes in analyzing incidents and identifying root causes"
    },
    "log_parser": {
        "name": "Maya Patel",
        "email": "maya.patel@healops.work",
        "display_name": "Maya Patel",
        "role": "DevOps Engineer",
        "department": "Platform Engineering",
        "crewai_role": "log_parser",
        "agent_type": "parsing",
        "capabilities": [
            "log_parsing",
            "anomaly_detection",
            "structured_extraction",
            "error_identification"
        ],
        "description": "Expert at parsing and extracting signals from various log formats"
    },
    "safety_officer": {
        "name": "David Kim",
        "email": "david.kim@healops.work",
        "display_name": "David Kim",
        "role": "Security & Compliance Engineer",
        "department": "Security & Compliance",
        "crewai_role": "safety_officer",
        "agent_type": "safety",
        "capabilities": [
            "safety_validation",
            "risk_assessment",
            "compliance_checking",
            "approval_workflow"
        ],
        "description": "Ensures all actions are safe, reversible, and compliant"
    }
}


def get_or_create_agent_employee(
    db,
    name: str,
    email: str,
    role: str,
    department: str,
    agent_type: str,
    crewai_role: str,
    capabilities: list,
    description: str,
    slack_bot_token: Optional[str] = None,
    slack_channel_id: Optional[str] = None,
    slack_user_id: Optional[str] = None
) -> Any:
    """
    Get existing agent employee or create new one.
    
    Note: This requires the AgentEmployee model to be defined in models.py
    See the plan for model structure.
    """
    try:
        from models import AgentEmployee
    except ImportError:
        print("❌ Error: AgentEmployee model not found in models.py")
        print("   Please implement the AgentEmployee model first (see plan)")
        sys.exit(1)
    
    # Check if agent employee already exists
    existing = db.query(AgentEmployee).filter(
        AgentEmployee.email == email
    ).first()
    
    if existing:
        print(f"⚠️  Agent employee {name} ({email}) already exists.")
        print(f"   Agent ID: {existing.id}")
        print(f"   Status: {existing.status}")
        
        # Update if new information provided
        updated = False
        
        if name and existing.name != name:
            existing.name = name
            print(f"   ✅ Updated name: {name}")
            updated = True
        
        if department and existing.department != department:
            existing.department = department
            print(f"   ✅ Updated department: {department}")
            updated = True
        
        if role and existing.role != role:
            existing.role = role
            print(f"   ✅ Updated role: {role}")
            updated = True
        
        if slack_bot_token:
            existing.slack_bot_token = encrypt_token(slack_bot_token)
            print(f"   ✅ Updated Slack bot token")
            updated = True
        
        if slack_channel_id:
            existing.slack_channel_id = slack_channel_id
            print(f"   ✅ Updated Slack channel ID: {slack_channel_id}")
            updated = True
        
        if slack_user_id:
            existing.slack_user_id = slack_user_id
            print(f"   ✅ Updated Slack user ID: {slack_user_id}")
            updated = True
        
        if updated:
            db.commit()
        
        return existing
    
    # Create new agent employee
    agent_employee = AgentEmployee(
        name=name,
        email=email,
        role=role,
        department=department,
        agent_type=agent_type,
        crewai_role=crewai_role,
        capabilities=capabilities,
        description=description,
        status="available",  # available, working, idle
        current_task=None,
        completed_tasks=[],
        slack_bot_token=encrypt_token(slack_bot_token) if slack_bot_token else None,
        slack_channel_id=slack_channel_id,
        slack_user_id=slack_user_id,
        created_at=datetime.utcnow()
    )
    
    db.add(agent_employee)
    db.commit()
    db.refresh(agent_employee)
    
    return agent_employee


def setup_slack_integration(
    agent_employee: Any,
    agent_name: str,
    agent_role: str,
    agent_department: str,
    capabilities: List[str],
    slack_channel: Optional[str] = None,
    post_welcome: bool = True
) -> Dict[str, Any]:
    """
    Setup Slack integration for agent employee.
    
    This requires:
    1. Slack App created (https://api.slack.com/apps)
    2. Bot token (xoxb-...) from OAuth flow
    3. Channel ID where agent will post updates
    
    Args:
        agent_employee: Agent employee object (for future use)
        agent_name: Agent's name
        agent_role: Agent's role/title
        agent_department: Agent's department
        capabilities: List of agent capabilities
        slack_channel: Channel name (e.g., "#engineering") or channel ID
        post_welcome: Whether to post welcome message after setup
    
    Returns:
        Dictionary with Slack setup results
    """
    print("\n" + "="*60)
    print("Slack Integration Setup")
    print("="*60)
    
    # Check for Slack bot token in environment
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    
    if not slack_bot_token:
        print("\n⚠️  SLACK_BOT_TOKEN not found in environment variables.")
        print("\nTo setup Slack integration:")
        print("1. Create a Slack App at https://api.slack.com/apps")
        print("2. Install the app to your workspace (OAuth & Permissions)")
        print("3. Add Bot Token Scopes:")
        print("   - chat:write")
        print("   - channels:read")
        print("   - channels:join")
        print("   - users:read")
        print("   - app_mentions:read")
        print("   - im:read")
        print("   - im:write")
        print("4. Copy the Bot User OAuth Token (starts with xoxb-)")
        print("5. Set environment variable: export SLACK_BOT_TOKEN='xoxb-your-token'")
        print("\n📖 See SLACK_ONBOARDING_GUIDE.md for detailed instructions")
        print("\nFor now, continuing without Slack integration...")
        return {
            "success": False,
            "message": "Slack bot token not configured"
        }
    
    print(f"✅ Found Slack bot token (prefix: {slack_bot_token[:12]}...)")
    
    # Import Slack service
    try:
        from slack_service import SlackService
        slack_service = SlackService(slack_bot_token)
    except ImportError as e:
        print(f"⚠️  SlackService import failed: {e}")
        print("   Install slack-sdk: pip install slack-sdk>=3.27.0")
        print("   Continuing without Slack service validation...")
        return {
            "success": False,
            "message": "SlackService not available"
        }
    except Exception as e:
        print(f"❌ Error initializing SlackService: {e}")
        return {
            "success": False,
            "message": f"SlackService initialization error: {str(e)}"
        }
    
    # Test Slack connection
    try:
        auth_test = slack_service.test_connection()
        if not auth_test.get("ok"):
            print(f"❌ Slack connection failed: {auth_test.get('error')}")
            return {
                "success": False,
                "message": f"Slack connection failed: {auth_test.get('error')}"
            }
        
        print(f"✅ Slack connection successful!")
        print(f"   Bot User: {auth_test.get('user')}")
        print(f"   Team: {auth_test.get('team')}")
        bot_user_id = auth_test.get("user_id")
        
        # Get or set channel
        channel_id = None
        if slack_channel:
            # Resolve channel name to ID
            channel_id = slack_service.get_channel_id(slack_channel)
            if channel_id:
                print(f"✅ Found channel: {slack_channel} (ID: {channel_id})")
            else:
                print(f"⚠️  Channel '{slack_channel}' not found.")
                print(f"   Please create the channel or invite the bot manually.")
                return {
                    "success": False,
                    "message": f"Channel '{slack_channel}' not found",
                    "bot_token": slack_bot_token,
                    "user_id": bot_user_id
                }
        else:
            # Use default channel
            default_channel = os.getenv("SLACK_DEFAULT_CHANNEL", "#general")
            channel_id = slack_service.get_channel_id(default_channel)
            if channel_id:
                print(f"✅ Using default channel: {default_channel} (ID: {channel_id})")
            else:
                print(f"⚠️  Default channel '{default_channel}' not found.")
                print(f"   Set SLACK_DEFAULT_CHANNEL environment variable or use --slack-channel flag")
                return {
                    "success": False,
                    "message": f"Default channel '{default_channel}' not found",
                    "bot_token": slack_bot_token,
                    "user_id": bot_user_id
                }
        
        # Invite bot to channel
        print(f"🔗 Inviting bot to channel...")
        if slack_service.invite_bot_to_channel(channel_id):
            print(f"✅ Bot added to channel")
        else:
            print(f"⚠️  Bot may already be in channel, or invitation failed")
            print(f"   You may need to manually invite the bot: /invite @{auth_test.get('user')}")
        
        # Post welcome message if requested
        if post_welcome and channel_id:
            print(f"📝 Posting welcome message...")
            welcome_result = slack_service.post_welcome_message(
                channel_id=channel_id,
                agent_name=agent_name,
                agent_role=agent_role,
                agent_department=agent_department,
                capabilities=capabilities
            )
            if welcome_result.get("success"):
                print(f"✅ Welcome message posted successfully!")
            else:
                print(f"⚠️  Failed to post welcome message: {welcome_result.get('error')}")
                print(f"   Bot may need additional permissions (chat:write.public)")
        
        return {
            "success": True,
            "bot_token": slack_bot_token,
            "channel_id": channel_id,
            "user_id": bot_user_id
        }
    except Exception as e:
        print(f"❌ Error during Slack setup: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Slack setup error: {str(e)}"
        }


def generate_email_from_name(name: str) -> str:
    """
    Generate email address from full name (firstname.lastname@healops.work).
    
    Args:
        name: Full name (e.g., "Alexandra Chen")
    
    Returns:
        Email address (e.g., "alexandra.chen@healops.work")
    """
    parts = name.lower().strip().split()
    if len(parts) >= 2:
        firstname = parts[0]
        lastname = "".join(parts[1:])  # Handle multi-part last names
        return f"{firstname}.{lastname}@healops.work"
    elif len(parts) == 1:
        return f"{parts[0]}@healops.work"
    else:
        raise ValueError(f"Invalid name format: {name}")


def onboard_agent(
    agent_role_key: str = "coding_agent",
    custom_name: Optional[str] = None,
    custom_email: Optional[str] = None,
    custom_department: Optional[str] = None,
    slack_channel: Optional[str] = None,
    skip_slack: bool = False
) -> Dict[str, Any]:
    """
    Main onboarding function for an agent employee.
    
    Args:
        agent_role_key: Key from AGENT_ROLES dict (e.g., "coding_agent")
        custom_name: Override default name (if provided, email will be auto-generated)
        custom_email: Override default email (overrides auto-generated email from name)
        custom_department: Override default department
        slack_channel: Slack channel name (e.g., "#general") or channel ID
        skip_slack: Skip Slack integration setup
    
    Returns:
        Dictionary with onboarding results
    """
    if agent_role_key not in AGENT_ROLES:
        print(f"❌ Error: Unknown agent role '{agent_role_key}'")
        print(f"   Available roles: {', '.join(AGENT_ROLES.keys())}")
        return {"success": False, "error": "Unknown agent role"}
    
    config = AGENT_ROLES[agent_role_key]
    
    # Use custom values if provided
    name = custom_name or config["name"]
    department = custom_department or config["department"]
    
    # Generate email from name if custom name provided, otherwise use config email
    if custom_name:
        email = custom_email or generate_email_from_name(name)
    else:
        email = custom_email or config["email"]
    
    print("="*60)
    print("Onboarding Agent Employee")
    print("="*60)
    print(f"Name: {name}")
    print(f"Email: {email}")
    print(f"Role: {config['role']}")
    print(f"Department: {department}")
    print(f"Type: {config['agent_type']}")
    print(f"CrewAI Role: {config['crewai_role']}")
    print(f"Capabilities: {', '.join(config['capabilities'])}")
    print("="*60)
    
    db = SessionLocal()
    
    try:
        # Setup Slack integration if not skipped
        slack_info = None
        if not skip_slack:
            slack_info = setup_slack_integration(
                agent_employee=None,  # Will be created after
                agent_name=name,
                agent_role=config["role"],
                agent_department=department,
                capabilities=config["capabilities"],
                slack_channel=slack_channel,
                post_welcome=True
            )
        else:
            print("\n⏭️  Skipping Slack integration setup")
        
        # Create agent employee
        agent_employee = get_or_create_agent_employee(
            db=db,
            name=name,
            email=email,
            role=config["role"],
            department=department,
            agent_type=config["agent_type"],
            crewai_role=config["crewai_role"],
            capabilities=config["capabilities"],
            description=config["description"],
            slack_bot_token=slack_info.get("bot_token") if slack_info and slack_info.get("success") else None,
            slack_channel_id=slack_info.get("channel_id") if slack_info else None,
            slack_user_id=slack_info.get("user_id") if slack_info else None
        )
        
        print("\n" + "="*60)
        print("✅ Agent Employee Onboarded Successfully!")
        print("="*60)
        print(f"Agent ID: {agent_employee.id}")
        print(f"Name: {agent_employee.name}")
        print(f"Email: {agent_employee.email}")
        print(f"Role: {agent_employee.role}")
        print(f"Department: {agent_employee.department}")
        print(f"Status: {agent_employee.status}")
        print(f"CrewAI Role: {agent_employee.crewai_role}")
        
        if agent_employee.slack_channel_id:
            print(f"Slack Channel: {agent_employee.slack_channel_id}")
        else:
            print("Slack Channel: Not configured")
        
        print("\n" + "="*60)
        print("Next Steps:")
        print("="*60)
        print("1. Configure Slack webhook endpoints in main.py:")
        print("   - POST /slack/events (Events API)")
        print("   - POST /slack/interactive (Interactive Components)")
        print("\n2. Set up agent communication hooks in agent_orchestrator.py")
        print("3. Test agent by triggering an incident resolution")
        print("4. Agent will post updates to Slack automatically")
        print("="*60)
        
        return {
            "success": True,
            "agent_id": agent_employee.id,
            "name": agent_employee.name,
            "email": agent_employee.email,
            "role": agent_employee.role,
            "department": agent_employee.department,
            "slack_configured": bool(agent_employee.slack_channel_id)
        }
        
    except Exception as e:
        print(f"\n❌ Error onboarding agent: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        db.close()


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Onboard an Agent Employee (AI Agent as Company Employee)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Onboard default coding agent (Alexandra Chen, Engineering)
  python onboard_agent_employee.py
  
  # Onboard with custom name (email auto-generated: firstname.lastname@healops.work)
  python onboard_agent_employee.py --name "Sarah Johnson" --department "Engineering"
  
  # Onboard RCA analyst (uses default: Samuel Rodriguez, Platform Engineering)
  python onboard_agent_employee.py --role rca_analyst
  
  # Onboard with custom department
  python onboard_agent_employee.py --role log_parser --department "DevOps"
  
  # Onboard without Slack setup
  python onboard_agent_employee.py --skip-slack
  
  # Onboard with specific Slack channel
  python onboard_agent_employee.py --slack-channel "#engineering"
        """
    )
    
    parser.add_argument(
        "--role",
        type=str,
        default="coding_agent",
        choices=list(AGENT_ROLES.keys()),
        help="Agent role to onboard (default: coding_agent)"
    )
    
    parser.add_argument(
        "--name",
        type=str,
        help="Custom name for the agent (overrides default)"
    )
    
    parser.add_argument(
        "--email",
        type=str,
        help="Custom email for the agent (overrides default; auto-generated from name if not provided)"
    )
    
    parser.add_argument(
        "--department",
        type=str,
        help="Custom department for the agent (overrides default)"
    )
    
    parser.add_argument(
        "--slack-channel",
        type=str,
        help="Slack channel name (e.g., '#general') or channel ID"
    )
    
    parser.add_argument(
        "--skip-slack",
        action="store_true",
        help="Skip Slack integration setup"
    )
    
    args = parser.parse_args()
    
    result = onboard_agent(
        agent_role_key=args.role,
        custom_name=args.name,
        custom_email=args.email,
        custom_department=args.department,
        slack_channel=args.slack_channel,
        skip_slack=args.skip_slack
    )
    
    if result["success"]:
        print("\n✅ Onboarding completed successfully!")
        sys.exit(0)
    else:
        print(f"\n❌ Onboarding failed: {result.get('error', 'Unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
