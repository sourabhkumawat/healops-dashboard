#!/usr/bin/env python3
"""
Debug script to check Slack agent employee setup.
Run this to verify your agent is configured correctly.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
from models import AgentEmployee

def check_agent_setup():
    """Check if agent employees are set up correctly."""
    db = SessionLocal()
    
    try:
        agents = db.query(AgentEmployee).all()
        
        print("="*60)
        print("Agent Employee Debug Check")
        print("="*60)
        
        if not agents:
            print("❌ No agent employees found in database!")
            print("\nTo create an agent, run:")
            print("  python onboard_agent_employee.py --role coding_agent --slack-channel \"#engineering\"")
            return
        
        print(f"✅ Found {len(agents)} agent(s) in database:\n")
        
        for agent in agents:
            print(f"Agent ID: {agent.id}")
            print(f"  Name: {agent.name}")
            print(f"  Email: {agent.email}")
            print(f"  Role: {agent.role}")
            print(f"  Department: {agent.department}")
            print(f"  Status: {agent.status}")
            print(f"  Slack Channel ID: {agent.slack_channel_id or 'NOT SET'}")
            print(f"  Slack User ID: {agent.slack_user_id or 'NOT SET'}")
            print(f"  Has Bot Token: {'YES' if agent.slack_bot_token else 'NO'}")
            print()
        
        # Check environment variables
        print("="*60)
        print("Environment Variables Check")
        print("="*60)
        
        slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        
        print(f"SLACK_BOT_TOKEN: {'✅ SET' if slack_bot_token else '❌ NOT SET'}")
        if slack_bot_token:
            print(f"  Token prefix: {slack_bot_token[:12]}...")
        
        print(f"SLACK_SIGNING_SECRET: {'✅ SET' if slack_signing_secret else '❌ NOT SET'}")
        
        print("\n" + "="*60)
        print("Next Steps")
        print("="*60)
        
        if not agents or not any(a.slack_channel_id for a in agents):
            print("1. Run the onboarding script:")
            print("   python onboard_agent_employee.py --role coding_agent --slack-channel \"#engineering\"")
        
        print("2. Check your Slack app Event Subscriptions:")
        print("   - URL should be: https://engine.healops.ai/slack/events")
        print("   - Must subscribe to: app_mentions, message.im")
        
        print("3. In Slack, try mentioning the bot:")
        print("   @HealOps Agent what are you working on?")
        
        print("4. Check server logs for:")
        print("   - '📥 Received Slack event' messages")
        print("   - '🔔 App mention detected' messages")
        print("   - Any error messages")
        
    finally:
        db.close()

if __name__ == "__main__":
    check_agent_setup()
