"""
AI Analysis module for incident root cause analysis using OpenRouter.
"""
import os
import json
from typing import Dict, Any, Optional
from models import Incident, LogEntry
from sqlalchemy.orm import Session


def analyze_incident_with_openrouter(incident: Incident, logs: list[LogEntry], db: Session) -> Dict[str, Any]:
    """
    Analyze an incident using OpenRouter AI to determine root cause and recommended actions.
    
    Args:
        incident: The incident to analyze
        logs: List of related log entries
        db: Database session
        
    Returns:
        Dict with 'root_cause' and 'action_taken' keys
    """
    api_key = os.getenv("OPENCOUNCIL_API")
    if not api_key:
        print("⚠️  OPENCOUNCIL_API not set, skipping AI analysis")
        return {"root_cause": None, "action_taken": None}
    
    # Prepare context from incident and logs
    log_context = "\n".join([
        f"[{log.timestamp}] {log.level}: {log.message}" 
        for log in logs[:20]  # Limit to last 20 logs
    ])
    
    incident_context = f"""
Incident Details:
- Title: {incident.title}
- Service: {incident.service_name}
- Source: {incident.source or 'Unknown'}
- Severity: {incident.severity}
- Status: {incident.status}
- First Seen: {incident.first_seen_at}
- Last Seen: {incident.last_seen_at}
- Description: {incident.description or 'No description'}

Related Logs:
{log_context}
"""
    
    # Prepare the prompt for root cause analysis
    prompt = f"""You are an expert SRE (Site Reliability Engineer) analyzing an incident. 

{incident_context}

Please provide:
1. Root Cause Analysis: Identify the underlying cause of this incident. Be specific and technical.
2. Recommended Action: Suggest a concrete action to resolve or mitigate this incident.

Respond in JSON format:
{{
    "root_cause": "Detailed explanation of the root cause",
    "action_taken": "Specific recommended action (e.g., 'Restart service X', 'Rollback deployment Y', 'Check database connection pool')"
}}

Keep the root_cause to 2-3 sentences max, and action_taken to 1-2 sentences max.
"""
    
    try:
        import requests
        
        # Use OpenRouter API
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", "https://healops.ai"),  # Optional
                "X-Title": "HealOps Incident Analysis",  # Optional
            },
            json={
                "model": "anthropic/claude-3.5-sonnet",  # You can change this to any model
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3,  # Lower temperature for more deterministic analysis
            },
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"❌ OpenRouter API error: {response.status_code} - {response.text}")
            return {"root_cause": None, "action_taken": None}
        
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # Try to parse JSON from the response
        # Sometimes AI wraps JSON in markdown code blocks
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]  # Remove ```json
        if content.startswith("```"):
            content = content[3:]  # Remove ```
        if content.endswith("```"):
            content = content[:-3]  # Remove closing ```
        content = content.strip()
        
        try:
            analysis = json.loads(content)
            root_cause = analysis.get("root_cause", "Analysis pending...")
            action_taken = analysis.get("action_taken", None)
            
            return {
                "root_cause": root_cause,
                "action_taken": action_taken
            }
        except json.JSONDecodeError:
            # If JSON parsing fails, extract text between markers or use the whole response
            print(f"⚠️  Failed to parse JSON from AI response: {content[:200]}")
            # Fallback: use the entire response as root_cause
            return {
                "root_cause": content[:500] if content else "Analysis pending...",
                "action_taken": None
            }
            
    except Exception as e:
        print(f"❌ Error calling OpenRouter API: {e}")
        return {"root_cause": None, "action_taken": None}
