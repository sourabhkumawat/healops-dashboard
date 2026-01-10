#!/usr/bin/env python3
"""
Update agent employee's Slack bot token.
Use this if the token needs to be refreshed or re-encrypted.
"""

import os
import sys
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.database.database import SessionLocal
from src.database.models import AgentEmployee
from src.auth.crypto_utils import encrypt_token

def update_agent_token(agent_email: str = None):
    """Update agent's Slack bot token from environment variable."""
    db = SessionLocal()
    
    try:
        # Get agent
        if agent_email:
            agent = db.query(AgentEmployee).filter(AgentEmployee.email == agent_email).first()
        else:
            # Get first agent
            agent = db.query(AgentEmployee).first()
        
        if not agent:
            print("❌ No agent found")
            if agent_email:
                print(f"   Searched for email: {agent_email}")
            return
        
        print(f"Found agent: {agent.name} ({agent.email})")
        
        # Get token from environment
        slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        if not slack_bot_token:
            print("❌ SLACK_BOT_TOKEN not found in environment variables")
            print("   Make sure it's set in .env file or environment")
            return
        
        print(f"✅ Found token (prefix: {slack_bot_token[:12]}...)")
        
        # Encrypt and store
        encrypted_token = encrypt_token(slack_bot_token)
        agent.slack_bot_token = encrypted_token
        
        db.commit()
        db.refresh(agent)
        
        print(f"✅ Successfully updated agent's Slack bot token")
        print(f"   Agent: {agent.name}")
        print(f"   Token encrypted and stored")
        
    except Exception as e:
        print(f"❌ Error updating token: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Update agent's Slack bot token")
    parser.add_argument("--email", help="Agent email (optional, uses first agent if not provided)")
    args = parser.parse_args()
    
    update_agent_token(args.email)
