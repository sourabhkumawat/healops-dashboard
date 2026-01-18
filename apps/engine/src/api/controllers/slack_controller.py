"""
Slack Controller - Handles Slack Events API, interactive components, and message handlers.
"""
import os
import json
import hmac
import hashlib
import time
import re
import urllib.parse
import logging
import threading
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException

from src.database.models import AgentEmployee
from src.database.database import SessionLocal
from src.services.slack.service import SlackService
from src.utils.slack_helpers import (
    get_bot_token_for_agent,
    get_bot_user_id_from_db,
    get_bot_user_id,
    get_conversation_context,
    add_to_conversation_context,
    generate_agent_response_llm,
    _conversation_contexts,
    _recently_posted_messages,
    _recently_responded_threads,
    _updating_messages
)

logger = logging.getLogger(__name__)


class SlackController:
    """Controller for Slack webhook and message handling."""
    
    @staticmethod
    async def handle_events(request: Request):
        """
        Handle Slack Events API webhook.
        Receives events like app_mentions, messages, etc.
        """
        try:
            print(f"📥 Received request to /slack/events from {request.client.host if request.client else 'unknown'}")
            print(f"📋 Headers: X-Slack-Request-Timestamp={request.headers.get('X-Slack-Request-Timestamp', 'MISSING')}, X-Slack-Signature={'present' if request.headers.get('X-Slack-Signature') else 'MISSING'}")
            
            # Read body once (can't read twice in FastAPI)
            body_bytes = await request.body()
            body_str = body_bytes.decode('utf-8')
            
            # Parse request body first to check for challenge
            try:
                data = json.loads(body_str)
            except json.JSONDecodeError:
                print("❌ Invalid JSON payload received")
                raise HTTPException(status_code=400, detail="Invalid JSON payload")
            
            # Handle URL verification challenge FIRST (before signature verification)
            # Slack sends this during initial setup and it doesn't require signature verification
            if data.get("type") == "url_verification":
                challenge = data.get("challenge")
                if challenge:
                    print(f"✅ Received Slack URL verification challenge: {challenge[:20]}...")
                    return {"challenge": challenge}
                else:
                    print("❌ Slack challenge request missing 'challenge' parameter")
                    raise HTTPException(status_code=400, detail="Challenge parameter missing")
            
            # For all other requests, verify Slack request signature
            # Support multiple signing secrets for separate bots (Alex and Morgan)
            signing_secrets = []
            
            # Try agent-specific signing secrets first
            alex_secret = os.getenv("SLACK_SIGNING_SECRET_ALEX")
            morgan_secret = os.getenv("SLACK_SIGNING_SECRET_MORGAN")
            generic_secret = os.getenv("SLACK_SIGNING_SECRET")
            
            if alex_secret:
                signing_secrets.append(("SLACK_SIGNING_SECRET_ALEX", alex_secret))
            if morgan_secret:
                signing_secrets.append(("SLACK_SIGNING_SECRET_MORGAN", morgan_secret))
            if generic_secret:
                signing_secrets.append(("SLACK_SIGNING_SECRET", generic_secret))
            
            if not signing_secrets:
                print("⚠️  WARNING: No SLACK_SIGNING_SECRET set - skipping signature verification")
            else:
                timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
                signature = request.headers.get("X-Slack-Signature", "")
                
                if not timestamp or not signature:
                    print(f"❌ Missing Slack signature headers - timestamp: {'present' if timestamp else 'MISSING'}, signature: {'present' if signature else 'MISSING'}")
                    raise HTTPException(status_code=401, detail="Missing Slack signature headers")
                
                # Check timestamp to prevent replay attacks (5 minute window)
                try:
                    timestamp_int = int(timestamp)
                    time_diff = abs(time.time() - timestamp_int)
                    if time_diff > 60 * 5:
                        print(f"❌ Request timestamp too old: {time_diff:.0f} seconds (max: 300)")
                        raise HTTPException(status_code=400, detail="Request timestamp too old")
                except ValueError as e:
                    print(f"❌ Invalid timestamp format: {timestamp} - {e}")
                    raise HTTPException(status_code=400, detail="Invalid timestamp format")
                
                # Try each signing secret until one matches
                signature_valid = False
                used_secret_name = None
                
                for secret_name, signing_secret in signing_secrets:
                    # Verify signature
                    sig_basestring = f"v0:{timestamp}:{body_str}"
                    computed_signature = "v0=" + hmac.new(
                        signing_secret.encode(),
                        sig_basestring.encode(),
                        hashlib.sha256
                    ).hexdigest()
                    
                    if hmac.compare_digest(computed_signature, signature):
                        signature_valid = True
                        used_secret_name = secret_name
                        print(f"✅ Slack signature verified successfully using {secret_name}")
                        break
                
                if not signature_valid:
                    print(f"❌ Invalid Slack signature - tried {len(signing_secrets)} secret(s)")
                    print(f"   Timestamp: {timestamp}, Body length: {len(body_str)}")
                    print(f"   Received signature: {signature[:20]}...")
                    raise HTTPException(status_code=401, detail="Invalid Slack signature")
            
            # Handle event callbacks
            if data.get("type") == "event_callback":
                event = data.get("event", {})
                event_type = event.get("type")
                
                print(f"📥 Received Slack event: type={event_type}, channel={event.get('channel', 'unknown')}")
                
                # Handle app mentions (Slack sends "app_mention" singular, not "app_mentions")
                if event_type == "app_mention" or event_type == "app_mentions":
                    # Skip bot messages to prevent recursive responses
                    subtype = event.get("subtype")
                    user_id = event.get("user")
                    if subtype == "bot_message" or not user_id:
                        print(f"⏭️  Skipping app_mention from bot/system (subtype: {subtype}, user_id: {user_id})")
                        return {"status": "ok"}
                    
                    print(f"🔔 App mention detected from user {user_id}: {event.get('text', '')[:100]}...")
                    # Pass which signing secret was used to identify which bot received this event
                    await SlackController.handle_mention(event, data.get("team_id"), used_secret_name=used_secret_name if 'used_secret_name' in locals() else None)
                    return {"status": "ok"}
                
                # Handle direct messages
                if event_type == "message" and event.get("channel_type") == "im":
                    # Skip bot messages to prevent recursive responses
                    subtype = event.get("subtype")
                    user_id = event.get("user")
                    if subtype == "bot_message" or not user_id:
                        print(f"⏭️  Skipping DM from bot/system (subtype: {subtype}, user_id: {user_id})")
                        return {"status": "ok"}
                    
                    print(f"💬 Direct message detected from user {user_id}: {event.get('text', '')[:100]}...")
                    # Pass which signing secret was used to identify which bot received this event
                    await SlackController.handle_dm(event, data.get("team_id"), used_secret_name=used_secret_name if 'used_secret_name' in locals() else None)
                    return {"status": "ok"}
                
                # Handle channel messages in threads (when user replies to bot's message)
                if event_type == "message" and event.get("channel_type") == "channel":
                    # Skip bot messages to prevent recursive responses
                    subtype = event.get("subtype")
                    event_user_id = event.get("user")
                    event_text = event.get("text", "")
                    channel_id = event.get("channel")  # Extract channel_id from event
                    
                    event_ts = event.get("ts")
                    print(f"📨 Processing channel message: user={event_user_id}, subtype={subtype}, ts={event_ts[:10] if event_ts else 'None'}..., text={event_text[:100]}...")
                    
                    # Skip bot messages, message updates, and system messages
                    if subtype == "bot_message" or subtype == "message_changed" or not event_user_id:
                        print(f"⏭️  Skipping bot/system message (subtype: {subtype}, user_id: {event_user_id})")
                        return {"status": "ok"}
                    
                    # Skip messages we're currently updating (prevent duplicate processing)
                    if event_ts and event_ts in _updating_messages:
                        print(f"⏭️  Skipping message we're currently updating (ts: {event_ts[:10]}...)")
                        return {"status": "ok"}
                    
                    # Skip messages we just posted (prevent recursive responses)
                    # BUT: Only skip if this is actually a bot message (check user_id against bot user IDs)
                    if event_ts and event_ts in _recently_posted_messages:
                        # Double-check: Is this actually from a bot? If it's from a real user, don't skip it
                        # This prevents incorrectly skipping user messages that happen to have the same timestamp
                        bot_user_id = get_bot_user_id_from_db(channel_id) or get_bot_user_id()
                        if bot_user_id and event_user_id == bot_user_id:
                            print(f"⏭️  Skipping message we just posted (ts: {event_ts[:10]}..., confirmed bot user_id: {bot_user_id})")
                            return {"status": "ok"}
                        else:
                            # This is a user message, not a bot message - don't skip it even if timestamp matches
                            print(f"⚠️  Message ts {event_ts[:10]}... is in recently_posted, but user_id {event_user_id} != bot_user_id {bot_user_id}, processing anyway")
                            # Remove from tracking to prevent future false positives
                            _recently_posted_messages.discard(event_ts)
                    
                    # Also skip if message looks like our thinking indicator (extra safeguard)
                    if "💭 Thinking" in event_text or "Thinking..." in event_text:
                        print(f"⏭️  Skipping thinking indicator message")
                        return {"status": "ok"}
                    
                    thread_ts = event.get("thread_ts")
                    # Only handle if it's a reply in a thread (has thread_ts)
                    if thread_ts:
                        # IMPORTANT: Check if this is a thread we just responded to
                        # When we post with thread_ts=ts, Slack sends it back with thread_ts=ts
                        # We need to skip it immediately
                        if thread_ts in _recently_responded_threads:
                            print(f"⏭️  Skipping message in thread we just responded to (thread_ts: {thread_ts[:10]}...)")
                            return {"status": "ok"}
                        
                        # IMPORTANT: Check if message is from bot BEFORE checking conversation context
                        # This prevents processing bot's own messages even if subtype check fails
                        # Try database first (faster), then fallback to API
                        bot_user_id = get_bot_user_id_from_db(channel_id) or get_bot_user_id()
                        if bot_user_id and event_user_id == bot_user_id:
                            print(f"⏭️  Skipping message from bot itself (user_id: {event_user_id}, bot_user_id: {bot_user_id})")
                            return {"status": "ok"}
                        
                        # Check if this thread has bot messages (conversation context exists)
                        # OR if the message mentions an agent name (handle even without context)
                        thread_id = thread_ts
                        event_text_lower = event_text.lower()
                        
                        # Check if message mentions an agent name
                        mentions_agent = False
                        try:
                            db = SessionLocal()
                            try:
                                agents = db.query(AgentEmployee).filter(
                                    AgentEmployee.slack_channel_id == channel_id
                                ).all()
                                
                                if not agents:
                                    agents = db.query(AgentEmployee).all()
                                
                                for agent in agents:
                                    if not agent.name:
                                        continue
                                        
                                    agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                                    agent_full_name = agent.name.lower() if agent.name else ""
                                    
                                    # Handle common nicknames/variations (same as mention handler)
                                    name_variations = [agent_first_name, agent_full_name]
                                    if agent_first_name == "alexandra":
                                        name_variations.extend(["alex", " alex ", "alex "])
                                    elif agent_first_name == "morgan":
                                        name_variations.append("morgan taylor")
                                    
                                    # Check if any variation matches
                                    for variation in name_variations:
                                        if variation and variation in event_text_lower:
                                            mentions_agent = True
                                            print(f"✅ Thread reply mentions agent: {agent.name} (matched: '{variation}')")
                                            break
                                    
                                    if mentions_agent:
                                        break
                            finally:
                                db.close()
                        except Exception as e:
                            print(f"⚠️  Error checking agent mentions in thread: {e}")
                        
                        # Process if we have conversation context OR if message mentions an agent
                        if thread_id in _conversation_contexts or mentions_agent:
                            print(f"💬 Thread reply detected: {event_text[:100]}...")
                            await SlackController.handle_thread_reply(event, data.get("team_id"), thread_id)
                            return {"status": "ok"}
                        else:
                            print(f"📢 Channel message in thread (no bot context, no agent mention): {event_text[:100]}...")
                    else:
                        # Handle regular channel messages that mention agent names (not in thread)
                        # Use improved matching logic similar to handle_slack_mention
                        print(f"📢 Checking channel message for agent mentions (not in thread): {event_text[:100]}...")
                        print(f"   Full text with mentions: {event_text}")
                        try:
                            db = SessionLocal()
                            try:
                                agents = db.query(AgentEmployee).filter(
                                    AgentEmployee.slack_channel_id == channel_id
                                ).all()
                                
                                if not agents:
                                    agents = db.query(AgentEmployee).all()
                                
                                print(f"🔍 Found {len(agents)} agent(s) for channel {channel_id}")
                                for agent in agents:
                                    print(f"   - Agent: {agent.name} (role: {agent.role}, slack_user_id: {agent.slack_user_id[:10] if agent.slack_user_id else 'None'}...)")
                                
                                # Extract Slack user mentions BEFORE cleaning to match by display name
                                mentioned_user_ids = []
                                mentioned_display_names = []
                                mention_pattern = r'<@([A-Z0-9]+)(?:\|([^>]+))?>'
                                for match in re.finditer(mention_pattern, event_text):
                                    mentioned_user_id = match.group(1)  # Use separate variable to avoid confusion
                                    display_name = match.group(2) if match.group(2) else None
                                    mentioned_user_ids.append(mentioned_user_id)
                                    if display_name:
                                        mentioned_display_names.append(display_name.lower())
                                
                                if mentioned_user_ids:
                                    print(f"🔍 Found Slack mentions with user IDs: {mentioned_user_ids}")
                                if mentioned_display_names:
                                    print(f"🔍 Found Slack mentions with display names: {mentioned_display_names}")
                                
                                event_text_lower = event_text.lower()
                                matched_agent = None
                                
                                # First, try to match by Slack user ID (most reliable)
                                # IMPORTANT: Check ALL agents, not just those for this channel, because
                                # the mentioned agent might be in a different channel or have no channel set
                                if mentioned_user_ids:
                                    print(f"🔍 Attempting to match by user IDs: {mentioned_user_ids}")
                                    # First check agents in channel
                                    for agent in agents:
                                        if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                                            matched_agent = agent
                                            print(f"✅ Channel message matched agent by Slack user ID (in channel): {agent.name} (ID: {agent.slack_user_id})")
                                            break
                                    
                                    # If not found in channel agents, check ALL agents
                                    if not matched_agent:
                                        print(f"   Not found in channel agents, checking all agents...")
                                        all_agents = db.query(AgentEmployee).all()
                                        print(f"   Checking {len(all_agents)} total agent(s):")
                                        for agent in all_agents:
                                            agent_id_display = agent.slack_user_id[:10] + "..." if agent.slack_user_id else "None"
                                            print(f"      - {agent.name}: slack_user_id={agent.slack_user_id if agent.slack_user_id else 'None'}")
                                            if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                                                matched_agent = agent
                                                print(f"✅ Channel message matched agent by Slack user ID (all agents): {agent.name} (ID: {agent.slack_user_id})")
                                                print(f"   Note: Agent {agent.name} is not in channel {channel_id} but was mentioned by user ID")
                                                break
                                        
                                        # If still no match and we have mentioned user IDs, try to resolve by fetching bot_user_id from Slack
                                        if not matched_agent and mentioned_user_ids:
                                            print(f"   ⚠️  No match found. Attempting to resolve user ID {mentioned_user_ids[0]} by checking agents' bot tokens...")
                                            for agent in all_agents:
                                                # Skip if agent already has a slack_user_id that doesn't match
                                                if agent.slack_user_id and agent.slack_user_id not in mentioned_user_ids:
                                                    continue
                                                
                                                # Try to get bot token and fetch bot_user_id from Slack
                                                # This works even if agent doesn't have token in DB - get_bot_token_for_agent checks env vars
                                                try:
                                                    from src.auth.crypto_utils import decrypt_token
                                                    bot_token = get_bot_token_for_agent(
                                                        agent_name=agent.name,
                                                        agent_role=agent.role,
                                                        agent_stored_token=agent.slack_bot_token
                                                    )
                                                    if bot_token:
                                                        slack_service = SlackService(bot_token)
                                                        auth_test = slack_service.test_connection()
                                                        if auth_test.get("ok"):
                                                            bot_user_id = auth_test.get("user_id")
                                                            print(f"      📡 Fetched bot_user_id for {agent.name}: {bot_user_id}")
                                                            
                                                            # Update agent's slack_user_id in database
                                                            if bot_user_id:
                                                                agent.slack_user_id = bot_user_id
                                                                # Also update channel_id if not set and we have one
                                                                if not agent.slack_channel_id and channel_id:
                                                                    agent.slack_channel_id = channel_id
                                                                    print(f"      💾 Updated {agent.name}'s slack_channel_id to {channel_id}")
                                                                db.commit()
                                                                print(f"      💾 Updated {agent.name}'s slack_user_id to {bot_user_id}")
                                                            
                                                            # Check if this matches the mentioned user ID
                                                            if bot_user_id in mentioned_user_ids:
                                                                matched_agent = agent
                                                                print(f"✅ Channel message matched agent by resolving bot_user_id: {agent.name} (ID: {bot_user_id})")
                                                                break
                                                except Exception as e:
                                                    print(f"      ⚠️  Could not resolve bot_user_id for {agent.name}: {e}")
                                                    continue
                                
                                # If no match by user ID, try to match by display name from Slack mention
                                if not matched_agent and mentioned_display_names:
                                    print(f"🔍 Attempting to match by display names: {mentioned_display_names}")
                                    # First check agents in channel
                                    for agent in agents:
                                        if not agent.name:
                                            continue
                                        agent_full_name = agent.name.lower()
                                        agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                                        
                                        print(f"   Checking agent: {agent.name} (full: '{agent_full_name}', first: '{agent_first_name}')")
                                        
                                        for display_name in mentioned_display_names:
                                            print(f"      Comparing display_name '{display_name}' with agent '{agent.name}'")
                                            if display_name == agent_full_name:
                                                matched_agent = agent
                                                print(f"✅ Channel message matched agent by exact display name: {agent.name} (display: '{display_name}')")
                                                break
                                            elif agent_first_name and display_name == agent_first_name:
                                                matched_agent = agent
                                                print(f"✅ Channel message matched agent by first name from display: {agent.name} (display: '{display_name}')")
                                                break
                                            elif agent_full_name in display_name or display_name in agent_full_name:
                                                matched_agent = agent
                                                print(f"✅ Channel message matched agent by partial display name: {agent.name} (display: '{display_name}')")
                                                break
                                        
                                        if matched_agent:
                                            break
                                    
                                    # If not found in channel agents, check ALL agents
                                    if not matched_agent:
                                        print(f"   Not found in channel agents, checking all agents for display name match...")
                                        all_agents = db.query(AgentEmployee).all()
                                        for agent in all_agents:
                                            if not agent.name:
                                                continue
                                            agent_full_name = agent.name.lower()
                                            agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                                            
                                            for display_name in mentioned_display_names:
                                                if display_name == agent_full_name:
                                                    matched_agent = agent
                                                    print(f"✅ Channel message matched agent by exact display name (all agents): {agent.name} (display: '{display_name}')")
                                                    break
                                                elif agent_first_name and display_name == agent_first_name:
                                                    matched_agent = agent
                                                    print(f"✅ Channel message matched agent by first name from display (all agents): {agent.name} (display: '{display_name}')")
                                                    break
                                                elif agent_full_name in display_name or display_name in agent_full_name:
                                                    matched_agent = agent
                                                    print(f"✅ Channel message matched agent by partial display name (all agents): {agent.name} (display: '{display_name}')")
                                                    break
                                            
                                            if matched_agent:
                                                break
                                
                                # If still no match, try to match from text content (prioritize exact matches)
                                if not matched_agent:
                                    agent_scores = []
                                    
                                    for agent in agents:
                                        if not agent.name:
                                            continue
                                        
                                        score = 0
                                        matched_pattern = None
                                        
                                        agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                                        agent_full_name = agent.name.lower()
                                        
                                        # Priority 1: Exact full name match (highest priority)
                                        if agent_full_name in event_text_lower:
                                            full_name_pattern = r'\b' + re.escape(agent_full_name) + r'\b'
                                            if re.search(full_name_pattern, event_text_lower):
                                                score = 100
                                                matched_pattern = f"exact full name: '{agent_full_name}'"
                                        
                                        # Priority 2: Full name with "morgan taylor" format
                                        if score == 0 and agent_first_name == "morgan" and "morgan taylor" in event_text_lower:
                                            score = 90
                                            matched_pattern = "full name format: 'morgan taylor'"
                                        
                                        # Priority 3: First name match (with word boundaries)
                                        if score == 0:
                                            first_name_pattern = r'\b' + re.escape(agent_first_name) + r'\b'
                                            if re.search(first_name_pattern, event_text_lower):
                                                score = 50
                                                matched_pattern = f"first name: '{agent_first_name}'"
                                        
                                        # Priority 4: Nickname matches (for Alex)
                                        if score == 0 and agent_first_name == "alexandra":
                                            if re.search(r'\balex\b', event_text_lower):
                                                score = 60
                                                matched_pattern = "nickname: 'alex'"
                                        
                                        if score > 0:
                                            agent_scores.append((score, agent, matched_pattern))
                                    
                                    # Sort by score and pick the best match
                                    if agent_scores:
                                        agent_scores.sort(key=lambda x: x[0], reverse=True)
                                        best_score, matched_agent, matched_pattern = agent_scores[0]
                                        print(f"✅ Channel message matched agent by text: {matched_agent.name} (score: {best_score}, pattern: {matched_pattern})")
                                        
                                        # If agent doesn't have slack_user_id but we have a mentioned user ID, store it
                                        if not matched_agent.slack_user_id and mentioned_user_ids:
                                            matched_agent.slack_user_id = mentioned_user_ids[0]
                                            print(f"      💾 Stored mentioned user ID {mentioned_user_ids[0]} for {matched_agent.name}")
                                        # Also update channel_id if not set
                                        if not matched_agent.slack_channel_id and channel_id:
                                            matched_agent.slack_channel_id = channel_id
                                            print(f"      💾 Stored channel_id {channel_id} for {matched_agent.name}")
                                        if not matched_agent.slack_user_id or not matched_agent.slack_channel_id:
                                            try:
                                                db.commit()
                                            except Exception as e:
                                                print(f"      ⚠️  Could not save agent updates: {e}")
                                        
                                        # Prefer exact matches if there's a tie
                                        if len(agent_scores) > 1 and agent_scores[1][0] >= best_score * 0.8:
                                            exact_matches = [a for a in agent_scores if a[0] >= 90]
                                            if exact_matches:
                                                matched_agent = exact_matches[0][1]
                                                print(f"✅ Preferring exact match: {matched_agent.name}")
                                
                                if matched_agent:
                                    print(f"💬 Processing channel message as mention for {matched_agent.name}: {event_text[:100]}...")
                                    # Pass which signing secret was used to identify which bot received this event
                                    await SlackController.handle_mention(event, data.get("team_id"), used_secret_name=used_secret_name if 'used_secret_name' in locals() else None)
                                    return {"status": "ok"}
                                else:
                                    print(f"📢 Channel message (not in thread, no agent mention detected): {event_text[:100]}...")
                            finally:
                                db.close()
                        except Exception as e:
                            print(f"⚠️  Error checking agent mentions in channel: {e}")
                            import traceback
                            traceback.print_exc()
                pass
            
            # Log if we receive an unexpected event type
            if data.get("type") != "url_verification" and data.get("type") != "event_callback":
                print(f"⚠️  Unexpected Slack event type: {data.get('type')}")
            
            return {"status": "ok"}
            
        except HTTPException:
            raise
        except Exception as e:
            import traceback
            error_msg = str(e) if e else "Unknown error (exception object is empty or None)"
            error_type = type(e).__name__ if e else "UnknownException"
            print(f"❌ Error handling Slack event: {error_type}: {error_msg}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error processing Slack event: {error_msg}")
    
    @staticmethod
    async def handle_interactive(request: Request):
        """
        Handle Slack Interactive Components (buttons, modals, etc.).
        Note: Slack sends interactive components as application/x-www-form-urlencoded.
        """
        try:
            # Read body for signature verification
            body_bytes = await request.body()
            body_str = body_bytes.decode('utf-8')
            
            # Verify Slack request signature
            # Support multiple signing secrets for separate bots (Alex and Morgan)
            signing_secrets = []
            
            # Try agent-specific signing secrets first
            alex_secret = os.getenv("SLACK_SIGNING_SECRET_ALEX")
            morgan_secret = os.getenv("SLACK_SIGNING_SECRET_MORGAN")
            generic_secret = os.getenv("SLACK_SIGNING_SECRET")
            
            if alex_secret:
                signing_secrets.append(("SLACK_SIGNING_SECRET_ALEX", alex_secret))
            if morgan_secret:
                signing_secrets.append(("SLACK_SIGNING_SECRET_MORGAN", morgan_secret))
            if generic_secret:
                signing_secrets.append(("SLACK_SIGNING_SECRET", generic_secret))
            
            if signing_secrets:
                timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
                signature = request.headers.get("X-Slack-Signature", "")
                
                if timestamp and signature:
                    # Try each signing secret until one matches
                    signature_valid = False
                    for secret_name, signing_secret in signing_secrets:
                        # Verify signature (body is already in form-encoded format)
                        sig_basestring = f"v0:{timestamp}:{body_str}"
                        computed_signature = "v0=" + hmac.new(
                            signing_secret.encode(),
                            sig_basestring.encode(),
                            hashlib.sha256
                        ).hexdigest()
                        
                        if hmac.compare_digest(computed_signature, signature):
                            signature_valid = True
                            print(f"✅ Slack interactive signature verified using {secret_name}")
                            break
                    
                    if not signature_valid:
                        print(f"❌ Invalid Slack interactive signature - tried {len(signing_secrets)} secret(s)")
                        raise HTTPException(status_code=401, detail="Invalid Slack signature")
            
            # Parse form data (need to restore body for FastAPI form parser)
            # Create a new request with the body restored
            async def receive():
                return {"type": "http.request", "body": body_bytes}
            request._receive = receive
            
            form_data = await request.form()
            payload_str = form_data.get("payload", "{}")
            payload = json.loads(payload_str)
            
            # Handle different interactive component types
            if payload.get("type") == "block_actions":
                # Handle button clicks, etc.
                pass
            elif payload.get("type") == "view_submission":
                # Handle modal submissions
                pass
            
            return {"status": "ok"}
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Error handling Slack interactive component: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Error processing Slack interaction: {str(e)}")
    
    @staticmethod
    async def handle_mention(event: Dict[str, Any], team_id: Optional[str], used_secret_name: Optional[str] = None):
        """
        Handle when the bot is mentioned in a channel.
        
        Args:
            event: Slack event data
            team_id: Slack workspace team ID
            used_secret_name: Which signing secret was used (identifies which bot received the event)
        """
        try:
            channel_id = event.get("channel")
            user_id = event.get("user")
            text = event.get("text", "")
            ts = event.get("ts")
            subtype = event.get("subtype")
            
            # Skip bot messages (bot responding to itself)
            if subtype == "bot_message" or subtype == "message_changed" or not user_id:
                print(f"⏭️  Skipping bot/system message (subtype: {subtype}, user_id: {user_id})")
                return
            
            # Skip messages we just posted (prevent recursive responses)
            if ts and ts in _recently_posted_messages:
                print(f"⏭️  Skipping message we just posted in mention handler (ts: {ts[:10]}...)")
                return
            
            # Also skip if message looks like a bot thinking indicator or response
            if "💭 Thinking" in text or "Thinking..." in text:
                print(f"⏭️  Skipping thinking indicator message")
                return
            
            # Note: We can't determine which specific bot this is from until we match the agent
            # The subtype check above should catch most bot messages
            # We'll do a more thorough check after agent matching below
            channel_id = event.get("channel")
            
            print(f"📩 Slack mention received from user {user_id}: {text[:100]}...")
            
            # Parse mention to extract agent name and query
            # Format: "@healops-agent @alexandra.chen what are you working on?"
            # Or: "@healops-agent ask Morgan what are you working on?"
            agent_name_match = None
            
            # Extract Slack user mentions BEFORE cleaning to match by display name
            mentioned_user_ids = []
            mentioned_display_names = []
            # Extract Slack user mentions: <@U123456> or <@U123456|Display Name>
            mention_pattern = r'<@([A-Z0-9]+)(?:\|([^>]+))?>'
            for match in re.finditer(mention_pattern, text):
                mentioned_user_id = match.group(1)  # Don't overwrite user_id (the actual message sender)
                display_name = match.group(2) if match.group(2) else None
                mentioned_user_ids.append(mentioned_user_id)
                if display_name:
                    mentioned_display_names.append(display_name.lower())
            
            # Clean text: Remove Slack user mention formatting like <@U123456|displayname>
            # Remove Slack user mentions: <@U123456> or <@U123456|Display Name>
            cleaned_text = re.sub(r'<@[A-Z0-9]+\|?[^>]*>', '', text)
            # Remove extra whitespace
            cleaned_text = ' '.join(cleaned_text.split())
            query = cleaned_text.lower()
            original_text_lower = text.lower()  # Keep original for matching
            
            print(f"🔍 Query after cleaning: {query[:100]}...")
            if mentioned_display_names:
                print(f"🔍 Found Slack mentions with display names: {mentioned_display_names}")
            
            # Find agent mentions in text (format: @agent-name or agent name)
            db = SessionLocal()
            try:
                # Get all agent employees for this channel
                agents = db.query(AgentEmployee).filter(
                    AgentEmployee.slack_channel_id == channel_id
                ).all()
                
                print(f"🔍 Found {len(agents)} agent(s) for channel {channel_id}")
                for idx, agent in enumerate(agents):
                    slack_id_display = f"{agent.slack_user_id[:10]}..." if agent.slack_user_id else "None"
                    print(f"   Agent {idx}: {agent.name} (role: {agent.role}, slack_user_id: {slack_id_display})")
                
                # Also log mentioned user IDs for debugging
                if mentioned_user_ids:
                    print(f"🔍 Looking for agent with slack_user_id matching: {mentioned_user_ids}")
                
                if not agents:
                    # Try to find agents without channel filter (in case channel_id format is different)
                    all_agents = db.query(AgentEmployee).all()
                    print(f"⚠️  No agents found for channel {channel_id}, but {len(all_agents)} agent(s) exist total")
                    print(f"   Channel IDs in DB: {[a.slack_channel_id for a in all_agents if a.slack_channel_id]}")
                    agents = all_agents  # Use all agents as fallback
                    for idx, agent in enumerate(agents):
                        print(f"   Agent {idx}: {agent.name} (role: {agent.role}, slack_user_id: {agent.slack_user_id[:10] if agent.slack_user_id else 'None'}...)")
                
                # First, try to match by Slack user ID (most reliable)
                # IMPORTANT: Check ALL agents, not just those for this channel, because
                # the mentioned agent might be in a different channel or have no channel set
                if mentioned_user_ids:
                    print(f"🔍 Attempting to match by user IDs: {mentioned_user_ids}")
                    # First check agents in channel
                    for agent in agents:
                        if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                            agent_name_match = agent
                            print(f"✅ Matched agent by Slack user ID (in channel): {agent.name} (ID: {agent.slack_user_id})")
                            break
                    
                    # If not found in channel agents, check ALL agents
                    if not agent_name_match:
                        print(f"   Not found in channel agents, checking all agents...")
                        all_agents = db.query(AgentEmployee).all()
                        print(f"   Checking {len(all_agents)} total agent(s):")
                        for agent in all_agents:
                            print(f"      - {agent.name}: slack_user_id={agent.slack_user_id if agent.slack_user_id else 'None'}")
                            if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                                agent_name_match = agent
                                print(f"✅ Matched agent by Slack user ID (all agents): {agent.name} (ID: {agent.slack_user_id})")
                                print(f"   Note: Agent {agent.name} is not in channel {channel_id} but was mentioned by user ID")
                                break
                        
                        # If still no match and we have mentioned user IDs, try to resolve by fetching bot_user_id from Slack
                        if not agent_name_match and mentioned_user_ids:
                            print(f"   ⚠️  No match found. Attempting to resolve user ID {mentioned_user_ids[0]} by checking agents' bot tokens...")
                            for agent in all_agents:
                                # Skip if agent's slack_user_id already matches (we already checked above)
                                if agent.slack_user_id and agent.slack_user_id in mentioned_user_ids:
                                    continue
                                
                                # Try to get bot token and fetch bot_user_id from Slack
                                # This works even if agent doesn't have token in DB - get_bot_token_for_agent checks env vars
                                try:
                                    from src.auth.crypto_utils import decrypt_token
                                    bot_token = get_bot_token_for_agent(
                                        agent_name=agent.name,
                                        agent_role=agent.role,
                                        agent_stored_token=agent.slack_bot_token
                                    )
                                    if bot_token:
                                        slack_service = SlackService(bot_token)
                                        auth_test = slack_service.test_connection()
                                        if auth_test.get("ok"):
                                            bot_user_id = auth_test.get("user_id")
                                            print(f"      📡 Fetched bot_user_id for {agent.name}: {bot_user_id}")
                                            
                                            # Update agent's slack_user_id in database
                                            if bot_user_id:
                                                agent.slack_user_id = bot_user_id
                                                # Also update channel_id if not set and we have one
                                                if not agent.slack_channel_id and channel_id:
                                                    agent.slack_channel_id = channel_id
                                                    print(f"      💾 Updated {agent.name}'s slack_channel_id to {channel_id}")
                                                db.commit()
                                                print(f"      💾 Updated {agent.name}'s slack_user_id to {bot_user_id}")
                                            
                                            # Check if this matches the mentioned user ID
                                            if bot_user_id in mentioned_user_ids:
                                                agent_name_match = agent
                                                print(f"✅ Matched agent by resolving bot_user_id: {agent.name} (ID: {bot_user_id})")
                                                break
                                except Exception as e:
                                    print(f"      ⚠️  Could not resolve bot_user_id for {agent.name}: {e}")
                                    continue
                
                # If no match by user ID, try to match by display name from Slack mention
                if not agent_name_match and mentioned_display_names:
                    print(f"🔍 Attempting to match by display names: {mentioned_display_names}")
                    # First check agents in channel
                    for agent in agents:
                        if not agent.name:
                            continue
                        agent_full_name = agent.name.lower()
                        agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                        
                        print(f"   Checking agent: {agent.name} (full: '{agent_full_name}', first: '{agent_first_name}')")
                        
                        # Check if any mentioned display name matches the agent's name
                        for display_name in mentioned_display_names:
                            print(f"      Comparing display_name '{display_name}' with agent '{agent.name}'")
                            # Exact match on full name (highest priority)
                            if display_name == agent_full_name:
                                agent_name_match = agent
                                print(f"✅ Matched agent by exact display name: {agent.name} (display: '{display_name}')")
                                break
                            # Match on first name (e.g., "morgan" matches "Morgan Taylor")
                            elif agent_first_name and display_name == agent_first_name:
                                agent_name_match = agent
                                print(f"✅ Matched agent by first name from display: {agent.name} (display: '{display_name}')")
                                break
                            # Partial match (e.g., "morgan taylor" in display name)
                            elif agent_full_name in display_name or display_name in agent_full_name:
                                agent_name_match = agent
                                print(f"✅ Matched agent by partial display name: {agent.name} (display: '{display_name}')")
                                break
                        
                        if agent_name_match:
                            break
                
                # If still no match, try to match from text content (prioritize exact matches)
                if not agent_name_match:
                    print(f"🔍 No match by user ID or display name, trying text content matching")
                    print(f"   Original text (lower): {original_text_lower[:200]}")
                    # Build match scores for each agent (higher score = better match)
                    agent_scores = []
                    
                    for agent in agents:
                        if not agent.name:
                            continue
                        
                        score = 0
                        matched_pattern = None
                        
                        # Check for agent name in mention
                        agent_first_name = agent.name.split()[0].lower() if agent.name else ""
                        agent_last_name = agent.name.split()[-1].lower() if len(agent.name.split()) > 1 else ""
                        agent_full_name = agent.name.lower()
                        
                        # Priority 1: Exact full name match (highest priority)
                        if agent_full_name in original_text_lower:
                            # Check for word boundaries to avoid partial matches
                            full_name_pattern = r'\b' + re.escape(agent_full_name) + r'\b'
                            if re.search(full_name_pattern, original_text_lower):
                                score = 100
                                matched_pattern = f"exact full name: '{agent_full_name}'"
                        
                        # Priority 2: Full name with "morgan taylor" format (for Morgan)
                        if score == 0 and agent_first_name == "morgan" and "morgan taylor" in original_text_lower:
                            score = 90
                            matched_pattern = "full name format: 'morgan taylor'"
                        
                        # Priority 3: First name match (but lower priority to avoid false matches)
                        if score == 0:
                            first_name_pattern = r'\b' + re.escape(agent_first_name) + r'\b'
                            if re.search(first_name_pattern, original_text_lower):
                                # Check if it's not a substring of another word
                                score = 50
                                matched_pattern = f"first name: '{agent_first_name}'"
                        
                        # Priority 4: Nickname matches (for Alex)
                        if score == 0 and agent_first_name == "alexandra":
                            if re.search(r'\balex\b', original_text_lower):
                                score = 60
                                matched_pattern = "nickname: 'alex'"
                        
                        # Priority 5: Role keywords (lowest priority)
                        if score == 0 and agent.role:
                            role_lower = agent.role.lower()
                            if "qa" in role_lower and "qa" in original_text_lower:
                                score = 20
                                matched_pattern = "role keyword: 'qa'"
                            elif "engineer" in role_lower and "engineer" in original_text_lower:
                                score = 10
                                matched_pattern = "role keyword: 'engineer'"
                        
                        if score > 0:
                            agent_scores.append((score, agent, matched_pattern))
                            print(f"   Agent {agent.name} scored {score} (pattern: {matched_pattern})")
                        else:
                            print(f"   Agent {agent.name} scored 0 (no match)")
                    
                    # Sort by score (highest first) and pick the best match
                    if agent_scores:
                        print(f"🔍 Found {len(agent_scores)} agent(s) with matches, scores: {[f'{a[1].name}:{a[0]}' for a in agent_scores]}")
                        agent_scores.sort(key=lambda x: x[0], reverse=True)
                        best_score, agent_name_match, matched_pattern = agent_scores[0]
                        print(f"✅ Matched agent by text: {agent_name_match.name} (score: {best_score}, pattern: {matched_pattern})")
                        
                        # If agent doesn't have slack_user_id but we have a mentioned user ID, store it
                        if not agent_name_match.slack_user_id and mentioned_user_ids:
                            agent_name_match.slack_user_id = mentioned_user_ids[0]
                            print(f"      💾 Stored mentioned user ID {mentioned_user_ids[0]} for {agent_name_match.name}")
                        # Also update channel_id if not set
                        if not agent_name_match.slack_channel_id and channel_id:
                            agent_name_match.slack_channel_id = channel_id
                            print(f"      💾 Stored channel_id {channel_id} for {agent_name_match.name}")
                        if (not agent_name_match.slack_user_id and mentioned_user_ids) or (not agent_name_match.slack_channel_id and channel_id):
                            try:
                                db.commit()
                            except Exception as e:
                                print(f"      ⚠️  Could not save agent updates: {e}")
                        
                        # If there's a tie or close scores, prefer exact matches
                        if len(agent_scores) > 1 and agent_scores[1][0] >= best_score * 0.8:
                            # Check if we have an exact full name match
                            exact_matches = [a for a in agent_scores if a[0] >= 90]
                            if exact_matches:
                                agent_name_match = exact_matches[0][1]
                                print(f"✅ Preferring exact match: {agent_name_match.name}")
                    else:
                        # No matches found in text - log why
                        print(f"⚠️  No agent matches found in text content")
                        print(f"   Original text: {text[:200]}")
                        print(f"   Original text (lower): {original_text_lower[:200]}")
                        print(f"   Mentioned user IDs: {mentioned_user_ids}")
                        print(f"   Mentioned display names: {mentioned_display_names}")
                        print(f"   Available agents: {[a.name for a in agents]}")
                
                # Only use fallback to first agent if NO mentions were detected at all
                # If mentions were detected but didn't match, that's an error - don't default
                if not agent_name_match:
                    if mentioned_user_ids or mentioned_display_names:
                        # We detected mentions but couldn't match them - this is an error
                        print(f"❌ ERROR: Detected agent mentions but couldn't match to any agent!")
                        print(f"   Mentioned user IDs: {mentioned_user_ids}")
                        print(f"   Mentioned display names: {mentioned_display_names}")
                        print(f"   Available agents: {[(a.name, a.slack_user_id) for a in agents]}")
                        print(f"   This message will NOT be processed to avoid wrong agent responding")
                        return  # Don't process - avoid wrong agent responding
                    elif agents:
                        # No mentions detected at all - safe to use first agent as fallback
                        agent_name_match = agents[0]
                        print(f"✅ No agent mentioned, using first agent in channel: {agent_name_match.name}")
                
                if not agent_name_match:
                    print(f"⚠️  No agent found for channel {channel_id}")
                    print(f"   Query text: {query}")
                    print(f"   Channel ID received: {channel_id}")
                    
                    # Only use fallback if NO mentions were detected
                    # If mentions were detected but didn't match, that's an error
                    if mentioned_user_ids or mentioned_display_names:
                        print(f"❌ ERROR: Detected agent mentions but couldn't match to any agent!")
                        print(f"   Mentioned user IDs: {mentioned_user_ids}")
                        print(f"   Mentioned display names: {mentioned_display_names}")
                        print(f"   This message will NOT be processed to avoid wrong agent responding")
                        return  # Don't process - avoid wrong agent responding
                    
                    # Try to find any agent (fallback if channel_id doesn't match AND no mentions detected)
                    all_agents = db.query(AgentEmployee).all()
                    if all_agents:
                        print(f"   Attempting to use first available agent from {len(all_agents)} total agents (no mentions detected)")
                        agent_name_match = all_agents[0]
                        print(f"   Using agent: {agent_name_match.name} (channel: {agent_name_match.slack_channel_id})")
                    else:
                        # Try to send a helpful message back to the channel
                        try:
                            bot_token = os.getenv("SLACK_BOT_TOKEN")
                            if bot_token:
                                slack_service = SlackService(bot_token)
                                slack_service.client.chat_postMessage(
                                    channel=channel_id,
                                    text="⚠️ No agent employee found in the system. Please run the onboarding script: `python onboard_agent_employee.py --role coding_agent --slack-channel \"#engineering\"`",
                                    thread_ts=ts
                                )
                        except Exception as e:
                            print(f"⚠️  Could not send error message to Slack: {e}")
                    return
                
                # Initialize Slack service
                # Get bot token using helper function (checks agent-specific tokens)
                bot_token = get_bot_token_for_agent(
                    agent_name=agent_name_match.name,
                    agent_role=agent_name_match.role,
                    agent_stored_token=agent_name_match.slack_bot_token
                )
                
                if bot_token:
                    # Determine token source for logging
                    agent_name_lower = agent_name_match.name.lower()
                    agent_role_lower = agent_name_match.role.lower() if agent_name_match.role else ""
                    if agent_name_match.slack_bot_token:
                        token_source = "stored (encrypted)"
                    elif ("morgan" in agent_name_lower or "qa" in agent_role_lower) and os.getenv("SLACK_BOT_TOKEN_MORGAN"):
                        token_source = "SLACK_BOT_TOKEN_MORGAN"
                    elif ("alex" in agent_name_lower or "alexandra" in agent_name_lower) and os.getenv("SLACK_BOT_TOKEN_ALEX"):
                        token_source = "SLACK_BOT_TOKEN_ALEX"
                    else:
                        token_source = "SLACK_BOT_TOKEN"
                    print(f"✅ Retrieved bot token ({token_source})")
                else:
                    print("⚠️  No Slack bot token available")
                    print(f"   Agent: {agent_name_match.name} ({agent_name_match.role})")
                    print(f"   Agent has stored token: {'YES' if agent_name_match.slack_bot_token else 'NO'}")
                    
                    # Check which tokens are set
                    agent_name_lower = agent_name_match.name.lower()
                    agent_role_lower = agent_name_match.role.lower() if agent_name_match.role else ""
                    if "morgan" in agent_name_lower or "qa" in agent_role_lower:
                        print(f"   Environment variable SLACK_BOT_TOKEN_MORGAN: {'SET' if os.getenv('SLACK_BOT_TOKEN_MORGAN') else 'NOT SET'}")
                    elif "alex" in agent_name_lower or "alexandra" in agent_name_lower:
                        print(f"   Environment variable SLACK_BOT_TOKEN_ALEX: {'SET' if os.getenv('SLACK_BOT_TOKEN_ALEX') else 'NOT SET'}")
                    print(f"   Environment variable SLACK_BOT_TOKEN: {'SET' if os.getenv('SLACK_BOT_TOKEN') else 'NOT SET'}")
                    
                    # Try to send error message using any available token
                    try:
                        # Last resort: try to use the raw token if it's not encrypted
                        if agent_name_match.slack_bot_token and not agent_name_match.slack_bot_token.startswith('gAAAAA'):
                            bot_token = agent_name_match.slack_bot_token
                            print(f"⚠️  Attempting to use raw token (may not be encrypted)")
                    except:
                        pass
                    
                    if not bot_token:
                        return
                
                slack_service = SlackService(bot_token)
                
                # Get the bot user ID for this specific agent's bot
                # Each agent has their own bot, so we need to use the correct bot user ID
                bot_user_id = agent_name_match.slack_user_id
                if not bot_user_id:
                    # If not stored in DB, get it from the SlackService (which uses the agent's token)
                    bot_user_id = slack_service.bot_user_id
                    # Store it in the database for future use
                    if bot_user_id:
                        try:
                            agent_name_match.slack_user_id = bot_user_id
                            db.commit()
                            print(f"💾 Stored bot user ID {bot_user_id} for {agent_name_match.name}")
                        except Exception as e:
                            print(f"⚠️  Could not store bot user ID: {e}")
                
                # CRITICAL: If a specific agent was mentioned by user ID, verify this bot is the correct one
                # This prevents Alex's bot from responding when Morgan is mentioned (and vice versa)
                if mentioned_user_ids and used_secret_name:
                    # Determine which bot received this event based on the signing secret used
                    # Get this bot's actual user ID (not the matched agent's token user ID)
                    receiving_bot_user_id = None
                    if "ALEX" in used_secret_name.upper():
                        # This event was received by Alex's bot - get Alex's bot user ID
                        alex_token = os.getenv("SLACK_BOT_TOKEN_ALEX")
                        if alex_token:
                            alex_service = SlackService(alex_token)
                            receiving_bot_user_id = alex_service.bot_user_id
                    elif "MORGAN" in used_secret_name.upper():
                        # This event was received by Morgan's bot - get Morgan's bot user ID
                        morgan_token = os.getenv("SLACK_BOT_TOKEN_MORGAN")
                        if morgan_token:
                            morgan_service = SlackService(morgan_token)
                            receiving_bot_user_id = morgan_service.bot_user_id
                    
                    # If we know which bot received this, verify it's the one that was mentioned
                    if receiving_bot_user_id:
                        if receiving_bot_user_id not in mentioned_user_ids:
                            # This bot was NOT the one mentioned - another bot should handle this
                            print(f"⏭️  Skipping: This bot ({used_secret_name}, user_id: {receiving_bot_user_id}) was not mentioned. Mentioned IDs: {mentioned_user_ids}")
                            print(f"   Another bot (the one with ID matching {mentioned_user_ids}) should respond instead")
                            return
                
                # Check if message is from this specific bot (prevent recursive responses)
                if bot_user_id and user_id == bot_user_id:
                    print(f"⏭️  Skipping message from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id} for {agent_name_match.name})")
                    return
                
                # Use thread_ts as conversation context ID (but don't create context yet)
                # We'll create context AFTER posting to prevent recursive responses
                thread_id = event.get("thread_ts") or ts  # Use existing thread or create new one
                
                # Get conversation history (if it exists) but don't create new context yet
                conversation_history = None
                if thread_id in _conversation_contexts:
                    conversation_history = get_conversation_context(thread_id)
                    print(f"📚 Found existing conversation context with {len(conversation_history)} messages")
                else:
                    print(f"📝 No existing conversation context, starting new thread")
                
                # Refresh agent status from database to get latest status (in case it changed)
                db.refresh(agent_name_match)
                logger.debug(
                    "Refreshed agent status from database",
                    extra={
                        "agent_name": agent_name_match.name,
                        "status": agent_name_match.status,
                        "has_current_task": bool(agent_name_match.current_task)
                    }
                )
                
                # Post thinking indicator immediately to show bot is processing
                thinking_ts = slack_service.post_thinking_indicator(channel_id, thread_ts=ts)
                
                # Generate LLM-powered response with conversation context (this takes time)
                # Pass conversation_history but don't add to context yet
                response_text = generate_agent_response_llm(agent_name_match, query, thread_id=None, conversation_history=conversation_history)
                
                # Delete the thinking indicator and post the actual response
                # This prevents duplicate messages that can occur when updating
                if thinking_ts:
                    try:
                        # Delete the thinking indicator message
                        slack_service.client.chat_delete(channel=channel_id, ts=thinking_ts)
                        print(f"✅ Deleted thinking indicator message")
                    except Exception as e:
                        print(f"⚠️  Could not delete thinking indicator: {e}")
                        # Continue to post response anyway
                    
                    # Post the actual response
                    response = slack_service.client.chat_postMessage(
                        channel=channel_id,
                        text=response_text,
                        thread_ts=ts
                    )
                    # Track this message to prevent recursive processing
                    if response.get("ok") and response.get("ts"):
                        response_ts = response.get("ts")
                        _recently_posted_messages.add(response_ts)
                        
                        # Track this thread as recently responded to
                        # When we post with thread_ts=ts, Slack sends it back with thread_ts=ts
                        # We need to skip it when it comes back
                        if ts:
                            _recently_responded_threads.add(ts)
                            print(f"🔒 Tracked thread {ts[:10]}... as recently responded to")
                            # Clean up after 3 seconds (enough time for Slack to send events back)
                            def clear_thread_tracking():
                                time.sleep(3)
                                _recently_responded_threads.discard(ts)
                                print(f"🔓 Removed thread {ts[:10]}... from tracking")
                            threading.Thread(target=clear_thread_tracking, daemon=True).start()
                        
                        # NOW add to conversation context AFTER successfully posting
                        # Use a delay to prevent the bot from responding to its own messages
                        def add_context_after_posting():
                            time.sleep(2)  # Wait 2 seconds before adding context to prevent recursion
                            # Only add context if thread_id is valid (not None)
                            if thread_id:
                                # Clean up the query (remove bot mentions)
                                clean_query = query
                                add_to_conversation_context(thread_id, "user", clean_query)
                                add_to_conversation_context(thread_id, "assistant", response_text)
                                print(f"📚 Added conversation context for thread {thread_id[:10]}... (after 2s delay)")
                        
                        threading.Thread(target=add_context_after_posting, daemon=True).start()
                        
                        print(f"✅ Posted response message (ts: {response_ts[:10]}...), tracked to prevent recursion")
                        print(f"   Tracked messages: {len(_recently_posted_messages)}")
                        print(f"   Tracked threads: {len(_recently_responded_threads)}")
                        # Clean up after 10 seconds
                        def clear_posted_message():
                            time.sleep(10)
                            _recently_posted_messages.discard(response_ts)
                            print(f"🗑️  Removed message {response_ts[:10]}... from tracking")
                        threading.Thread(target=clear_posted_message, daemon=True).start()
                    else:
                        print(f"⚠️  Failed to post response or get timestamp: {response}")
                        print(f"✅ Posted response message (no tracking)")
                else:
                    # Fallback: if thinking indicator failed, just post the response
                    print(f"⚠️  No thinking_ts available, posting response as new message")
                    response = slack_service.client.chat_postMessage(
                        channel=channel_id,
                        text=response_text,
                        thread_ts=ts
                    )
                    # Track this message to prevent recursive processing
                    if response.get("ok") and response.get("ts"):
                        response_ts = response.get("ts")
                        _recently_posted_messages.add(response_ts)
                        print(f"✅ Posted response message fallback (ts: {response_ts[:10]}...)")
                        # Clean up after 10 seconds
                        def clear_posted_message():
                            time.sleep(10)
                            _recently_posted_messages.discard(response_ts)
                        threading.Thread(target=clear_posted_message, daemon=True).start()
            
            finally:
                db.close()
                
        except Exception as e:
            print(f"❌ Error handling Slack mention: {e}")
            import traceback
            traceback.print_exc()
            # Try to send error message to Slack if possible
            try:
                if 'slack_service' in locals() and 'channel_id' in locals():
                    slack_service.client.chat_postMessage(
                        channel=channel_id,
                        text=f"⚠️ Sorry, I encountered an error: {str(e)}"
                    )
            except:
                pass  # Don't fail if we can't send error message
    
    @staticmethod
    async def handle_dm(event: Dict[str, Any], team_id: Optional[str], used_secret_name: Optional[str] = None):
        """
        Handle direct messages to the bot.
        
        Args:
            event: Slack event data
            team_id: Slack workspace team ID
            used_secret_name: Which signing secret was used (identifies which bot received the event)
        """
        try:
            # Similar to handle_slack_mention but for DMs
            # For now, redirect to mention handler
            await SlackController.handle_mention(event, team_id, used_secret_name=used_secret_name)
        except Exception as e:
            print(f"❌ Error handling Slack DM: {e}")
            import traceback
            traceback.print_exc()
    
    @staticmethod
    async def handle_thread_reply(event: Dict[str, Any], team_id: Optional[str], thread_id: str):
        """
        Handle when a user replies to the bot's message in a thread.
        
        Args:
            event: Slack event data
            team_id: Slack workspace team ID
            thread_id: Thread timestamp (used as conversation context ID)
        """
        try:
            channel_id = event.get("channel")
            user_id = event.get("user")
            text = event.get("text", "")
            ts = event.get("ts")
            thread_ts = event.get("thread_ts")
            subtype = event.get("subtype")
            
            # Skip bot messages and system messages to prevent recursive responses
            if subtype == "bot_message" or subtype == "message_changed" or not user_id:
                print(f"⏭️  Skipping bot/system message in thread (subtype: {subtype}, user_id: {user_id})")
                return
            
            # Skip messages we just posted (prevent recursive responses)
            if ts and ts in _recently_posted_messages:
                print(f"⏭️  Skipping message we just posted in thread reply handler (ts: {ts[:10]}...)")
                return
            
            # Also skip if message looks like a bot thinking indicator to prevent recursive responses
            if "💭 Thinking" in text or "Thinking..." in text:
                print(f"⏭️  Skipping thinking indicator message in thread")
                return
            
            # Early check: Get bot user ID and verify message is not from bot
            bot_user_id = None
            if channel_id:
                bot_user_id = get_bot_user_id_from_db(channel_id) or get_bot_user_id()
                if bot_user_id and user_id == bot_user_id:
                    print(f"⏭️  Skipping thread reply from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id})")
                    return
            
            print(f"💬 Thread reply received from user {user_id}: {text[:100]}...")
            
            # Find agent for this channel
            db = SessionLocal()
            try:
                # Get agent for this channel
                agents = db.query(AgentEmployee).filter(
                    AgentEmployee.slack_channel_id == channel_id
                ).all()
                
                if not agents:
                    # Try to find any agent
                    agents = db.query(AgentEmployee).all()
                
                if not agents:
                    print(f"⚠️  No agent found for thread reply")
                    return
                
                # Try to match agent name from text (similar to mention handler)
                # Extract Slack user mentions BEFORE cleaning to match by display name
                mentioned_user_ids = []
                mentioned_display_names = []
                # Extract Slack user mentions: <@U123456> or <@U123456|Display Name>
                mention_pattern = r'<@([A-Z0-9]+)(?:\|([^>]+))?>'
                for match in re.finditer(mention_pattern, text):
                    mentioned_user_id = match.group(1)  # Don't overwrite user_id (the actual message sender)
                    display_name = match.group(2) if match.group(2) else None
                    mentioned_user_ids.append(mentioned_user_id)
                    if display_name:
                        mentioned_display_names.append(display_name.lower())
                
                text_lower = text.lower()
                agent = None
                
                # First, try to match by Slack user ID (most reliable)
                # IMPORTANT: Check ALL agents, not just those for this channel, because
                # the mentioned agent might be in a different channel or have no channel set
                if mentioned_user_ids:
                    print(f"🔍 Attempting to match by user IDs: {mentioned_user_ids}")
                    # First check agents in channel
                    for candidate_agent in agents:
                        if candidate_agent.slack_user_id and candidate_agent.slack_user_id in mentioned_user_ids:
                            agent = candidate_agent
                            print(f"✅ Thread reply matched agent by Slack user ID (in channel): {agent.name} (ID: {candidate_agent.slack_user_id})")
                            break
                    
                    # If not found in channel agents, check ALL agents
                    if not agent:
                        print(f"   Not found in channel agents, checking all agents...")
                        all_agents = db.query(AgentEmployee).all()
                        print(f"   Checking {len(all_agents)} total agent(s):")
                        for candidate_agent in all_agents:
                            print(f"      - {candidate_agent.name}: slack_user_id={candidate_agent.slack_user_id if candidate_agent.slack_user_id else 'None'}")
                            if candidate_agent.slack_user_id and candidate_agent.slack_user_id in mentioned_user_ids:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by Slack user ID (all agents): {agent.name} (ID: {candidate_agent.slack_user_id})")
                                print(f"   Note: Agent {agent.name} is not in channel {channel_id} but was mentioned by user ID")
                                break
                        
                        # If still no match and we have mentioned user IDs, try to resolve by fetching bot_user_id from Slack
                        if not agent and mentioned_user_ids:
                            print(f"   ⚠️  No match found. Attempting to resolve user ID {mentioned_user_ids[0]} by checking agents' bot tokens...")
                            for candidate_agent in all_agents:
                                # Skip if agent already has a slack_user_id that doesn't match
                                if candidate_agent.slack_user_id:
                                    continue
                                
                                # Try to get bot token and fetch bot_user_id from Slack
                                try:
                                    from src.auth.crypto_utils import decrypt_token
                                    bot_token = get_bot_token_for_agent(
                                        agent_name=candidate_agent.name,
                                        agent_role=candidate_agent.role,
                                        agent_stored_token=candidate_agent.slack_bot_token
                                    )
                                    if bot_token:
                                        slack_service = SlackService(bot_token)
                                        auth_test = slack_service.test_connection()
                                        if auth_test.get("ok"):
                                            bot_user_id = auth_test.get("user_id")
                                            print(f"      📡 Fetched bot_user_id for {candidate_agent.name}: {bot_user_id}")
                                            
                                            # Update agent's slack_user_id in database
                                            if bot_user_id:
                                                candidate_agent.slack_user_id = bot_user_id
                                                db.commit()
                                                print(f"      💾 Updated {candidate_agent.name}'s slack_user_id to {bot_user_id}")
                                            
                                            # Check if this matches the mentioned user ID
                                            if bot_user_id in mentioned_user_ids:
                                                agent = candidate_agent
                                                print(f"✅ Thread reply matched agent by resolving bot_user_id: {agent.name} (ID: {bot_user_id})")
                                                break
                                except Exception as e:
                                    print(f"      ⚠️  Could not resolve bot_user_id for {candidate_agent.name}: {e}")
                                    continue
                
                # If no match by user ID, try to match by display name from Slack mention
                if not agent and mentioned_display_names:
                    print(f"🔍 Attempting to match by display names: {mentioned_display_names}")
                    # First check agents in channel
                    for candidate_agent in agents:
                        if not candidate_agent.name:
                            continue
                        agent_full_name = candidate_agent.name.lower()
                        agent_first_name = candidate_agent.name.split()[0].lower() if candidate_agent.name else ""
                        
                        print(f"   Checking agent: {candidate_agent.name} (full: '{agent_full_name}', first: '{agent_first_name}')")
                        
                        # Check if any mentioned display name matches the agent's name
                        for display_name in mentioned_display_names:
                            print(f"      Comparing display_name '{display_name}' with agent '{candidate_agent.name}'")
                            # Exact match on full name (highest priority)
                            if display_name == agent_full_name:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by exact display name: {agent.name} (display: '{display_name}')")
                                break
                            # Match on first name (e.g., "morgan" matches "Morgan Taylor")
                            elif agent_first_name and display_name == agent_first_name:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by first name from display: {agent.name} (display: '{display_name}')")
                                break
                            # Partial match (e.g., "morgan taylor" in display name)
                            elif agent_full_name in display_name or display_name in agent_full_name:
                                agent = candidate_agent
                                print(f"✅ Thread reply matched agent by partial display name: {agent.name} (display: '{display_name}')")
                                break
                        
                        if agent:
                            break
                    
                    # If not found in channel agents, check ALL agents
                    if not agent:
                        print(f"   Not found in channel agents, checking all agents for display name match...")
                        all_agents = db.query(AgentEmployee).all()
                        for candidate_agent in all_agents:
                            if not candidate_agent.name:
                                continue
                            agent_full_name = candidate_agent.name.lower()
                            agent_first_name = candidate_agent.name.split()[0].lower() if candidate_agent.name else ""
                            
                            for display_name in mentioned_display_names:
                                if display_name == agent_full_name:
                                    agent = candidate_agent
                                    print(f"✅ Thread reply matched agent by exact display name (all agents): {agent.name} (display: '{display_name}')")
                                    break
                                elif agent_first_name and display_name == agent_first_name:
                                    agent = candidate_agent
                                    print(f"✅ Thread reply matched agent by first name from display (all agents): {agent.name} (display: '{display_name}')")
                                    break
                                elif agent_full_name in display_name or display_name in agent_full_name:
                                    agent = candidate_agent
                                    print(f"✅ Thread reply matched agent by partial display name (all agents): {agent.name} (display: '{display_name}')")
                                    break
                            
                            if agent:
                                break
                
                # If still no match, try to match from text content (prioritize exact matches)
                if not agent:
                    # Build match scores for each agent (higher score = better match)
                    agent_scores = []
                    
                    for candidate_agent in agents:
                        if not candidate_agent.name:
                            continue
                        
                        score = 0
                        matched_pattern = None
                        
                        agent_first_name = candidate_agent.name.split()[0].lower() if candidate_agent.name else ""
                        agent_full_name = candidate_agent.name.lower()
                        
                        # Priority 1: Exact full name match (highest priority)
                        if agent_full_name in text_lower:
                            # Check for word boundaries to avoid partial matches
                            full_name_pattern = r'\b' + re.escape(agent_full_name) + r'\b'
                            if re.search(full_name_pattern, text_lower):
                                score = 100
                                matched_pattern = f"exact full name: '{agent_full_name}'"
                        
                        # Priority 2: Full name with "morgan taylor" format (for Morgan)
                        if score == 0 and agent_first_name == "morgan" and "morgan taylor" in text_lower:
                            score = 90
                            matched_pattern = "full name format: 'morgan taylor'"
                        
                        # Priority 3: First name match (but lower priority to avoid false matches)
                        if score == 0:
                            first_name_pattern = r'\b' + re.escape(agent_first_name) + r'\b'
                            if re.search(first_name_pattern, text_lower):
                                score = 50
                                matched_pattern = f"first name: '{agent_first_name}'"
                        
                        # Priority 4: Nickname matches (for Alex)
                        if score == 0 and agent_first_name == "alexandra":
                            if re.search(r'\balex\b', text_lower):
                                score = 60
                                matched_pattern = "nickname: 'alex'"
                        
                        if score > 0:
                            agent_scores.append((score, candidate_agent, matched_pattern))
                    
                    # Sort by score (highest first) and pick the best match
                    if agent_scores:
                        agent_scores.sort(key=lambda x: x[0], reverse=True)
                        best_score, agent, matched_pattern = agent_scores[0]
                        print(f"✅ Thread reply matched agent by text: {agent.name} (score: {best_score}, pattern: {matched_pattern})")
                        
                        # If there's a tie or close scores, prefer exact matches
                        if len(agent_scores) > 1 and agent_scores[1][0] >= best_score * 0.8:
                            # Check if we have an exact full name match
                            exact_matches = [a for a in agent_scores if a[0] >= 90]
                            if exact_matches:
                                agent = exact_matches[0][1]
                                print(f"✅ Preferring exact match: {agent.name}")
                
                # Only use fallback to first agent if NO mentions were detected at all
                # If mentions were detected but didn't match, that's an error - don't default
                if not agent:
                    if mentioned_user_ids or mentioned_display_names:
                        # We detected mentions but couldn't match them - this is an error
                        print(f"❌ ERROR: Detected agent mentions in thread reply but couldn't match to any agent!")
                        print(f"   Mentioned user IDs: {mentioned_user_ids}")
                        print(f"   Mentioned display names: {mentioned_display_names}")
                        print(f"   Available agents: {[(a.name, a.slack_user_id) for a in agents]}")
                        print(f"   This thread reply will NOT be processed to avoid wrong agent responding")
                        return  # Don't process - avoid wrong agent responding
                    elif agents:
                        # No mentions detected at all - safe to use first agent as fallback
                        agent = agents[0]
                        print(f"✅ No agent mentioned in thread reply, using first agent in channel: {agent.name}")
                
                # Get bot token using helper function (checks agent-specific tokens)
                bot_token = get_bot_token_for_agent(
                    agent_name=agent.name,
                    agent_role=agent.role,
                    agent_stored_token=agent.slack_bot_token
                )
                
                if not bot_token:
                    print("⚠️  No Slack bot token available for thread reply")
                    print(f"   Agent: {agent.name} ({agent.role})")
                    return
                
                slack_service = SlackService(bot_token)
                
                # Get the bot user ID for this specific agent's bot
                # Each agent has their own bot, so we need to use the correct bot user ID
                bot_user_id = agent.slack_user_id
                if not bot_user_id:
                    # If not stored in DB, get it from the SlackService (which uses the agent's token)
                    bot_user_id = slack_service.bot_user_id
                    # Store it in the database for future use
                    if bot_user_id:
                        try:
                            agent.slack_user_id = bot_user_id
                            db.commit()
                            print(f"💾 Stored bot user ID {bot_user_id} for {agent.name}")
                        except Exception as e:
                            print(f"⚠️  Could not store bot user ID: {e}")
                
                # Check if message is from this specific bot (prevent recursive responses)
                if bot_user_id and user_id == bot_user_id:
                    print(f"⏭️  Skipping thread reply from bot itself (user_id: {user_id}, bot_user_id: {bot_user_id} for {agent.name})")
                    return
                
                # Refresh agent status from database to get latest status (in case it changed)
                db.refresh(agent)
                logger.debug(
                    "Refreshed agent status from database for thread reply",
                    extra={
                        "agent_name": agent.name,
                        "status": agent.status,
                        "has_current_task": bool(agent.current_task)
                    }
                )
                
                # Use thread_ts as conversation context ID (parameter thread_id is the thread timestamp)
                conversation_thread_id = thread_ts or thread_id or ts  # Use thread_ts if available, otherwise use thread_id parameter or ts
                
                # Post thinking indicator immediately to show bot is processing
                thinking_ts = slack_service.post_thinking_indicator(channel_id, thread_ts=thread_ts)
                
                # Get existing conversation history (if thread exists) but don't create new context
                conversation_history = None
                if conversation_thread_id and conversation_thread_id in _conversation_contexts:
                    conversation_history = get_conversation_context(conversation_thread_id)
                    print(f"📚 Found existing conversation context with {len(conversation_history)} messages")
                
                # Generate LLM-powered response with conversation context (this takes time)
                # Pass conversation_history but don't add to context yet
                response_text = generate_agent_response_llm(agent, text, thread_id=None, conversation_history=conversation_history)
                
                # Delete the thinking indicator and post the actual response
                # This prevents duplicate messages that can occur when updating
                if thinking_ts:
                    try:
                        # Delete the thinking indicator message
                        slack_service.client.chat_delete(channel=channel_id, ts=thinking_ts)
                        print(f"✅ Deleted thinking indicator message in thread")
                    except Exception as e:
                        print(f"⚠️  Could not delete thinking indicator in thread: {e}")
                        # Continue to post response anyway
                    
                    # Post the actual response
                    response = slack_service.client.chat_postMessage(
                        channel=channel_id,
                        text=response_text,
                        thread_ts=thread_ts
                    )
                    # Track this message to prevent recursive processing
                    if response.get("ok") and response.get("ts"):
                        response_ts = response.get("ts")
                        _recently_posted_messages.add(response_ts)
                        
                        # Track this thread as recently responded to
                        # When we post with thread_ts=thread_ts, Slack will send it back with thread_ts=thread_ts
                        if thread_ts:
                            _recently_responded_threads.add(thread_ts)
                            print(f"🔒 Tracked thread {thread_ts[:10]}... as recently responded to")
                            # Clean up after 3 seconds
                            def clear_thread_tracking():
                                time.sleep(3)
                                _recently_responded_threads.discard(thread_ts)
                                print(f"🔓 Removed thread {thread_ts[:10]}... from tracking")
                            threading.Thread(target=clear_thread_tracking, daemon=True).start()
                        
                        # Add to conversation context AFTER posting with delay
                        def add_context_after_posting():
                            time.sleep(2)  # Wait 2 seconds before adding context to prevent recursion
                            if thread_id:
                                add_to_conversation_context(thread_id, "user", text)
                                add_to_conversation_context(thread_id, "assistant", response_text)
                                print(f"📚 Added conversation context for thread {thread_id[:10]}... (after 2s delay)")
                        
                        threading.Thread(target=add_context_after_posting, daemon=True).start()
                        
                        print(f"✅ Posted thread reply (ts: {response_ts[:10]}...), tracked to prevent recursion")
                        print(f"   Tracked threads: {len(_recently_responded_threads)}")
                        # Clean up after 10 seconds
                        def clear_posted_message():
                            time.sleep(10)
                            _recently_posted_messages.discard(response_ts)
                        threading.Thread(target=clear_posted_message, daemon=True).start()
                    else:
                        print(f"✅ Posted thread reply")
                else:
                    # Fallback: if thinking indicator failed, just post the response
                    print(f"⚠️  No thinking_ts available for thread, posting response as new message")
                    response = slack_service.client.chat_postMessage(
                        channel=channel_id,
                        text=response_text,
                        thread_ts=thread_ts
                    )
                    # Track this message to prevent recursive processing
                    if response.get("ok") and response.get("ts"):
                        response_ts = response.get("ts")
                        _recently_posted_messages.add(response_ts)
                        print(f"✅ Posted thread reply fallback (ts: {response_ts[:10]}...)")
                        # Clean up after 10 seconds
                        def clear_posted_message():
                            time.sleep(10)
                            _recently_posted_messages.discard(response_ts)
                        threading.Thread(target=clear_posted_message, daemon=True).start()
                    else:
                        print(f"✅ Sent thread reply")
            
            finally:
                db.close()
                
        except Exception as e:
            print(f"❌ Error handling thread reply: {e}")
            import traceback
            traceback.print_exc()
