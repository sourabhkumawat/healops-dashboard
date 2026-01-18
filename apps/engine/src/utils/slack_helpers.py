"""
Slack helper utilities for bot token management, conversation context, and agent responses.
"""
import os
from typing import Optional, Dict, Any, List

# Conversation context storage (in-memory, can be moved to Redis for production)
_conversation_contexts: Dict[str, List[Dict[str, str]]] = {}

# Cache bot user ID to prevent recursive responses (avoid creating SlackService repeatedly)
_cached_bot_user_id: Optional[str] = None

# Track message timestamps we're currently updating to prevent duplicate posts
_updating_messages: set = set()

# Track message timestamps we just posted to prevent recursive processing
_recently_posted_messages: set = set()

# Track threads we just responded to (by thread_ts) to prevent recursive responses
_recently_responded_threads: set = set()


def get_bot_token_for_agent(agent_name: str, agent_role: str = None, agent_stored_token: str = None) -> Optional[str]:
    """
    Get the appropriate Slack bot token for an agent.
    
    Priority:
    1. Agent's stored token (decrypted if encrypted)
    2. Agent-specific environment variable (SLACK_BOT_TOKEN_MORGAN, SLACK_BOT_TOKEN_ALEX)
    3. Generic SLACK_BOT_TOKEN environment variable
    
    Args:
        agent_name: Agent's name (e.g., "Morgan Taylor", "Alexandra Chen")
        agent_role: Agent's role (e.g., "QA Engineer", "Senior Software Engineer")
        agent_stored_token: Encrypted token stored in database (optional)
    
    Returns:
        Bot token string or None if not found
    """
    # Try agent's stored token first (if provided)
    if agent_stored_token:
        try:
            from src.auth.crypto_utils import decrypt_token
            decrypted = decrypt_token(agent_stored_token)
            if decrypted:
                return decrypted
        except Exception as e:
            print(f"⚠️  Failed to decrypt stored token: {e}")
    
    # Try agent-specific environment variable
    agent_token_var = None
    agent_name_lower = agent_name.lower() if agent_name else ""
    agent_role_lower = agent_role.lower() if agent_role else ""
    
    if "alex" in agent_name_lower or "alexandra" in agent_name_lower:
        agent_token_var = os.getenv("SLACK_BOT_TOKEN_ALEX")
    elif "morgan" in agent_name_lower or "qa" in agent_role_lower:
        agent_token_var = os.getenv("SLACK_BOT_TOKEN_MORGAN")
    
    if agent_token_var:
        return agent_token_var
    
    # Fallback to generic token
    return os.getenv("SLACK_BOT_TOKEN")


def get_bot_user_id_from_db(channel_id: str, agent_name: Optional[str] = None) -> Optional[str]:
    """
    Get bot user ID from database for a specific channel or agent.
    
    When we have separate bots for each agent, we need to get the correct bot user ID.
    If agent_name is provided, we prioritize that agent's bot user ID.
    
    Args:
        channel_id: Slack channel ID
        agent_name: Optional agent name to get specific bot user ID
    
    Returns:
        Bot user ID string or None if not found
    """
    try:
        from src.database.models import AgentEmployee
        from src.database.database import SessionLocal
        
        db = SessionLocal()
        try:
            # If agent name is provided, try to find that specific agent first
            if agent_name:
                agent = db.query(AgentEmployee).filter(
                    AgentEmployee.name == agent_name
                ).first()
                if agent and agent.slack_user_id:
                    return agent.slack_user_id
            
            # Try to find agent by channel
            agent = db.query(AgentEmployee).filter(
                AgentEmployee.slack_channel_id == channel_id
            ).first()
            
            if agent and agent.slack_user_id:
                return agent.slack_user_id
            
            # Try any agent as fallback
            agent = db.query(AgentEmployee).first()
            if agent and agent.slack_user_id:
                return agent.slack_user_id
        finally:
            db.close()
    except Exception as e:
        print(f"⚠️  Error getting bot user ID from DB: {e}")
    return None


def get_bot_user_id() -> Optional[str]:
    """Get bot user ID, caching it for performance."""
    global _cached_bot_user_id
    if _cached_bot_user_id:
        return _cached_bot_user_id
    
    try:
        bot_token = os.getenv("SLACK_BOT_TOKEN")
        if bot_token:
            from src.services.slack.service import SlackService
            slack_service = SlackService(bot_token)
            _cached_bot_user_id = slack_service.bot_user_id
            return _cached_bot_user_id
    except Exception as e:
        print(f"⚠️  Error getting bot user ID: {e}")
    return None


def get_conversation_context(thread_id: str, max_messages: int = 10) -> List[Dict[str, str]]:
    """Get conversation history for a thread."""
    if thread_id not in _conversation_contexts:
        _conversation_contexts[thread_id] = []
    # Return last N messages
    return _conversation_contexts[thread_id][-max_messages:]


def add_to_conversation_context(thread_id: str, role: str, content: str):
    """Add a message to conversation context."""
    if thread_id not in _conversation_contexts:
        _conversation_contexts[thread_id] = []
    _conversation_contexts[thread_id].append({"role": role, "content": content})
    # Keep only last 20 messages to prevent memory issues
    if len(_conversation_contexts[thread_id]) > 20:
        _conversation_contexts[thread_id] = _conversation_contexts[thread_id][-20:]


def generate_agent_response_llm(agent: Any, query: str, thread_id: str = None, conversation_history: List[Dict[str, str]] = None) -> str:
    """
    Generate an LLM-powered response from an agent.
    
    Args:
        agent: AgentEmployee object
        query: User's query text
        thread_id: Thread ID for conversation context
        conversation_history: Previous messages in the conversation
    
    Returns:
        Response text
    """
    api_key = os.getenv("OPENCOUNCIL_API")
    if not api_key:
        # Fallback to simple responses if LLM not configured
        return generate_agent_response_simple(agent, query)
    
    # Build system prompt with agent context
    system_prompt = f"""You are {agent.name}, a {agent.role} from the {agent.department} department at HealOps.

Your role: {agent.role}
Department: {agent.department}
Current status: {agent.status}
Current task: {agent.current_task or "No active task"}
Capabilities: {', '.join(agent.capabilities or [])}

You are an AI agent employee helping with incident resolution, code fixes, and technical support. 
Be helpful, professional, and concise. You can discuss:
- Your current work and tasks
- Technical questions about incidents and code
- Status updates on ongoing work
- General questions about your capabilities

Keep responses conversational and friendly. If asked about specific incidents or code, provide helpful information based on your role and capabilities."""

    # Build messages with conversation history
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add conversation history if provided
    if conversation_history:
        messages.extend(conversation_history)
    
    # Add current query
    messages.append({"role": "user", "content": query})
    
    try:
        import requests
        from src.core.ai_analysis import MODEL_CONFIG
        
        # Use chat model from config (free Xiaomi model)
        chat_config = MODEL_CONFIG.get("chat", MODEL_CONFIG["simple_analysis"])
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", "https://healops.ai"),
                "X-Title": "HealOps Agent Chat",
            },
            json={
                "model": chat_config["model"],
                "messages": messages,
                "temperature": chat_config["temperature"],
                "max_tokens": chat_config["max_tokens"],
            },
            timeout=15
        )
        
        if response.status_code == 200:
            result = response.json()
            assistant_message = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if assistant_message:
                # NOTE: Do NOT add to conversation context here - we'll add it AFTER posting
                # to prevent the bot from responding to its own messages
                # The conversation context will be added after the message is successfully posted
                return assistant_message
            else:
                return generate_agent_response_simple(agent, query)
        else:
            print(f"⚠️  LLM API error: {response.status_code} - {response.text[:200]}")
            return generate_agent_response_simple(agent, query)
            
    except Exception as e:
        print(f"⚠️  Error calling LLM: {e}")
        import traceback
        traceback.print_exc()
        return generate_agent_response_simple(agent, query)


def generate_agent_response_simple(agent: Any, query: str) -> str:
    """
    Simple keyword-based fallback responses.
    
    Args:
        agent: AgentEmployee object
        query: User's query text
    
    Returns:
        Response text
    """
    query_lower = query.lower()
    
    # Simple keyword-based responses
    if "what are you working on" in query_lower or "current task" in query_lower:
        if agent.current_task:
            return f"🚀 I'm currently working on: {agent.current_task}"
        else:
            return f"💤 I'm currently idle. No active tasks."
    
    if "completed" in query_lower or "what did you do" in query_lower:
        completed = agent.completed_tasks or []
        if completed:
            tasks_text = "\n".join([f"• {task}" for task in completed[-5:]])
            return f"✅ Recently completed tasks:\n{tasks_text}"
        else:
            return "No completed tasks yet."
    
    if "status" in query_lower or "what's your status" in query_lower:
        status_emoji = {"available": "✅", "working": "⚙️", "idle": "💤"}.get(agent.status, "❓")
        return f"{status_emoji} Status: {agent.status}\nDepartment: {agent.department}\nRole: {agent.role}"
    
    # Default response
    return f"Hi! I'm {agent.name}, {agent.role} from {agent.department}. Ask me:\n• 'What are you working on?'\n• 'What have you completed?'\n• 'What's your status?'"


def generate_agent_response(agent: Any, query: str, thread_id: str = None) -> str:
    """
    Generate a response from an agent (uses LLM if available, falls back to simple).
    
    Args:
        agent: AgentEmployee object
        query: User's query text
        thread_id: Thread ID for conversation context
    
    Returns:
        Response text
    """
    # Get conversation history if thread_id provided
    conversation_history = None
    if thread_id:
        conversation_history = get_conversation_context(thread_id)
    
    # Use LLM-powered response
    return generate_agent_response_llm(agent, query, thread_id, conversation_history)


# Export conversation tracking sets for use in Slack controller
# These are accessed directly by controllers (despite _ prefix, they're module-level shared state)
__all__ = [
    'get_bot_token_for_agent',
    'get_bot_user_id_from_db',
    'get_bot_user_id',
    'get_conversation_context',
    'add_to_conversation_context',
    'generate_agent_response_llm',
    'generate_agent_response_simple',
    'generate_agent_response',
    '_conversation_contexts',  # Export for direct access
    '_recently_posted_messages',  # Export for direct access
    '_recently_responded_threads',  # Export for direct access
    '_updating_messages',  # Export for direct access
]
