# Slack Employee Onboarding Guide

This guide explains how to onboard agent employees (AI agents) to Slack as bot users, enabling them to communicate about their work.

## Overview

Each agent employee needs:
1. **Slack Bot User Identity** - A bot user that represents the agent
2. **Channel Access** - Ability to post updates in relevant channels
3. **Event Subscriptions** - Ability to receive mentions and respond to queries
4. **Bot Profile** - Custom name, avatar, and display info

## Architecture Options

### Option A: Single Bot with Agent Identity (Recommended)
- One Slack App with one bot user
- Each agent posts messages with their name/avatar in the message
- Use Slack Block Kit to display agent identity
- **Pros**: Simpler setup, one OAuth flow, easier management
- **Cons**: All agents share the same bot user ID

### Option B: Multiple Bots (One per Agent)
- Each agent gets their own Slack App and bot user
- **Pros**: True separate identities, better isolation
- **Cons**: Complex setup, multiple OAuth flows, more maintenance

**We'll use Option A for this implementation.**

## Step 1: One-Time Slack App Setup

### 1.1 Create Slack App

1. Go to https://api.slack.com/apps
2. Click **"Create New App"** → **"From scratch"**
3. Fill in:
   - **App Name**: `HealOps Agents` (or your company name)
   - **Workspace**: Select your workspace
4. Click **"Create App"**

### 1.2 Configure Bot User

1. In the app settings, go to **"OAuth & Permissions"** (left sidebar)
2. Scroll to **"Bot Token Scopes"**
3. Add the following scopes:
   ```
   chat:write              # Post messages
   chat:write.public       # Post to public channels
   channels:read           # Read channel info
   channels:join           # Join channels
   groups:read             # Read private channel info
   groups:write            # Post to private channels
   users:read              # Read user info
   users:read.email        # Read user emails
   app_mentions:read       # Receive @mentions
   im:read                 # Read DMs
   im:write                # Send DMs
   files:write             # Upload files (optional, for code snippets)
   ```

4. Scroll to **"Scopes"** → **"User Token Scopes"** (keep empty for bot-only)

### 1.3 Install App to Workspace

1. In **"OAuth & Permissions"**, scroll to top
2. Click **"Install to Workspace"**
3. Review permissions and click **"Allow"**
4. Copy the **"Bot User OAuth Token"** (starts with `xoxb-`)
5. Save this token as environment variable:
   ```bash
   export SLACK_BOT_TOKEN='xoxb-your-token-here'
   ```

### 1.4 Configure Event Subscriptions

1. Go to **"Event Subscriptions"** (left sidebar)
2. Enable **"Enable Events"**
3. Set **Request URL** to: `https://your-domain.com/slack/events`
   - This will receive Slack events (mentions, messages, etc.)
   - Slack will verify this URL with a challenge
4. Under **"Subscribe to bot events"**, add:
   ```
   app_mentions      # When bot is @mentioned
   message.channels  # Messages in public channels (if needed)
   message.groups    # Messages in private channels (if needed)
   message.im        # Direct messages to bot
   ```
5. Click **"Save Changes"**

### 1.5 Configure Interactive Components (Optional)

1. Go to **"Interactivity & Shortcuts"** (left sidebar)
2. Enable **"Interactivity"**
3. Set **Request URL** to: `https://your-domain.com/slack/interactive`
4. Click **"Save Changes"**

### 1.6 Set Bot Profile (Optional but Recommended)

1. Go to **"App Home"** (left sidebar)
2. Under **"Your App's Presence in Slack"**:
   - Set **Display Name**: `HealOps Agent` (or company name)
   - Set **Default Username**: `healops-agent`
   - Upload **Icon**: Use your company logo or agent avatar
   - Set **Description**: "AI Agent employees for automated incident resolution"
3. Click **"Save Changes"**

## Step 2: Onboard Individual Agent Employee

### 2.1 Run Onboarding Script

```bash
# Onboard coding agent (Alexandra Chen)
python onboard_agent_employee.py --role coding_agent --slack-channel "#engineering"

# Onboard with custom channel
python onboard_agent_employee.py --role rca_analyst --slack-channel "#incidents"
```

### 2.2 What the Script Does

The onboarding script will:
1. ✅ Check for `SLACK_BOT_TOKEN` environment variable
2. ✅ Test connection to Slack API
3. ✅ Verify bot has required permissions
4. ✅ Find or create Slack channel
5. ✅ Invite bot to channel (if not already member)
6. ✅ Store channel ID and bot user ID in database
7. ✅ Configure agent's Slack identity

### 2.3 Manual Steps (if needed)

#### Invite Bot to Channel

If the bot isn't automatically invited, manually add it:

1. In Slack, go to your channel (e.g., `#engineering`)
2. Type: `/invite @HealOps Agent` (or your bot's display name)
3. Or go to channel settings → **Members** → **Add people** → Search for bot

#### Set Channel-Specific Settings

1. In channel settings, ensure bot has permission to post
2. Optional: Pin a welcome message introducing the agent:
   ```
   👋 Hey team! I'm Alexandra Chen, your AI coding agent.
   I'll post updates here about code fixes, PRs, and incidents I'm working on.
   Feel free to @mention me to ask about my work!
   ```

## Step 3: Agent Communication Setup

### 3.1 Automatic Updates

Agents will automatically post to Slack when:
- ✅ Starting work on a task
- ✅ Completing a task
- ✅ Encountering errors
- ✅ Creating pull requests
- ✅ Resolving incidents

**Example messages:**
```
🚀 [Alexandra Chen] Starting work on fixing null pointer exception in UserService
✅ [Alexandra Chen] Completed code fix. Created PR #123: Fix null pointer in UserService
⚠️ [Samuel Rodriguez] Error in root cause analysis: Missing log context
```

### 3.2 On-Demand Queries

Team members can ask agents about their work:

**Examples:**
```
@alexandra.chen what are you working on?
@samuel.rodriguez what incidents did you resolve today?
@maya.patel show me your completed tasks
```

The bot will parse these queries and respond with agent-specific information.

### 3.3 Message Format

Each agent message includes:
- **Agent Name** (e.g., "Alexandra Chen")
- **Department** (e.g., "Engineering")
- **Current Task** (if working)
- **Completed Tasks** (recent work)
- **Status** (available, working, idle)

## Step 4: Environment Variables

Set these in your environment or `.env` file:

```bash
# Required
SLACK_BOT_TOKEN=xoxb-your-bot-token-here

# Optional
SLACK_DEFAULT_CHANNEL=#general
SLACK_SIGNING_SECRET=your-signing-secret  # For event verification
SLACK_WEBHOOK_URL=https://your-domain.com/slack/events
```

## Step 5: Verify Setup

### Test Bot Connection

```bash
python -c "
import os
from slack_sdk import WebClient
client = WebClient(token=os.getenv('SLACK_BOT_TOKEN'))
response = client.auth_test()
print(f'Bot: {response[\"user\"]}')
print(f'Team: {response[\"team\"]}')
print(f'User ID: {response[\"user_id\"]}')
"
```

### Test Message Posting

```bash
python -c "
import os
from slack_sdk import WebClient
client = WebClient(token=os.getenv('SLACK_BOT_TOKEN'))
response = client.chat_postMessage(
    channel='#general',
    text='👋 Test message from HealOps agent!'
)
print(f'Message sent: {response[\"ts\"]}')
"
```

### Test Agent Onboarding

```bash
# Test onboarding with Slack
python onboard_agent_employee.py --role coding_agent --slack-channel "#general"
```

## Troubleshooting

### Bot Not Receiving Events

1. **Check Event Subscriptions**: Ensure URL is correct and verified
2. **Check Scopes**: Bot must have `app_mentions:read` scope
3. **Check Channel**: Bot must be member of channel where it's mentioned
4. **Check Logs**: Check server logs for incoming webhook requests

### Bot Can't Post Messages

1. **Check Permissions**: Ensure `chat:write` and `chat:write.public` scopes
2. **Check Channel**: Bot must be member of channel
3. **Check Channel Type**: Private channels need `groups:write` scope
4. **Check Bot User**: Ensure bot user is properly configured

### Bot Not Found in Channel

1. **Invite Manually**: Use `/invite @bot-name` in channel
2. **Check Permissions**: Admin may need to allow bot in workspace
3. **Check Channel Settings**: Some channels restrict bot access

### Authentication Errors

1. **Verify Token**: Ensure `SLACK_BOT_TOKEN` is correct
2. **Check Expiration**: Reinstall app if token expired
3. **Check Workspace**: Ensure token is for correct workspace

## Security Best Practices

1. **Never commit tokens**: Use environment variables or secret managers
2. **Rotate tokens**: Regularly rotate bot tokens
3. **Limit scopes**: Only request necessary permissions
4. **Verify requests**: Always verify Slack request signatures
5. **Rate limiting**: Implement rate limiting for Slack API calls
6. **Error handling**: Don't expose sensitive info in error messages

## Next Steps

After onboarding:

1. ✅ Configure webhook endpoints in `main.py`
2. ✅ Implement `SlackService` class
3. ✅ Set up agent communication hooks in `agent_orchestrator.py`
4. ✅ Test end-to-end: Agent completes task → Posts to Slack
5. ✅ Test queries: User asks agent → Agent responds

## Resources

- [Slack API Documentation](https://api.slack.com/)
- [Slack SDK for Python](https://slack.dev/python-slack-sdk/)
- [Slack Block Kit Builder](https://app.slack.com/block-kit-builder)
- [Slack App Management](https://api.slack.com/apps)
