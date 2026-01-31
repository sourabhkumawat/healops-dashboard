"""
AI Analysis module for incident root cause analysis using OpenRouter.
"""
import os
import json
import re
import traceback
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from src.database.models import Incident, LogEntry, Integration
from sqlalchemy.orm import Session
from src.integrations.github.integration import GithubIntegration
from src.memory.memory import CodeMemory
from src.core.openrouter_client import openrouter_chat_completion, get_api_key

# Cost-optimized model configuration
# Use cheaper models for simpler tasks, expensive models only when needed
MODEL_CONFIG = {
    "simple_analysis": {
        "model": "xiaomi/mimo-v2-flash",
        "max_tokens": 500,
        "temperature": 0.3
    },
    "complex_analysis": {
        "model": "x-ai/grok-code-fast-1",  # ~$0.20 per 1M input tokens
        "max_tokens": 1000,
        "temperature": 0.3
    },
    "code_generation": {
        "model": "x-ai/grok-code-fast-1",  # ~$0.20 per 1M input tokens (Specialized for agentic coding)
        "max_tokens": 8000,
        "temperature": 0.2
    },
    "chat": {
        "model": "xiaomi/mimo-v2-flash",  # Paid model for Slack chat (free tier ended)
        "max_tokens": 500,
        "temperature": 0.7
    }
}

# COST OPTIMIZATION: Reduced limits for incident analysis
# Reserve ~5K tokens for system prompt and response, leaving ~20K for input
MAX_INCIDENT_PROMPT_TOKENS = 20000  # Reduced from 25K to 20K (~$0.06 vs $0.075 with cheaper model)

# Limit logs based on token count, not just count
MAX_TOKENS_FOR_LOGS = 10000  # Reduced from 15K to 10K tokens for logs

# Small/cheap model for lightweight AI tasks (stack trace classification, skipped-resolution copy)
_SMALL_MODEL = "google/gemini-flash-1.5-8b"


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for a text string.
    Rough approximation: ~1 token = 4 characters for English text.
    For code, it's closer to ~1 token = 3 characters.
    We use a conservative 3.5 chars/token average.
    
    Args:
        text: Text to estimate tokens for
        
    Returns:
        Estimated token count
    """
    if not text:
        return 0
    # Conservative estimate: 3.5 characters per token
    return int(len(text) / 3.5)


def get_incident_fingerprint(incident: Incident, logs: list[LogEntry]) -> str:
    """
    Generate a fingerprint for an incident to enable caching.
    Uses service name, error message pattern, and first few log messages.
    
    Args:
        incident: The incident
        logs: Related log entries
        
    Returns:
        Hash string representing the incident signature
    """
    try:
        # Create a signature from key incident characteristics
        signature_parts = [
            getattr(incident, 'service_name', None) or "",
            getattr(incident, 'source', None) or "",
            getattr(incident, 'severity', None) or "",
        ]
        
        # Add first 3 error messages (normalized)
        for log in (logs or [])[:3]:
            if log and hasattr(log, 'message') and log.message:
                try:
                    # Normalize message (remove timestamps, IDs, etc.)
                    msg = str(log.message)
                    # Remove common variable parts
                    msg = re.sub(r'\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}', '[TIMESTAMP]', msg)
                    msg = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]', msg)
                    msg = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '[UUID]', msg, flags=re.IGNORECASE)
                    signature_parts.append(msg[:200])  # First 200 chars
                except Exception as e:
                    print(f"⚠️  Error processing log message for fingerprint: {e}")
                    continue
        
        signature = "|".join(signature_parts)
        return hashlib.sha256(signature.encode()).hexdigest()[:16]
    except Exception as e:
        print(f"⚠️  Error generating incident fingerprint: {e}")
        # Return a default fingerprint on error
        return hashlib.sha256(str(incident.id if incident else "unknown").encode()).hexdigest()[:16]


def should_use_expensive_model(logs: list[LogEntry], root_cause: Optional[str]) -> bool:
    """
    Determine if we should use expensive model (Claude 3.5 Sonnet) or cheaper alternatives.
    
    Args:
        logs: Related log entries
        root_cause: Root cause analysis (if available)
        
    Returns:
        True if expensive model should be used
    """
    try:
        # Use expensive model if: complex error patterns (stack traces from app code, multiple errors)
        has_stack_trace = False
        error_count = 0
        for log in (logs or [])[:10]:
            if log:
                for trace_str in _get_trace_strings_from_log(log):
                    if not is_stacktrace_from_node_modules(trace_str):
                        has_stack_trace = True
                        break
                if hasattr(log, 'severity') and log.severity and log.severity.upper() in ["ERROR", "CRITICAL"]:
                    error_count += 1
        
        if has_stack_trace and error_count >= 2:
            return True
        
        # 3. Root cause suggests code changes needed
        if root_cause and isinstance(root_cause, str):
            code_keywords = ["bug", "code", "function", "method", "class", "syntax", "exception", "error in"]
            if any(keyword in root_cause.lower() for keyword in code_keywords):
                return True
        
        # Otherwise use cheaper model
        return False
    except Exception as e:
        print(f"⚠️  Error in should_use_expensive_model: {e}")
        # Default to cheaper model on error
        return False


def truncate_to_token_limit(text: str, max_tokens: int, suffix: str = "... [truncated]") -> str:
    """
    Truncate text to stay within token limit.
    
    Args:
        text: Text to truncate
        max_tokens: Maximum allowed tokens
        suffix: Suffix to add when truncating
        
    Returns:
        Truncated text
    """
    if not text:
        return text
    
    estimated_tokens = estimate_tokens(text)
    if estimated_tokens <= max_tokens:
        return text
    
    # Calculate max characters (conservative: 3.5 chars per token)
    max_chars = int(max_tokens * 3.5) - len(suffix)
    if max_chars <= 0:
        return suffix.strip()
    
    return text[:max_chars] + suffix


def get_repo_name_from_integration(integration: Integration, service_name: Optional[str] = None) -> Optional[str]:
    """
    Extract repository name from integration config or project_id.
    Supports service-to-repo mapping for multiple services.
    
    Args:
        integration: Integration model instance
        service_name: Optional service name to look up in service mappings
        
    Returns:
        Repository name in format "owner/repo" or None
    """
    # Check config first
    if integration.config and isinstance(integration.config, dict):
        # If service_name is provided, check service mappings first
        if service_name:
            service_mappings = integration.config.get("service_mappings", {})
            if isinstance(service_mappings, dict) and service_name in service_mappings:
                repo_name = service_mappings[service_name]
                if repo_name:
                    return repo_name
        
        # Fallback to default repo_name or repository
        repo_name = integration.config.get("repo_name") or integration.config.get("repository")
        if repo_name:
            return repo_name
    
    # Check project_id as fallback
    if integration.project_id:
        # project_id might be in format "owner/repo" or just "repo"
        return integration.project_id
    
    return None


def generate_incident_title_and_description(log: LogEntry, service_name: str) -> tuple[str, str]:
    """
    Generate a meaningful title and description for an incident based on error logs.
    Uses AI to create human-readable, descriptive titles and descriptions.
    
    Args:
        log: The log entry that triggered the incident
        service_name: Name of the service
        
    Returns:
        Tuple of (title, description) - falls back to simple format if AI fails
    """
    if not get_api_key():
        # Fallback to simple format if API key not available
        return (
            f"Detected {log.severity} in {service_name}",
            log.message[:200] if log.message else "No error message available"
        )
    
    try:
        # Prepare log message for analysis (limit to reasonable size)
        log_message = log.message[:1000] if log.message else "No error message available"
        
        # Build prompt for title and description generation
        prompt = f"""Analyze the following error log and generate a clear, meaningful title and description for an incident.

Error Log:
Service: {service_name}
Severity: {log.severity}
Message: {log_message}

Generate:
1. A concise title (max 80 characters) that clearly describes what went wrong
2. A brief description (max 300 characters) that explains the issue in user-friendly terms

Format your response as JSON:
{{
  "title": "Clear, descriptive title here",
  "description": "User-friendly description explaining what happened"
}}

Focus on making it easy for users to understand what the problem is without needing to read the raw error log."""

        model_config = MODEL_CONFIG["simple_analysis"]
        r = openrouter_chat_completion(
            model_config["model"],
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
            timeout=10,
            title="HealOps Incident Title Generation",
        )
        
        if r["success"] and r["content"]:
            content = r["content"]
            
            # Try to parse JSON response
            try:
                # First, try to extract JSON from markdown code blocks if present
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)
                
                # Try to parse the entire content as JSON first
                try:
                    parsed = json.loads(content.strip())
                    title = parsed.get("title", "").strip()
                    description = parsed.get("description", "").strip()
                    
                    if title and len(title) <= 150 and description and len(description) <= 500:
                        return (title, description)
                except json.JSONDecodeError:
                    # If direct parse fails, try to find JSON object in the content
                    # Use a more robust pattern that handles nested objects
                    json_match = re.search(r'\{[^{}]*(?:"title"|"description")[^}]*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            parsed = json.loads(json_match.group(0))
                            title = parsed.get("title", "").strip()
                            description = parsed.get("description", "").strip()
                            
                            if title and len(title) <= 150 and description and len(description) <= 500:
                                return (title, description)
                        except json.JSONDecodeError:
                            pass
                
                # If no JSON found, try to extract title and description from plain text
                # Look for patterns like "Title: ..." or "title: ..."
                title_match = re.search(r'(?:title|Title):\s*(.+?)(?:\n|$|Description|description)', content, re.IGNORECASE)
                desc_match = re.search(r'(?:description|Description):\s*(.+?)(?:\n\n|$)', content, re.IGNORECASE | re.DOTALL)
                
                if title_match and desc_match:
                    title = title_match.group(1).strip()
                    description = desc_match.group(1).strip()
                    if title and len(title) <= 150 and description and len(description) <= 500:
                        return (title, description)
                        
            except (KeyError, AttributeError) as e:
                print(f"⚠️  Failed to parse AI response for incident title/description: {e}")
                print(f"   Response content: {content[:200]}")
        
        # Fallback if AI response is invalid
        return (
            f"Detected {log.severity} in {service_name}",
            log_message[:200]
        )
        
    except Exception as e:
        print(f"⚠️  Error generating AI title/description: {e}")
        # Fallback to simple format
        return (
            f"Detected {log.severity} in {service_name}",
            log.message[:200] if log.message else "No error message available"
        )


def extract_paths_from_stacktrace(text: str) -> list[str]:
    """Extract file paths from a stack trace string."""
    paths = []
    
    # Python stack trace pattern: File "/path/to/file.py", line 123
    python_pattern = r'File "([^"]+)", line \d+'
    paths.extend(re.findall(python_pattern, text))
    
    # Node.js/JS stack trace pattern: at FunctionName (/path/to/file.js:123:45)
    # or at /path/to/file.js:123:45
    js_pattern = r'at (?:.*? \()?([^:)]+(?:\.js|\.ts|\.jsx|\.tsx)):\d+:\d+\)?'
    matches = re.findall(js_pattern, text)
    
    # Filter out bundled/minified files
    bundled_patterns = [
        r'/_next/static/chunks/',  # Next.js chunks
        r'/_next/static/.*\.js',   # Next.js static files
        r'webpack://',              # Webpack internal
        r'\.min\.js',               # Minified files
        r'chunk-[a-f0-9]+\.js',    # Generic chunk files
    ]
    
    for match in matches:
        # Skip if it matches bundled patterns
        is_bundled = any(re.search(pattern, match) for pattern in bundled_patterns)
        if not is_bundled:
            paths.append(match)
    
    return paths


def is_stacktrace_from_node_modules(stack_trace: str) -> bool:
    """
    Use AI to determine if a stack trace is from node_modules or actual application code.
    This helps avoid wasting computation on issues we cannot resolve (node_modules).
    
    Purely relies on AI output - no pattern matching or code-based decisions.
    
    Args:
        stack_trace: The stack trace string to analyze
        
    Returns:
        True if the stack trace is primarily from node_modules, False if from application code
        Returns False if AI is unavailable or fails (assume application code to be safe)
    """
    if not stack_trace or len(stack_trace.strip()) < 50:
        # Too short to analyze, assume it's from code (safe default)
        return False
    
    if not get_api_key():
        # No API key - cannot use AI, default to False (assume application code)
        print("⚠️  OPENCOUNCIL_API not set, cannot analyze stack trace. Assuming application code.")
        return False
    
    try:
        # Truncate stack trace if too long to save tokens
        truncated_trace = stack_trace[:2000] if len(stack_trace) > 2000 else stack_trace
        
        prompt = f"""Analyze the following stack trace and determine if it originates primarily from node_modules (third-party dependencies) or from application code.

                Stack Trace:
                {truncated_trace}

                Respond with ONLY a JSON object:
                {{
                "is_node_modules": true or false
                }}

                Rules:
                - If the stack trace shows paths containing "/node_modules/" or references to third-party libraries, it's likely node_modules
                - If the stack trace shows paths to application code (src/, app/, pages/, components/, etc.), it's likely application code
                - If it's mixed but primarily node_modules, return true
                - If it's mixed but primarily application code, return false
            """

        r = openrouter_chat_completion(
            _SMALL_MODEL,
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=100,
            timeout=5,
            title="HealOps Stack Trace Analysis",
        )
        
        if r["success"] and r["content"]:
            content = r["content"]
            try:
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)
                parsed = json.loads(content.strip())
                is_node_modules = parsed.get("is_node_modules", False)
                return bool(is_node_modules)
            except (json.JSONDecodeError, KeyError, AttributeError) as e:
                print(f"⚠️  Failed to parse AI response for stack trace classification: {e}")
                print(f"   Response content: {content[:200]}")
                return False
        else:
            if not r["success"]:
                print(f"⚠️  OpenRouter API error for stack trace classification: {r['status_code']} - {r.get('error_message', '')}")
            return False
            
    except Exception as e:
        print(f"⚠️  Error in is_stacktrace_from_node_modules: {e}")
        traceback.print_exc()
        # On error, default to False (assume application code to be safe)
        return False


# Shared keywords to detect stack-trace-like content (used by trace extraction helpers)
_STACK_TRACE_KEYWORDS = ("Traceback", "at ", "File \"", "Error:", "Exception:", "node_modules")


def _get_trace_strings_from_log(log: Any) -> List[str]:
    """
    Extract stack trace strings from a single log (message + metadata events).
    Returns non-empty snippets of at least 50 chars. Reused by Linear description,
    should_use_expensive_model, and incident-from-external check.
    """
    traces: List[str] = []
    msg = getattr(log, "message", None) or ""
    if msg and any(k in msg for k in _STACK_TRACE_KEYWORDS):
        s = msg.strip()
        if len(s) >= 50:
            traces.append(s)
    meta = getattr(log, "metadata_json", None) or {}
    if isinstance(meta, dict):
        for event in meta.get("events") or []:
            if isinstance(event, dict):
                attrs = (event.get("attributes") or {})
                if isinstance(attrs, dict) and attrs.get("exception.stacktrace"):
                    s = (attrs["exception.stacktrace"] or "").strip()
                    if len(s) >= 50:
                        traces.append(s)
    return traces


def _get_trace_strings_from_incident(incident: Incident) -> List[str]:
    """Extract stack trace strings from incident (message/title + metadata events)."""
    traces: List[str] = []
    if not incident:
        return traces
    msg = getattr(incident, "message", None) or getattr(incident, "title", None) or ""
    if msg and any(k in msg for k in _STACK_TRACE_KEYWORDS):
        s = msg.strip()
        if len(s) >= 50:
            traces.append(s)
    meta = getattr(incident, "metadata_json", None) or {}
    if isinstance(meta, dict):
        for event in meta.get("events") or []:
            if isinstance(event, dict):
                attrs = (event.get("attributes") or {})
                if isinstance(attrs, dict) and attrs.get("exception.stacktrace"):
                    s = (attrs["exception.stacktrace"] or "").strip()
                    if len(s) >= 50:
                        traces.append(s)
    return traces


def _collect_stack_traces_from_incident(incident: Incident, logs: list) -> List[str]:
    """
    Collect stack trace strings from incident and logs for classification.
    Returns a deduplicated list of non-empty stack trace snippets (each at least 50 chars).
    Reuses _get_trace_strings_from_incident and _get_trace_strings_from_log.
    """
    seen: set = set()
    result: List[str] = []
    for s in _get_trace_strings_from_incident(incident):
        key = s[:200]
        if key not in seen:
            seen.add(key)
            result.append(s)
    for log in logs or []:
        if not log:
            continue
        for s in _get_trace_strings_from_log(log):
            key = s[:200]
            if key not in seen:
                seen.add(key)
                result.append(s)
    return result


def is_incident_from_external_code(incident: Incident, logs: list) -> Tuple[bool, str]:
    """
    Determine if the incident's error originates from node_modules or other external
    (third-party) code. Reuses is_stacktrace_from_node_modules for classification.

    Use this before running the coding agent to avoid wasting cost and time on
    issues we cannot fix (e.g. dependencies).

    Args:
        incident: The incident.
        logs: Related log entries.

    Returns:
        (True, sample_trace) if the incident is from external/node_modules code;
        (False, "") otherwise. sample_trace is a truncated stack trace for display.
    """
    traces = _collect_stack_traces_from_incident(incident, logs)
    if not traces:
        return False, ""

    # Check the first substantial trace; if it's from node_modules, skip resolution
    sample = traces[0]
    if is_stacktrace_from_node_modules(sample):
        return True, (sample[:1500] + ("..." if len(sample) > 1500 else ""))
    return False, ""


def _generate_skipped_resolution_description_with_ai(
    incident: Incident,
    sample_trace: str,
) -> Optional[str]:
    """
    Use a small model to generate a clear, contextual "why we didn't auto-resolve" description
    based on the incident and stack trace. Returns None on failure (caller should fall back to static template).
    """
    if not get_api_key():
        return None
    title = getattr(incident, "title", None) or "Incident"
    service = getattr(incident, "service_name", None) or "Service"
    truncated = (sample_trace[:1500] + "...") if len(sample_trace) > 1500 else sample_trace

    prompt = f"""You are writing a short, developer-friendly explanation for a dashboard. We did NOT auto-fix this incident because the error comes from dependency code (e.g. node_modules or vendor libs), which we do not modify.

Context:
- Incident: {title}
- Service: {service}

Stack trace snippet (use this to make the explanation specific—e.g. mention the package or error type if visible):
```
{truncated}
```

Write a single markdown document (no YAML, no code fence around the whole thing) with:
1. A heading: ## Why we didn't auto-resolve this incident
2. One short paragraph explaining that the error originates from dependency/external code and we only fix application code (be specific if the trace shows a package name or error type).
3. A subsection "### Flow (what happened)" with a simple ASCII diagram showing: Your app → Dependency → Error; Our agent skips. One line of "Next steps" after the diagram.
4. A subsection "### What you can do" with 3–4 bullet points: upgrade/pin dependency, handle in app code, report upstream. Keep bullets concise.

Include the incident title and service at the top (e.g. **Incident:** ... **Service:** ...). Do not repeat the full stack trace in your text. Output only the markdown."""

    try:
        r = openrouter_chat_completion(
            _SMALL_MODEL,
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
            timeout=10,
            title="HealOps Skipped Resolution Description",
        )
        if not r.get("success") or not r.get("content"):
            return None
        content = (r["content"] or "").strip()
        # Remove wrapping markdown code block if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:markdown)?\s*", "", content)
            content = re.sub(r"\s*```\s*$", "", content)
        if len(content) < 100:
            return None
        return content
    except Exception as e:
        print(f"⚠️  AI skipped-resolution description failed: {e}")
        return None


def _build_skipped_resolution_description_static(
    incident: Incident,
    sample_trace: str,
) -> str:
    """Static fallback when AI is unavailable or fails."""
    title = getattr(incident, "title", None) or "Incident"
    service = getattr(incident, "service_name", None) or "Service"
    reason_text = (
        "The error **originates from external or dependency code** (e.g. `node_modules`, "
        "third-party or vendor libs). Our coding agent only modifies your application "
        "code, not dependencies, so we did not attempt an automated fix."
    )
    flow = (
        "### Flow (what happened)\n\n"
        "```\n[Your app] → calls → [Dependency in node_modules/vendor]\n"
        "                          ↓\nError thrown here (stack trace points to dependency)\n"
        "                          ↓\n[Our agent] → skips → No change to your repo\n```\n\n"
        "**Next steps:** Upgrade or patch the dependency, report upstream, or fix the call site in your app."
    )
    copywriting = (
        "### What you can do\n\n"
        "- **Upgrade the dependency** if a newer version fixes the issue.\n"
        "- **Pin a known-good version** if a recent upgrade introduced the bug.\n"
        "- **Handle the error in your code** (try/catch) and log or report it.\n"
        "- **Report upstream** to the package maintainers if it's a bug in the dependency."
    )
    trace_section = ""
    if sample_trace:
        trace_section = "\n### Stack trace (relevant snippet)\n\n```\n" + sample_trace.strip() + "\n```\n"
    return f"""## Why we didn't auto-resolve this incident

**Incident:** {title}  
**Service:** {service}

{reason_text}

{flow}

{trace_section}
{copywriting}
""".strip()


def build_skipped_resolution_description(
    incident: Incident,
    sample_trace: str = "",
) -> str:
    """
    Build a developer-friendly description for the frontend when we skip automated
    resolution (e.g. because the error is from node_modules or third-party code).
    Uses a small AI model to tailor the text to the incident and stack trace when
    possible; falls back to a static template if the API is unavailable or fails.

    Args:
        incident: The incident.
        sample_trace: Truncated stack trace snippet to display.

    Returns:
        Markdown string suitable for code_fix_explanation / frontend display.
    """
    # Prefer AI-generated description for clear, context-aware copy
    if sample_trace:
        ai_description = _generate_skipped_resolution_description_with_ai(incident, sample_trace)
        if ai_description:
            # Append the raw trace snippet so developers always have it
            trace_block = "\n### Stack trace (relevant snippet)\n\n```\n" + sample_trace.strip() + "\n```"
            return ai_description.rstrip() + trace_block
    return _build_skipped_resolution_description_static(incident, sample_trace)


def normalize_path(path: str) -> str:
    """Normalize path to be relative to repo root."""
    # Remove protocol
    if "://" in path:
        path = "/" + "/".join(path.split("/")[3:])
        
    # Remove query params
    if "?" in path:
        path = path.split("?")[0]
        
    # Remove webpack prefixes
    if path.startswith("webpack://"):
        path = path.replace("webpack://", "")
        # Remove leading dot-segments often found in webpack map
        path = path.replace("./", "")
        
    # Handle standard Docker paths
    if path.startswith("/usr/src/app/"):
        path = path.replace("/usr/src/app/", "")
    elif path.startswith("/app/"):
        path = path.replace("/app/", "")
        
    # Try to make it relative if it's absolute
    # Prioritize specific project directories
    if "/apps/" in path:
        path = "apps/" + path.split("/apps/", 1)[1]
    elif "/packages/" in path:
        path = "packages/" + path.split("/packages/", 1)[1]
    elif "/src/" in path and not path.startswith("src/"):
        path = "src/" + path.split("/src/", 1)[1]
    
    # Handle common web app directories if not caught above
    for common_dir in ["/app/", "/pages/", "/components/", "/lib/", "/utils/", "/public/", "/api/"]:
        if common_dir in path:
            # e.g. /Users/foo/project/app/page.tsx -> app/page.tsx
            path = common_dir.lstrip("/") + path.split(common_dir, 1)[1]
            break
        
    return path.lstrip("/")


def _filter_and_normalize_paths(paths: list[str], *, dedupe: bool = False) -> list[str]:
    """
    Filter out node_modules and .next, normalize each path.
    Optionally deduplicate (order preserved).
    """
    valid_paths: list[str] = []
    for p in paths:
        if not p or "/node_modules/" in p or "/.next/" in p:
            continue
        valid_paths.append(normalize_path(p))
    return list(dict.fromkeys(valid_paths)) if dedupe else valid_paths


def extract_file_paths_from_log(log: LogEntry) -> list[str]:
    """
    Extract potential file paths from log message and metadata.
    """
    paths = []
    metadata = log.metadata_json or {}
    
    if not isinstance(metadata, dict):
        metadata = {}
        
    # 1. Check explicit fields
    if metadata.get("filePath"):
        paths.append(metadata["filePath"])
    if metadata.get("file_path"):
        paths.append(metadata["file_path"])
        
    # 2. Check OTel attributes
    attributes = metadata.get("attributes", {})
    if attributes.get("code.filepath"):
        paths.append(attributes["code.filepath"])
    if attributes.get("code.file_path"):
        paths.append(attributes["code.file_path"])
        
    # 3. Check OTel events (exceptions)
    events = metadata.get("events", [])
    for event in events:
        if event.get("name") == "exception":
            evt_attrs = event.get("attributes", {})
            stacktrace = evt_attrs.get("exception.stacktrace", "")
            if stacktrace:
                paths.extend(extract_paths_from_stacktrace(stacktrace))
                
    # 4. Check stack trace in metadata or message
    if metadata.get("stack"):
        paths.extend(extract_paths_from_stacktrace(metadata["stack"]))
    if metadata.get("traceback"):
        paths.extend(extract_paths_from_stacktrace(metadata["traceback"]))
        
    # Also check message for stack trace-like patterns if it's long
    if log.message and len(log.message) > 100 and ("File \"" in log.message or "at " in log.message):
        paths.extend(extract_paths_from_stacktrace(log.message))

    return _filter_and_normalize_paths(paths)


def extract_file_paths_from_incident_metadata(metadata_json: dict) -> list[str]:
    """
    Extract file paths from incident.metadata_json (stack traces and direct path fields).
    Filters out node_modules and .next; returns normalized, deduplicated paths.
    """
    if not isinstance(metadata_json, dict):
        return []

    paths: list[str] = []

    # Direct path fields
    for key in ("filePath", "code.file.path"):
        val = metadata_json.get(key)
        if val and isinstance(val, str):
            paths.append(val)

    # Stack trace string fields
    for key in ("stack", "errorStack", "fullStack"):
        val = metadata_json.get(key)
        if val and isinstance(val, str):
            paths.extend(extract_paths_from_stacktrace(val))

    # exception.stacktrace (nested)
    exc = metadata_json.get("exception")
    if isinstance(exc, dict):
        st = exc.get("stacktrace")
        if st and isinstance(st, str):
            paths.extend(extract_paths_from_stacktrace(st))

    return _filter_and_normalize_paths(paths, dedupe=True)


def get_trace_logs(logs: list[LogEntry], db: Session, user_id: Optional[int] = None) -> list[LogEntry]:
    """
    Get all logs from the same trace(s) as the provided logs.
    This helps understand the full execution flow, not just the error spans.
    
    Args:
        logs: List of log entries (typically from an incident)
        db: Database session
        user_id: Optional user_id to filter logs
        
    Returns:
        List of all LogEntry objects from the same trace(s)
    """
    if not logs:
        return []
    
    # Collect all unique traceIds from the provided logs
    trace_ids = set()
    for log in logs:
        try:
            metadata = log.metadata_json or {}
            if isinstance(metadata, dict):
                trace_id = metadata.get("traceId")
                # Filter out None, empty strings, and non-string values
                if trace_id and isinstance(trace_id, str) and trace_id.strip():
                    trace_ids.add(trace_id.strip())
        except (AttributeError, TypeError) as e:
            print(f"⚠️  Error reading metadata from log {log.id}: {e}")
            continue
    
    if not trace_ids:
        print("⚠️  No traceIds found in provided logs, returning original logs")
        return logs
    
    print(f"🔍 Found {len(trace_ids)} unique traceId(s), querying related logs...")
    
    try:
        # Query all logs and filter in-memory for traceIds
        # This approach works with both PostgreSQL JSONB and SQLite JSON
        query = db.query(LogEntry)
        
        # Filter by user_id if provided
        if user_id:
            query = query.filter(LogEntry.user_id == user_id)
        
        # Get logs from a reasonable time window (last 1 hour) to limit query size
        # Also handle None timestamps (include them to be safe)
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        query = query.filter(
            (LogEntry.timestamp >= one_hour_ago) | (LogEntry.timestamp.is_(None))
        )
        
        # Limit query results to prevent memory issues (reduced from 10,000 to 5,000 for 4GB RAM)
        query = query.limit(5000)
        
        all_logs = query.all()
        
        # Filter logs that match our traceIds (in-memory filtering for compatibility)
        trace_logs = []
        for log in all_logs:
            try:
                metadata = log.metadata_json or {}
                if isinstance(metadata, dict):
                    log_trace_id = metadata.get("traceId")
                    if log_trace_id and isinstance(log_trace_id, str) and log_trace_id.strip() in trace_ids:
                        trace_logs.append(log)
            except (AttributeError, TypeError) as e:
                # Skip logs with invalid metadata
                continue
        
        # Also include original logs if not already included
        trace_log_ids = {log.id for log in trace_logs}
        for log in logs:
            if log.id not in trace_log_ids:
                trace_logs.append(log)
        
        # Sort by timestamp (handle None timestamps)
        trace_logs.sort(key=lambda x: x.timestamp if x.timestamp else datetime.min, reverse=False)
        
        # Limit final result to prevent context overflow (reduced from 500 to 300 for 4GB RAM)
        if len(trace_logs) > 300:
            print(f"⚠️  Trace has {len(trace_logs)} logs, limiting to 300 most recent")
            trace_logs = trace_logs[-300:]
        
        print(f"✅ Found {len(trace_logs)} total logs from trace(s) (including {len(logs)} original)")
        return trace_logs
        
    except Exception as e:
        print(f"⚠️  Error querying trace logs: {e}")
        traceback.print_exc()
        # Fallback to original logs on error
        return logs


def build_trace_execution_flow(logs: list[LogEntry]) -> Dict[str, Any]:
    """
    Build a trace execution flow from span relationships.
    Creates a tree structure showing the request flow.
    
    Args:
        logs: List of log entries from the same trace
        
    Returns:
        Dict with trace execution flow information
    """
    if not logs:
        return {"trace_id": None, "spans": [], "flow": ""}
    
    # Find root span (span with no parentSpanId or parentSpanId not in our logs)
    spans_by_id = {}
    trace_id = None
    
    for log in logs:
        try:
            metadata = log.metadata_json or {}
            if not isinstance(metadata, dict):
                continue
                
            span_id = metadata.get("spanId")
            # Validate span_id is a non-empty string
            if not span_id or not isinstance(span_id, str) or not span_id.strip():
                continue
                
            span_id = span_id.strip()
            parent_span_id = metadata.get("parentSpanId")
            # Normalize parent_span_id if it exists
            if parent_span_id and isinstance(parent_span_id, str):
                parent_span_id = parent_span_id.strip() if parent_span_id.strip() else None
            else:
                parent_span_id = None
            
            # Safely extract numeric values with defaults
            duration = metadata.get("duration", 0)
            if not isinstance(duration, (int, float)):
                duration = 0
            
            status_code = metadata.get("statusCode", 0)
            if not isinstance(status_code, (int, float)):
                status_code = 0
            
            start_time = metadata.get("startTime", 0)
            if not isinstance(start_time, (int, float)):
                start_time = 0
            
            spans_by_id[span_id] = {
                "log": log,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "span_name": metadata.get("spanName", "unknown") or "unknown",
                "duration": duration,
                "status_code": int(status_code),
                "start_time": start_time,
                "children": []
            }
            
            if not trace_id and metadata.get("traceId"):
                trace_id = metadata.get("traceId")
        except (AttributeError, TypeError, KeyError) as e:
            print(f"⚠️  Error processing log {log.id if hasattr(log, 'id') else 'unknown'} for trace flow: {e}")
            continue
    
    # Build parent-child relationships (prevent circular references)
    root_spans = []
    processed_parents = set()  # Track processed parent-child relationships
    
    for span_id, span_data in spans_by_id.items():
        parent_id = span_data["parent_span_id"]
        # Prevent circular references
        if parent_id and parent_id in spans_by_id and parent_id != span_id:
            parent_key = (parent_id, span_id)
            if parent_key not in processed_parents:
                spans_by_id[parent_id]["children"].append(span_data)
                processed_parents.add(parent_key)
            else:
                # Circular reference detected, treat as root
                root_spans.append(span_data)
        else:
            root_spans.append(span_data)
    
    # Build flow description
    flow_lines = []
    if trace_id:
        flow_lines.append(f"Trace ID: {trace_id}")
        flow_lines.append("")
    
    def format_span(span_data, indent=0, visited=None):
        """Recursively format span tree with cycle detection."""
        if visited is None:
            visited = set()
        
        # Prevent infinite recursion from circular references
        span_id = span_data.get("span_id")
        if span_id in visited:
            flow_lines.append(f"{'  ' * indent}├─ [CYCLE DETECTED: {span_data.get('span_name', 'unknown')}]")
            return
        
        visited.add(span_id)
        
        try:
            prefix = "  " * indent
            span_name = span_data.get("span_name", "unknown") or "unknown"
            duration = span_data.get("duration", 0) or 0
            status = "ERROR" if span_data.get("status_code") == 2 else "OK"
            
            # Safely get log message
            log_msg = ""
            try:
                log = span_data.get("log")
                if log and hasattr(log, "message") and log.message:
                    log_msg = str(log.message)[:100]
            except (AttributeError, TypeError):
                pass
            
            line = f"{prefix}├─ {span_name} ({duration}ms) [{status}]"
            if log_msg:
                line += f": {log_msg}"
            flow_lines.append(line)
            
            # Recursively format children (limit depth to prevent stack overflow)
            if indent < 20:  # Max depth of 20
                for child in span_data.get("children", []):
                    format_span(child, indent + 1, visited)
            else:
                flow_lines.append(f"{'  ' * (indent + 1)}├─ [MAX DEPTH REACHED]")
        except Exception as e:
            print(f"⚠️  Error formatting span: {e}")
            flow_lines.append(f"{'  ' * indent}├─ [ERROR FORMATTING SPAN]")
        finally:
            visited.discard(span_id)  # Remove from visited when done with this branch
    
    for root_span in root_spans:
        format_span(root_span)
    
    return {
        "trace_id": trace_id,
        "spans": list(spans_by_id.values()),
        "flow": "\n".join(flow_lines),
        "total_spans": len(spans_by_id),
        "error_spans": sum(1 for s in spans_by_id.values() if s.get("status_code") == 2)
    }


def build_enhanced_linear_description(
    incident: Incident,
    logs: list[LogEntry],
    db: Session,
    include_trace: bool = True
) -> str:
    """
    Build an enhanced Linear ticket description with trace/span info, diagrams, and all useful details.
    
    Args:
        incident: The incident
        logs: Related log entries
        db: Database session
        include_trace: Whether to include trace execution flow
        
    Returns:
        Formatted markdown description for Linear ticket
    """
    description_parts = []
    
    try:
        # Basic Information
        description_parts.append("## 📋 Incident Details")
        description_parts.append(f"**Service:** {incident.service_name or 'N/A'}")
        description_parts.append(f"**Severity:** {incident.severity or 'N/A'}")
        description_parts.append(f"**Source:** {incident.source or 'N/A'}")
        description_parts.append(f"**Status:** {incident.status or 'N/A'}")
        
        if incident.first_seen_at:
            try:
                description_parts.append(f"**First Seen:** {incident.first_seen_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            except (AttributeError, ValueError):
                description_parts.append(f"**First Seen:** N/A")
        else:
            description_parts.append("**First Seen:** N/A")
        
        if incident.last_seen_at:
            try:
                description_parts.append(f"**Last Seen:** {incident.last_seen_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            except (AttributeError, ValueError):
                description_parts.append(f"**Last Seen:** N/A")
        else:
            description_parts.append("**Last Seen:** N/A")
        
        description_parts.append("")
    except Exception as e:
        print(f"⚠️  Error building basic incident info: {e}")
        description_parts.append("## 📋 Incident Details")
        description_parts.append(f"**Service:** {getattr(incident, 'service_name', 'N/A')}")
        description_parts.append("")
    
    # Description
    if incident.description:
        description_parts.append("## 📝 Description")
        description_parts.append(incident.description)
        description_parts.append("")
    
    # Root Cause (if available)
    if incident.root_cause:
        description_parts.append("## 🔍 Root Cause Analysis")
        description_parts.append(incident.root_cause)
        description_parts.append("")
    
    # Trace and Span Information
    if logs and include_trace:
        trace_ids = set()
        span_info = []
        
        for log in logs:
            try:
                metadata = log.metadata_json or {}
                if isinstance(metadata, dict):
                    trace_id = metadata.get("traceId")
                    span_id = metadata.get("spanId")
                    
                    if trace_id:
                        trace_ids.add(str(trace_id))
                    
                    if span_id:
                        span_name = metadata.get("spanName", "unknown")
                        duration = metadata.get("duration", 0)
                        status_code = metadata.get("statusCode", 0)
                        parent_span_id = metadata.get("parentSpanId")
                        
                        span_info.append({
                            "span_id": span_id,
                            "span_name": span_name,
                            "duration": duration,
                            "status": "ERROR" if status_code == 2 else "OK",
                            "parent_span_id": parent_span_id,
                            "log_id": log.id,
                            "message": log.message[:200] if log.message else ""
                        })
            except (AttributeError, TypeError, KeyError):
                continue
        
        if trace_ids:
            description_parts.append("## 🔗 Trace Information")
            description_parts.append(f"**Trace ID(s):** {', '.join(list(trace_ids)[:3])}")  # Show up to 3 trace IDs
            if len(trace_ids) > 3:
                description_parts.append(f"*({len(trace_ids) - 3} more trace(s))*")
            description_parts.append("")
        
        if span_info:
            description_parts.append("### Spans")
            description_parts.append("| Span ID | Name | Duration | Status | Message |")
            description_parts.append("|---------|------|----------|--------|---------|")
            
            for span in span_info[:20]:  # Limit to 20 spans to avoid overwhelming
                span_id_short = str(span["span_id"])[:16] + "..." if len(str(span["span_id"])) > 16 else str(span["span_id"])
                span_name = span["span_name"][:30] + "..." if len(span["span_name"]) > 30 else span["span_name"]
                duration = f"{span['duration']:.2f}ms" if isinstance(span['duration'], (int, float)) else "N/A"
                status = span["status"]
                message = span["message"][:50] + "..." if len(span["message"]) > 50 else span["message"]
                description_parts.append(f"| `{span_id_short}` | {span_name} | {duration} | {status} | {message} |")
            
            if len(span_info) > 20:
                description_parts.append(f"*({len(span_info) - 20} more span(s))*")
            description_parts.append("")
            
            # Build trace execution flow
            try:
                # Get all logs from the same trace
                trace_logs = get_trace_logs(logs, db, user_id=incident.user_id)
                if len(trace_logs) > len(logs):
                    trace_flow = build_trace_execution_flow(trace_logs)
                    
                    if trace_flow and trace_flow.get("flow"):
                        description_parts.append("### 📊 Trace Execution Flow")
                        description_parts.append("```")
                        description_parts.append(trace_flow.get("flow", ""))
                        description_parts.append("```")
                        description_parts.append("")
                        description_parts.append(f"**Statistics:** {trace_flow.get('total_spans', 0)} total spans, {trace_flow.get('error_spans', 0)} error span(s)")
                        description_parts.append("")
            except Exception as e:
                print(f"⚠️  Error building trace flow for Linear ticket: {e}")
    
    # Stack Traces (filter out node_modules; reuse shared trace extraction)
    stack_traces = []
    for log in logs[:10]:
        try:
            for trace_str in _get_trace_strings_from_log(log):
                if not is_stacktrace_from_node_modules(trace_str):
                    stack_traces.append({
                        "log_id": log.id,
                        "message": trace_str[:1000]
                    })
        except (AttributeError, TypeError, KeyError):
            continue
    
    if stack_traces:
        description_parts.append("## 📚 Stack Traces")
        for i, trace in enumerate(stack_traces[:5], 1):  # Limit to 5 stack traces
            description_parts.append(f"### Stack Trace {i} (Log ID: {trace['log_id']})")
            description_parts.append("```")
            description_parts.append(trace["message"])
            description_parts.append("```")
            description_parts.append("")
    
    # Related Logs Summary
    if logs:
        description_parts.append("## 📋 Related Logs Summary")
        description_parts.append(f"**Total Logs:** {len(logs)}")
        description_parts.append("")
        
        # Show first 5 error logs
        error_logs = [log for log in logs if log.severity and log.severity.upper() in ["ERROR", "CRITICAL"]][:5]
        if error_logs:
            description_parts.append("### Recent Error Logs")
            for log in error_logs:
                timestamp = log.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC') if log.timestamp else 'N/A'
                message = log.message[:150] + "..." if log.message and len(log.message) > 150 else (log.message or "No message")
                description_parts.append(f"- **[{timestamp}]** `{log.severity}`: {message}")
            description_parts.append("")
    
    # Metadata (if available)
    if incident.metadata_json and isinstance(incident.metadata_json, dict):
        # Only include relevant metadata, not everything
        relevant_metadata = {}
        for key in ["environment", "version", "deployment", "region", "host", "container_id"]:
            if key in incident.metadata_json:
                relevant_metadata[key] = incident.metadata_json[key]
        
        if relevant_metadata:
            description_parts.append("## 🏷️ Metadata")
            for key, value in relevant_metadata.items():
                description_parts.append(f"**{key.replace('_', ' ').title()}:** {value}")
            description_parts.append("")
    
    # Action Taken (if available)
    if incident.action_taken:
        description_parts.append("## ⚡ Action Taken")
        description_parts.append(incident.action_taken)
        description_parts.append("")
    
    # Repository Information
    if incident.repo_name:
        description_parts.append("## 🔗 Repository")
        description_parts.append(f"**Repo:** `{incident.repo_name}`")
        description_parts.append("")
    
    return "\n".join(description_parts)


def analyze_incident_with_openrouter(incident: Incident, logs: list[LogEntry], db: Session) -> Dict[str, Any]:
    """
    Analyze an incident using OpenRouter AI to determine root cause and recommended actions.
    
    COST OPTIMIZATION:
    - Uses cheaper models (Gemini Flash / Claude Haiku) for simple analysis
    - Only uses expensive model (Claude 3.5 Sonnet) for code generation
    - Checks for cached similar incidents
    - Reduced token limits
    
    Args:
        incident: The incident to analyze
        logs: List of related log entries
        db: Database session
        
    Returns:
        Dict with 'root_cause' and 'action_taken' keys
    """
    if not get_api_key():
        print("⚠️  OPENCOUNCIL_API not set, skipping AI analysis")
        # Return an error message instead of None so UI stops loading
        return {
            "root_cause": "AI analysis is not configured. Please set OPENCOUNCIL_API environment variable.",
            "action_taken": None
        }
    
    # COST OPTIMIZATION: Check for similar incidents with existing analysis
    # Look for incidents with same service/severity that have root_cause
    try:
        incident_fingerprint = get_incident_fingerprint(incident, logs)
        # Safely query for similar incidents
        query = db.query(Incident).filter(
            Incident.id != incident.id,
            Incident.service_name == incident.service_name,
            Incident.severity == incident.severity,
            Incident.user_id == incident.user_id,
            Incident.root_cause.isnot(None),
            Incident.root_cause != "",
            # Within last 7 days
            Incident.created_at >= datetime.utcnow() - timedelta(days=7)
        )
        similar_incident = query.order_by(Incident.created_at.desc()).first()
        
        if similar_incident and similar_incident.root_cause:
            try:
                # Check if root cause is similar (simple similarity check)
                similar_keywords = set(re.findall(r'\b\w{4,}\b', similar_incident.root_cause.lower()))
                current_keywords = set()
                for log in (logs or [])[:5]:
                    if log and log.message:
                        current_keywords.update(re.findall(r'\b\w{4,}\b', log.message.lower()))
                
                # If 30% keywords match, reuse analysis
                if len(similar_keywords) > 0:
                    try:
                        similarity = len(similar_keywords & current_keywords) / len(similar_keywords)
                        if similarity > 0.3:
                            print(f"💰 Reusing analysis from similar incident {similar_incident.id} (similarity: {similarity:.1%})")
                            return {
                                "root_cause": similar_incident.root_cause or "Analysis pending...",
                                "action_taken": similar_incident.action_taken,
                                "cached": True
                            }
                    except (ZeroDivisionError, TypeError) as e:
                        print(f"⚠️  Error calculating similarity: {e}")
                        # Continue with normal analysis
            except Exception as similarity_error:
                print(f"⚠️  Error calculating similarity: {similarity_error}")
                # Continue with normal analysis
    except Exception as e:
        print(f"⚠️  Error checking for similar incidents: {e}")
        traceback.print_exc()
        # Continue with normal analysis
    
    # ENHANCEMENT: Get all logs from the same trace(s) for better context
    trace_logs = logs
    trace_flow = None
    if logs:
        try:
            trace_logs = get_trace_logs(logs, db, user_id=incident.user_id)
            if len(trace_logs) > len(logs):
                print(f"✅ Expanded context: {len(logs)} incident logs → {len(trace_logs)} trace logs")
            
            # Build execution flow from trace
            trace_flow = build_trace_execution_flow(trace_logs)
            if trace_flow.get("flow"):
                print(f"📊 Built trace execution flow with {trace_flow.get('total_spans', 0)} spans")
        except Exception as e:
            print(f"⚠️  Error gathering trace context: {e}")
            traceback.print_exc()
            # Fallback to original logs if trace gathering fails
            trace_logs = logs
    
    # Prepare context from incident and logs (now using trace_logs for better context)
    # COST OPTIMIZATION: Reduced limits for incident analysis
    # Using module-level constants
    
    logs_for_context = []
    total_log_tokens = 0
    
    for log in (trace_logs or [])[:100]:  # Start with max 100 logs
        try:
            timestamp_str = "N/A"
            if log.timestamp:
                if isinstance(log.timestamp, datetime):
                    timestamp_str = log.timestamp.isoformat()
                else:
                    timestamp_str = str(log.timestamp)
            
            level = log.level or "UNKNOWN"
            message = log.message or "No message"
            
            # Truncate individual log messages to token limit
            if len(message) > 525:  # ~150 tokens (reduced for cost)
                message = message[:525] + "... [truncated]"
            
            log_line = f"[{timestamp_str}] {level}: {message}"
            log_tokens = estimate_tokens(log_line)
            
            # Check if adding this log would exceed limit
            if total_log_tokens + log_tokens > MAX_TOKENS_FOR_LOGS:
                break
            
            logs_for_context.append(log_line)
            total_log_tokens += log_tokens
        except Exception as e:
            print(f"⚠️  Error formatting log for context: {e}")
            continue
    
    log_context = "\n".join(logs_for_context)
    
    if not log_context:
        log_context = "No related logs available."
    
    # Add trace execution flow to context if available
    # Limit flow size to prevent prompt overflow
    trace_context = ""
    if trace_flow and trace_flow.get("flow"):
        flow_text = trace_flow.get('flow', '')
        # Limit trace flow to ~5K tokens (reduced for cost)
        MAX_TOKENS_FOR_TRACE_FLOW = 5000  # Reduced from 10K to 5K
        flow_tokens = estimate_tokens(flow_text)
        if flow_tokens > MAX_TOKENS_FOR_TRACE_FLOW:
            print(f"⚠️  Trace flow is large ({flow_tokens} tokens), truncating to {MAX_TOKENS_FOR_TRACE_FLOW} tokens")
            flow_text = truncate_to_token_limit(flow_text, MAX_TOKENS_FOR_TRACE_FLOW, "\n... [trace flow truncated]")
        
        trace_context = f"""
Trace Execution Flow:
{flow_text}

Trace Statistics:
- Total Spans: {trace_flow.get('total_spans', 0)}
- Error Spans: {trace_flow.get('error_spans', 0)}
- Trace ID: {trace_flow.get('trace_id', 'N/A')[:50]}

"""
    
    # MEMORY: Retrieve context from CodeMemory
    memory_context_str = ""
    try:
        code_memory = CodeMemory()
        # Recalculate or use existing fingerprint
        mem_fingerprint = get_incident_fingerprint(incident, logs)
        memory_data = code_memory.retrieve_context(mem_fingerprint)

        known_fixes = memory_data.get("known_fixes", [])
        past_errors = memory_data.get("past_errors", [])

        if known_fixes or past_errors:
            print(f"🧠 Found {len(known_fixes)} known fixes and {len(past_errors)} past error contexts in memory")

            memory_context_parts = []
            if known_fixes:
                memory_context_parts.append("KNOWN FIXES FROM PAST INCIDENTS:")
                for i, fix in enumerate(known_fixes[:3]): # Limit to top 3
                    memory_context_parts.append(f"Fix #{i+1}: {fix.get('description', 'No description')}")

            if past_errors:
                memory_context_parts.append("PAST ERROR CONTEXT:")
                for i, err in enumerate(past_errors[:2]):
                    memory_context_parts.append(f"Context #{i+1}: {err.get('context', '')[:500]}...")

            memory_context_str = "\n".join(memory_context_parts)
            memory_context_str = f"\n\nSYSTEM MEMORY (Previous Incidents):\n{memory_context_str}\n"
    except Exception as e:
        print(f"⚠️ Error retrieving code memory: {e}")

    # Build incident context
    incident_details = f"""
Incident Details:
- Title: {incident.title}
- Service: {incident.service_name}
- Source: {incident.source or 'Unknown'}
- Severity: {incident.severity}
- Status: {incident.status}
- First Seen: {incident.first_seen_at}
- Last Seen: {incident.last_seen_at}
- Description: {incident.description or 'No description'}

{memory_context_str}
{trace_context}Related Logs (including full trace context):
{log_context}
"""
    
    # Check total context size
    context_tokens = estimate_tokens(incident_details)
    if context_tokens > MAX_INCIDENT_PROMPT_TOKENS:
        print(f"⚠️  Incident context exceeds limit ({context_tokens} tokens), truncating")
        # Truncate log context to fit
        base_context = incident_details.replace(log_context, "")
        available_for_logs = MAX_INCIDENT_PROMPT_TOKENS - estimate_tokens(base_context)
        if available_for_logs > 0:
            log_context = truncate_to_token_limit(log_context, available_for_logs)
            incident_context = base_context.replace("Related Logs (including full trace context):\n", f"Related Logs (including full trace context):\n{log_context}")
        else:
            incident_context = base_context.replace("Related Logs (including full trace context):\n", "Related Logs (including full trace context):\n[Logs context too large, truncated]")
    else:
        incident_context = incident_details
    
    # Prepare the prompt for root cause analysis
    base_prompt = f"""You are an expert SRE (Site Reliability Engineer) analyzing an incident. 

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
    
    # Final check on total prompt
    prompt_tokens = estimate_tokens(base_prompt)
    print(f"📊 Incident analysis prompt: ~{prompt_tokens} tokens (logs: ~{total_log_tokens} tokens)")
    
    if prompt_tokens > MAX_INCIDENT_PROMPT_TOKENS:
        print(f"⚠️  Prompt exceeds limit ({prompt_tokens} tokens), truncating to {MAX_INCIDENT_PROMPT_TOKENS} tokens")
        prompt = truncate_to_token_limit(base_prompt, MAX_INCIDENT_PROMPT_TOKENS)
    else:
        prompt = base_prompt
    
    try:
        # COST OPTIMIZATION: Use cheaper model for initial analysis
        # Only use expensive model if code generation is needed
        use_expensive = should_use_expensive_model(logs, None)
        
        if use_expensive:
            model_config = MODEL_CONFIG["complex_analysis"]  # Claude 3 Haiku
            print("💰 Using Claude 3 Haiku for complex analysis")
        else:
            model_config = MODEL_CONFIG["simple_analysis"]  # Gemini Flash
            print("💰 Using Gemini Flash for simple analysis (cost-optimized)")
        
        r = openrouter_chat_completion(
            model_config["model"],
            [{"role": "user", "content": prompt}],
            temperature=model_config["temperature"],
            max_tokens=model_config["max_tokens"],
            timeout=int(os.getenv("HTTP_LLM_API_TIMEOUT", "60")),
            title="HealOps Incident Analysis"
        )
        
        if r["status_code"] == 402:
            print(f"❌ OpenRouter API: Insufficient credits - {r.get('error_message', '')}")
            return {
                "root_cause": "AI analysis is currently unavailable due to insufficient API credits. Please add credits at https://openrouter.ai/settings/credits or contact your administrator.",
                "action_taken": None
            }
        if not r["success"]:
            print(f"❌ OpenRouter API error (status {r['status_code']}): {r.get('error_message', '')}")
            return {
                "root_cause": f"Analysis failed: {r.get('error_message', 'Unknown error')}. Please check OpenRouter API configuration or try again later.",
                "action_taken": None
            }
        
        content = r["content"] or ""
        
        # COST TRACKING: Log token usage for monitoring
        usage = r.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", 0) or 0
        output_tokens = usage.get("completion_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        
        # Estimate cost (approximate) - safely handle model_config
        try:
            model_name = model_config.get("model", "unknown") if isinstance(model_config, dict) else "unknown"
            if "xiaomi/mimo-v2-flash" in model_name:
                # MiMo-V2-Flash: $0.09/M input, $0.29/M output (https://openrouter.ai/xiaomi/mimo-v2-flash)
                estimated_cost = (input_tokens * 0.09 / 1_000_000) + (output_tokens * 0.29 / 1_000_000)
            elif "grok-code-fast" in model_name:
                # Grok Code Fast 1: $0.20/M Input, $1.50/M Output
                estimated_cost = (input_tokens * 0.20 / 1_000_000) + (output_tokens * 1.50 / 1_000_000)
            elif "gemini-flash-1.5-8b" in model_name:
                estimated_cost = (input_tokens * 0.0375 / 1_000_000) + (output_tokens * 0.15 / 1_000_000)
            elif model_name == "google/gemini-flash-1.5":
                estimated_cost = (input_tokens * 0.075 / 1_000_000) + (output_tokens * 0.30 / 1_000_000)
            elif model_name == "anthropic/claude-3-haiku":
                estimated_cost = (input_tokens * 0.25 / 1_000_000) + (output_tokens * 1.25 / 1_000_000)
            else:  # Claude 3.5 Sonnet or default
                estimated_cost = (input_tokens * 3.00 / 1_000_000) + (output_tokens * 15.00 / 1_000_000)
            
            print(f"💰 Cost: ~${estimated_cost:.4f} | Tokens: {input_tokens} in + {output_tokens} out = {total_tokens} total | Model: {model_name}")
        except Exception as cost_error:
            print(f"⚠️  Error calculating cost: {cost_error}")
            estimated_cost = 0.0
        
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
            
            # IMPROVED DECISION LOGIC: Check multiple signals to determine if code changes are needed
            # 1. Check action_taken for code-related keywords
            # 2. Check root_cause for code-related issues
            # 3. Check logs for code-related error patterns (stack traces, syntax errors, etc.)
            should_generate_code = False
            code_fix_explanation = None
            
            # Expanded keyword list for code-related actions
            code_action_keywords = [
                "fix", "update", "change", "modify", "add", "remove", "patch", "bug", "code",
                "correct", "resolve", "address", "implement", "refactor", "rewrite", "adjust",
                "edit", "improve", "enhance", "repair", "solve", "handle", "prevent"
            ]
            
            # Keywords that indicate code-related root causes
            code_root_cause_keywords = [
                "bug", "error", "exception", "syntax", "logic", "function", "method", "class",
                "variable", "parameter", "argument", "type", "null", "undefined", "reference",
                "index", "key", "attribute", "property", "import", "module", "package",
                "stack trace", "traceback", "line", "file", "code", "implementation"
            ]
            
            # Keywords that indicate infrastructure/operational issues (NOT code)
            operational_keywords = [
                "restart", "reboot", "redeploy", "scale", "capacity", "resource", "memory",
                "cpu", "disk", "network", "timeout", "connection", "configuration", "config",
                "environment", "env", "secret", "credential", "permission", "access", "auth",
                "external", "dependency", "service", "api", "endpoint", "infrastructure"
            ]
            
            # Check 1: action_taken for code-related keywords
            action_suggests_code = False
            if action_taken:
                action_lower = action_taken.lower()
                action_suggests_code = any(keyword in action_lower for keyword in code_action_keywords)
                # Exclude if it's clearly operational
                is_operational = any(keyword in action_lower for keyword in operational_keywords)
                if is_operational and not any(c in action_lower for c in ["code", "bug", "fix", "patch"]):
                    action_suggests_code = False
            
            # Check 2: root_cause for code-related issues
            root_cause_suggests_code = False
            if root_cause:
                root_cause_lower = root_cause.lower()
                root_cause_suggests_code = any(keyword in root_cause_lower for keyword in code_root_cause_keywords)
            
            # Check 3: logs for code-related error patterns
            logs_suggest_code = False
            if logs:
                for log in logs[:20]:  # Check first 20 logs
                    if log and log.message:
                        log_msg = log.message.lower()
                        # Look for stack traces, syntax errors, code-related exceptions
                        code_patterns = [
                            "traceback", "stack trace", "at ", "file \"", "line ", "syntaxerror",
                            "typeerror", "valueerror", "attributeerror", "keyerror", "indexerror",
                            "nameerror", "indentationerror", "importerror", "modulenotfounderror",
                            "function", "method", "class", "undefined", "null pointer", "none"
                        ]
                        # Check if log contains code-related patterns
                        has_code_pattern = any(pattern in log_msg for pattern in code_patterns)
                        
                        if has_code_pattern:
                            # If we have trace strings, verify at least one is NOT from node_modules
                            trace_strs = _get_trace_strings_from_log(log)
                            if trace_strs:
                                if not any(is_stacktrace_from_node_modules(t) for t in trace_strs):
                                    logs_suggest_code = True
                                    break
                            else:
                                # Pattern match but no trace text, or other code-related error
                                logs_suggest_code = True
                                break
            
            # Decision: Generate code if ANY signal suggests code changes
            # But exclude if action_taken is clearly operational (unless root_cause strongly suggests code)
            should_generate_code = (
                action_suggests_code or 
                (root_cause_suggests_code and not any(op in (action_taken or "").lower() for op in ["restart", "reboot", "redeploy"])) or
                logs_suggest_code
            )
            
            # Debug logging for decision transparency
            print(f"🔍 Code generation decision for incident {incident.id}:")
            print(f"   - Action suggests code: {action_suggests_code}")
            print(f"   - Root cause suggests code: {root_cause_suggests_code}")
            print(f"   - Logs suggest code: {logs_suggest_code}")
            print(f"   - Final decision: {'GENERATE CODE' if should_generate_code else 'SKIP CODE GENERATION'}")
            
            # Build result dictionary with decision metadata
            result = {
                "root_cause": root_cause,
                "action_taken": action_taken,
                "cost_estimate": estimated_cost,
                "tokens_used": total_tokens,
                "model_used": model_config.get("model", "unknown") if isinstance(model_config, dict) else "unknown",
                # Decision metadata for debugging and transparency
                "code_generation_decision": {
                    "should_generate_code": should_generate_code,
                    "action_suggests_code": action_suggests_code,
                    "root_cause_suggests_code": root_cause_suggests_code,
                    "logs_suggest_code": logs_suggest_code
                }
            }
            
            # If GitHub integration is available and code changes are needed, try to analyze repo and create PR
            if not incident.integration_id:
                code_fix_explanation = "No GitHub integration configured for this incident. Please set up a GitHub integration to enable automatic code fixes."
            elif not should_generate_code:
                # Provide more detailed explanation
                reasons = []
                if not action_suggests_code and action_taken:
                    reasons.append(f"action '{action_taken}' suggests operational/infrastructure fix")
                if not root_cause_suggests_code and root_cause:
                    reasons.append("root cause analysis doesn't indicate code issues")
                if not logs_suggest_code:
                    reasons.append("logs don't show code-related error patterns")
                
                reason_text = ", ".join(reasons) if reasons else "analysis indicates operational/infrastructure issue"
                code_fix_explanation = (
                    f"Code changes not generated: {reason_text}. "
                    f"This appears to require manual intervention (e.g., restart service, check configuration, "
                    f"verify external dependencies, scale resources). If you believe this needs a code fix, "
                    f"please review the root cause and action taken fields."
                )
            elif incident.integration_id and should_generate_code:
                try:
                    integration = db.query(Integration).filter(Integration.id == incident.integration_id).first()
                    repo_name = None
                    github_integration = None

                    if integration and integration.provider == "GITHUB":
                        # Use repo_name from incident if available (assigned during creation),
                        # otherwise fall back to looking it up from integration config
                        if hasattr(incident, 'repo_name') and incident.repo_name:
                            repo_name = incident.repo_name
                            print(f"📌 Using repo_name from incident: {repo_name}")
                        else:
                            repo_name = get_repo_name_from_integration(integration, service_name=incident.service_name)
                            if repo_name:
                                print(f"🔍 Found repo_name from integration config: {repo_name}")
                        try:
                            github_integration = GithubIntegration(integration_id=integration.id)
                            if github_integration.client:
                                verification = github_integration.verify_connection()
                                if verification.get("status") == "verified":
                                    print(f"✅ GitHub connection verified: {verification.get('username', 'N/A')}")
                                else:
                                    print(f"⚠️  GitHub connection verification failed: {verification.get('message', 'Unknown error')}")
                            else:
                                print(f"⚠️  GitHub client not initialized after loading integration")
                        except Exception as e:
                            print(f"⚠️  Warning: Failed to load GitHub integration: {e}")
                            traceback.print_exc()
                            github_integration = None
                    else:
                        # Incident from SigNoz or other non-GitHub source: use incident.repo_name and user's GitHub integration
                        if hasattr(incident, 'repo_name') and incident.repo_name:
                            repo_name = incident.repo_name
                            print(f"📌 Using repo_name from incident (non-GitHub source): {repo_name}")
                            github_integration_obj = (
                                db.query(Integration)
                                .filter(
                                    Integration.user_id == incident.user_id,
                                    Integration.provider == "GITHUB",
                                    Integration.status == "ACTIVE",
                                )
                                .first()
                            )
                            if github_integration_obj:
                                try:
                                    github_integration = GithubIntegration(integration_id=github_integration_obj.id)
                                    if github_integration.client:
                                        verification = github_integration.verify_connection()
                                        if verification.get("status") == "verified":
                                            print(f"✅ GitHub connection verified (user's GitHub): {verification.get('username', 'N/A')}")
                                except Exception as e:
                                    print(f"⚠️  Warning: Failed to load user GitHub integration: {e}")
                                    traceback.print_exc()
                                    github_integration = None

                    if repo_name and github_integration:
                            print(f"🔍 Analyzing repository {repo_name} for incident {incident.id} (service: {incident.service_name})")
                            # Only proceed with agent if GitHub integration is available (already loaded above)
                            if not github_integration:
                                print(f"⚠️  Skipping agent execution - GitHub integration not available")
                                code_fix_explanation = (
                                    f"Code changes not generated: GitHub integration is not available or failed to load. "
                                    f"Please verify your GitHub integration is properly configured."
                                )
                                # Skip agent execution - will continue to code_fix_explanation handling at end
                            else:
                                # Pre-check: skip resolution if error is from node_modules or external code
                                is_external, sample_trace = is_incident_from_external_code(incident, logs)
                                if is_external:
                                    print("⚠️  Skipping agent execution - error is from node_modules or external code")
                                    code_fix_explanation = build_skipped_resolution_description(
                                        incident=incident,
                                        sample_trace=sample_trace,
                                    )
                                    result["resolution_skipped"] = True
                                    result["resolution_skipped_reason"] = "node_modules_or_external_code"
                                    # Skip agent execution - will continue to code_fix_explanation handling at end
                                else:
                                    # Use robust crew system (Manus-style architecture)
                                    try:
                                        print("🚀 Using robust multi-agent crew system (Manus-style)")
                                        from src.agents.orchestrator import run_robust_crew
                                        enhanced_result = run_robust_crew(
                                            incident=incident,
                                            logs=logs,
                                            root_cause=root_cause,
                                            github_integration=github_integration,
                                            repo_name=repo_name,
                                            db=db
                                        )
                                        
                                        # Process robust crew result - adapt format
                                        # Robust crew returns: status, fixes, events, plan_progress
                                        fixes = enhanced_result.get("fixes", {})
                                        
                                        # Convert fixes to PR format
                                        changes = {}
                                        for file_path, file_info in fixes.items():
                                            if isinstance(file_info, dict):
                                                changes[file_path] = file_info.get("content", "")
                                            else:
                                                changes[file_path] = str(file_info)
                                        
                                        # Determine decision based on result
                                        if enhanced_result.get("status") == "success" and changes:
                                            decision_action = "CREATE_PR"
                                            confidence_score = 85  # Default confidence for robust crew
                                        elif enhanced_result.get("status") == "partial" and changes:
                                            decision_action = "CREATE_DRAFT_PR"
                                            confidence_score = 70
                                        else:
                                            decision_action = "SKIP_PR"
                                            confidence_score = 0
                                        
                                        is_draft = decision_action == "CREATE_DRAFT_PR"
                                        
                                        # Create decision object for compatibility
                                        decision = {
                                            "action": decision_action,
                                            "reasoning": f"Robust crew completed with {len(changes)} files changed. Status: {enhanced_result.get('status')}",
                                            "warnings": [] if decision_action == "CREATE_PR" else ["Partial completion - review recommended"]
                                        }
                                        
                                        if changes and decision_action in ["CREATE_PR", "CREATE_PR_WITH_WARNINGS", "CREATE_DRAFT_PR"]:
                                            # Build PR body with appropriate warnings
                                            warnings_text = ""
                                            if decision_action == "CREATE_PR_WITH_WARNINGS":
                                                warnings = enhanced_result.get("decision", {}).get("warnings", [])
                                                if warnings:
                                                    warnings_text = "\n### ⚠️ Warnings\n" + "\n".join(f"- {w}" for w in warnings) + "\n"
                                            elif decision_action == "CREATE_DRAFT_PR":
                                                warnings = enhanced_result.get("decision", {}).get("warnings", [])
                                                if warnings:
                                                    warnings_text = "\n### ⚠️ Low Confidence - Draft PR\n" + "\n".join(f"- {w}" for w in warnings) + "\n"
                                                warnings_text += "\n**This is a draft PR. Please review the changes on the incident page before merging.**\n"
                                            
                                            # Check if incident has Linear issue for branch naming
                                            linear_issue_id = None
                                            linear_issue_url = None
                                            linear_issue_identifier = None
                                            if incident.metadata_json and incident.metadata_json.get("linear_issue"):
                                                linear_issue_data = incident.metadata_json["linear_issue"]
                                                linear_issue_identifier = linear_issue_data.get("identifier")  # e.g., "ID-123"
                                                linear_issue_url = linear_issue_data.get("url")
                                            
                                            # Use Linear standard format for branch name if Linear issue exists
                                            if linear_issue_identifier:
                                                # Linear format: ID-123-fix-description
                                                branch_suffix = incident.title.lower().replace(' ', '-')[:30]
                                                # Remove special characters that might cause issues
                                                branch_suffix = ''.join(c for c in branch_suffix if c.isalnum() or c == '-')
                                                head_branch = f"{linear_issue_identifier}-fix-{branch_suffix}"
                                            else:
                                                head_branch = f"healops-enhanced-fix-{incident.id}"
                                            
                                            # Add Linear issue reference to PR body
                                            linear_ref = ""
                                            if linear_issue_identifier and linear_issue_url:
                                                linear_ref = f"\n**Linear Issue:** [{linear_issue_identifier}]({linear_issue_url})\n"
                                            
                                            pr_result = github_integration.create_pr(
                                                repo_name=repo_name,
                                                title=f"Fix: {incident.title} (Enhanced AI)" + (" [DRAFT]" if is_draft else ""),
                                                body=f"""## Incident Fix (Enhanced AI)

**Incident ID:** #{incident.id}
**Service:** {incident.service_name}
**Root Cause:** {root_cause}
{linear_ref}
### AI Analysis
This PR was generated using the enhanced multi-agent system with:
- Interactive codebase exploration
- Incremental edits (Cursor-style)
- Multi-layer validation
- Confidence scoring: {confidence_score}%

{warnings_text}
### Decision
{decision.get('reasoning', 'N/A')}

### Files Changed
{chr(10).join(f"- `{path}`" for path in changes.keys())}

---
*Generated by HealOps Enhanced Multi-Agent System*
""",
                                                head_branch=head_branch,
                                                base_branch="main",
                                                changes=changes,
                                                draft=is_draft
                                            )
                                            
                                            # Track PR created by Alex for QA review
                                            if pr_result.get("status") == "success":
                                                try:
                                                    from src.database.models import AgentPR, AgentEmployee
                                                    pr_number = pr_result.get("pr_number")
                                                    pr_url = pr_result.get("pr_url")
                                                    
                                                    # Find Alex agent
                                                    alex_agent = db.query(AgentEmployee).filter(
                                                        AgentEmployee.email == "alexandra.chen@healops.work"
                                                    ).first()
                                                    
                                                    if alex_agent:
                                                        agent_pr = AgentPR(
                                                            pr_number=pr_number,
                                                            repo_name=repo_name,
                                                            pr_url=pr_url,
                                                            title=f"Fix: {incident.title} (Enhanced AI)" + (" [DRAFT]" if is_draft else ""),
                                                            head_branch=head_branch,
                                                            base_branch="main",
                                                            agent_employee_id=alex_agent.id,
                                                            agent_name=alex_agent.name,
                                                            incident_id=incident.id,
                                                            qa_review_status="pending"
                                                        )
                                                        db.add(agent_pr)
                                                        db.commit()
                                                        print(f"✅ Tracked Enhanced AI PR #{pr_number} created by {alex_agent.name} for QA review")
                                                        
                                                        # Update Linear issue with PR link if Linear integration exists
                                                        if linear_issue_identifier and incident.metadata_json and incident.metadata_json.get("linear_issue"):
                                                            try:
                                                                from src.utils.integrations import get_linear_integration_for_user
                                                                from src.integrations.linear.integration import LinearIntegration
                                                                
                                                                linear_integration_obj = get_linear_integration_for_user(db, incident.user_id)
                                                                if linear_integration_obj:
                                                                    linear_integration = LinearIntegration(integration_id=linear_integration_obj.id)
                                                                    linear_issue_id = incident.metadata_json["linear_issue"]["id"]
                                                                    
                                                                    # Add comment to Linear issue with PR link
                                                                    comment_body = f"**Pull Request Created**\n\nPR: {pr_url}\nBranch: `{head_branch}`\n\nThis PR addresses the incident and includes the fix."
                                                                    linear_integration.add_comment_to_issue(linear_issue_id, comment_body)
                                                                    print(f"✅ Added PR link to Linear issue {linear_issue_identifier}")
                                                            except Exception as e:
                                                                print(f"⚠️  Failed to update Linear issue with PR link: {e}")
                                                                traceback.print_exc()
                                                        
                                                        # Trigger QA review
                                                        try:
                                                            from src.agents.qa_orchestrator import review_pr_for_alex
                                                            import asyncio
                                                            integration_id = None
                                                            if hasattr(github_integration, 'installation_id') and github_integration.installation_id:
                                                                integration = db.query(Integration).filter(
                                                                    Integration.installation_id == github_integration.installation_id
                                                                ).first()
                                                                if integration:
                                                                    integration_id = integration.id
                                                            
                                                            asyncio.create_task(
                                                                review_pr_for_alex(
                                                                    repo_name=repo_name,
                                                                    pr_number=pr_number,
                                                                    user_id=incident.user_id,
                                                                    integration_id=integration_id,
                                                                    db=db
                                                                )
                                                            )
                                                        except Exception as e:
                                                            print(f"⚠️  Failed to trigger QA review: {e}")
                                                except Exception as e:
                                                    print(f"⚠️  Failed to track Enhanced AI PR for QA review: {e}")
                                            
                                            if pr_result.get("status") == "success":
                                                result["pr_url"] = pr_result.get("pr_url")
                                                result["pr_number"] = pr_result.get("pr_number")
                                                result["pr_files_changed"] = list(changes.keys())
                                                result["changes"] = changes
                                                result["enhanced_crew"] = True
                                                result["confidence_score"] = confidence_score
                                                result["is_draft"] = is_draft
                                                result["decision"] = enhanced_result.get("decision")
                                                
                                                # Store original file contents for comparison on UI
                                                original_contents = {}
                                                for file_path in changes.keys():
                                                    original_content = github_integration.get_file_contents(repo_name, file_path, ref="main")
                                                    if original_content:
                                                        original_contents[file_path] = original_content
                                                result["original_contents"] = original_contents
                                                
                                                if is_draft:
                                                    print(f"📝 Created DRAFT PR using enhanced crew: {pr_result.get('pr_url')}")
                                                else:
                                                    print(f"✅ Created PR using enhanced crew: {pr_result.get('pr_url')}")
                                            else:
                                                print(f"⚠️  Enhanced crew PR creation failed: {pr_result.get('message')}")
                                                result["pr_error"] = pr_result.get("message")
                                                result["code_fix_explanation"] = f"Agent attempted to create a pull request but encountered an error: {pr_result.get('message')}. The code changes were generated but could not be submitted to GitHub."
                                        elif not changes:
                                            print("⚠️  Enhanced crew generated no changes")
                                            result["enhanced_crew"] = True
                                            result["decision"] = enhanced_result.get("decision")
                                            # Capture explanation for why no changes were generated
                                            decision_info = enhanced_result.get("decision", {})
                                            reasoning = decision_info.get("reasoning", "The agent analyzed the codebase but determined no code changes are needed.")
                                            result["code_fix_explanation"] = f"Agent attempted to generate code fixes but found no changes necessary. {reasoning}"
                                        else:
                                            # SKIP_PR or other action - still store decision and changes for UI
                                            print(f"⚠️  Enhanced crew decision: {decision_action}")
                                            result["enhanced_crew"] = True
                                            result["decision"] = enhanced_result.get("decision")
                                            result["confidence_score"] = confidence_score
                                            # Capture explanation for why PR was skipped
                                            decision_info = enhanced_result.get("decision", {})
                                            reasoning = decision_info.get("reasoning", f"Agent determined that creating a PR is not appropriate. Status: {enhanced_result.get('status')}")
                                            result["code_fix_explanation"] = f"Agent analyzed the incident and attempted to generate fixes. {reasoning}"
                                            if changes:
                                                result["changes"] = changes
                                                # Store original contents for UI display
                                                original_contents = {}
                                                for file_path in changes.keys():
                                                    original_content = github_integration.get_file_contents(repo_name, file_path, ref="main")
                                                    if original_content:
                                                        original_contents[file_path] = original_content
                                                result["original_contents"] = original_contents
                                    
                                    except Exception as e:
                                        error_trace = traceback.format_exc()
                                        print(f"⚠️  Enhanced crew failed: {e}")
                                        print(f"Full traceback:\n{error_trace}")
                                        result["pr_error"] = str(e)
                                        result["code_fix_explanation"] = f"Enhanced crew execution failed: {str(e)[:200]}. Please check logs for details."
                    else:
                        print(f"⚠️  No repository name found for incident {incident.id} (integration {integration.id})")
                        code_fix_explanation = f"No repository name configured for this service ({incident.service_name}). Please configure the repository name in the GitHub integration settings (service mappings or default repo_name)."
                except Exception as e:
                    print(f"⚠️  Error during GitHub analysis: {e}")
                    print(traceback.format_exc())
                    # Don't fail the whole analysis if PR creation fails
            
            # Store code fix explanation if available
            if code_fix_explanation:
                result["code_fix_explanation"] = code_fix_explanation
            
            return result
        except json.JSONDecodeError:
            # If JSON parsing fails, extract text between markers or use the whole response
            print(f"⚠️  Failed to parse JSON from AI response: {content[:200]}")
            # Fallback: use the entire response as root_cause
            return {
                "root_cause": content[:500] if content else "Analysis pending...",
                "action_taken": None
            }
            
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"❌ Error calling OpenRouter API: {e}")
        print(f"Full traceback: {error_trace}")
        # Return a fallback message instead of None so the UI stops loading
        return {
            "root_cause": f"Analysis failed: {str(e)[:200]}. Please check OPENCOUNCIL_API configuration.",
            "action_taken": None
        }
