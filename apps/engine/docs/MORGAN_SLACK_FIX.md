# Fix: Morgan Not Replying on Slack

## Problem
Morgan Taylor (QA Engineer agent) was not responding to messages on Slack, even when mentioned directly with `@Morgan Taylor`.

## Root Cause
The Slack event handler had two issues:

1. **Thread Reply Limitation**: When users replied to messages in threads, the code only processed replies if conversation context already existed (meaning the bot had responded before). If Morgan never responded to a thread initially, subsequent replies in that thread were ignored.

2. **Missing Agent Name Detection**: Regular channel messages (not `app_mention` events) that mentioned agent names like "Morgan" or "Morgan Taylor" were not being processed, even though they should trigger a response.

## Solution
Updated `/slack/events` endpoint in `main.py` to:

1. **Check for Agent Mentions in Thread Replies**: When processing thread replies, the code now checks if the message text mentions any agent name (like "morgan" or "Morgan Taylor"). If it does, the reply is processed even if there's no existing conversation context.

2. **Handle Regular Channel Messages**: Regular channel messages (not in threads) that mention agent names are now detected and processed as mentions, even if the bot itself wasn't @mentioned.

3. **Improved Agent Name Matching**: Enhanced the agent matching logic in `handle_slack_mention()` to:
   - Clean Slack mention formatting (removes `<@U123456|Display Name>` patterns)
   - Match multiple name patterns: "morgan", "morgan taylor", "ask morgan", "tell morgan", "@morgan", etc.
   - Match by role keywords (e.g., "qa" matches Morgan who is a QA Engineer)
   - Better fallback logic when channel IDs don't match exactly

## Code Changes
- Modified the thread reply handler (lines 707-762) to check for agent name mentions
- Added logic to handle regular channel messages that mention agents (lines 763-792)
- Enhanced agent name matching in `handle_slack_mention()` (lines 917-1005):
  - Added text cleaning to remove Slack mention formatting
  - Improved pattern matching for various mention formats
  - Added role keyword matching (e.g., "qa" for QA engineers)
  - Better fallback when channel IDs don't match

## Testing
To verify the fix works:

1. **Test Thread Reply**: 
   - Send a message mentioning `@Morgan Taylor` in a channel
   - Reply to that message in a thread
   - Morgan should now respond

2. **Test Direct Mention**:
   - Send a message mentioning `@Morgan Taylor` or just "morgan" in a channel
   - Morgan should respond even if the bot wasn't @mentioned

## Additional Checks
If Morgan still doesn't respond, verify:

1. **Database Configuration**: Ensure Morgan is properly onboarded:
   ```bash
   python scripts/onboard_agent_employee.py --role qa_reviewer --slack-channel "#engineering"
   ```

2. **Bot Token**: Check that `SLACK_BOT_TOKEN_MORGAN` or `SLACK_BOT_TOKEN` is set:
   ```bash
   echo $SLACK_BOT_TOKEN_MORGAN
   ```

3. **Slack App Configuration**: Verify the Slack app has:
   - `app_mentions:read` scope
   - `message.channels` event subscription enabled
   - Event subscription URL points to `/slack/events`

4. **Channel ID Match**: Ensure Morgan's `slack_channel_id` in the database matches the actual Slack channel ID where messages are sent.

## Related Files
- `apps/engine/main.py` - Slack event handlers
- `apps/engine/src/database/models.py` - AgentEmployee model
- `apps/engine/scripts/onboard_agent_employee.py` - Agent onboarding script
