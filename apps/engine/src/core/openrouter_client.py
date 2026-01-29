"""
Common OpenRouter API client for chat completions.
Use this module for all OpenRouter requests to keep URL, headers, and error handling consistent.
"""
import os
from typing import Any, Dict, List, Optional

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_REFERER = "https://healops.ai"


def get_api_key() -> Optional[str]:
    """Return OpenRouter API key from environment."""
    return os.getenv("OPENCOUNCIL_API")


def openrouter_chat_completion(
    model: str,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 500,
    timeout: int = 30,
    title: str = "HealOps",
    referer: Optional[str] = None,
    extra_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Call OpenRouter chat completions API.

    Args:
        model: Model id (e.g. "google/gemini-flash-1.5-8b", "xiaomi/mimo-v2-flash").
        messages: List of {"role": "user"|"system"|"assistant", "content": "..."}.
        temperature: Sampling temperature (default 0.3).
        max_tokens: Max tokens to generate (default 500).
        timeout: Request timeout in seconds (default 30).
        title: X-Title header for OpenRouter (default "HealOps").
        referer: HTTP-Referer header (default from APP_URL or DEFAULT_REFERER).
        extra_json: Optional extra keys to merge into the request body.

    Returns:
        Dict with:
            - success: bool
            - content: str or None (message content when success)
            - usage: dict (prompt_tokens, completion_tokens, total_tokens when present)
            - status_code: int
            - error_message: str or None (when not success)
            - raw: full response json when success (for callers that need choices/usage)
    """
    api_key = get_api_key()
    if not api_key:
        return {
            "success": False,
            "content": None,
            "usage": {},
            "status_code": 0,
            "error_message": "OPENCOUNCIL_API is not set",
            "raw": None,
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer or os.getenv("APP_URL", DEFAULT_REFERER),
        "X-Title": title,
    }

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_json:
        body.update(extra_json)

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return {
            "success": False,
            "content": None,
            "usage": {},
            "status_code": -1,
            "error_message": str(e),
            "raw": None,
        }

    status_code = response.status_code
    if status_code != 200:
        error_message = "Unknown error"
        if response.text:
            try:
                data = response.json()
                error_message = (
                    data.get("error") or {}
                ).get("message", response.text[:500])
            except Exception:
                error_message = response.text[:500]
        return {
            "success": False,
            "content": None,
            "usage": {},
            "status_code": status_code,
            "error_message": error_message,
            "raw": None,
        }

    try:
        result = response.json()
    except ValueError as e:
        return {
            "success": False,
            "content": None,
            "usage": {},
            "status_code": status_code,
            "error_message": f"Invalid JSON: {e}",
            "raw": None,
        }

    choices = result.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content", "") or ""
    usage = result.get("usage") or {}

    return {
        "success": True,
        "content": content,
        "usage": usage,
        "status_code": status_code,
        "error_message": None,
        "raw": result,
    }
