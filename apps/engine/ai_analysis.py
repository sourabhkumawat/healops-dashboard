"""
AI Analysis module for incident root cause analysis using OpenRouter.
"""
import os
import json
import re
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from models import Incident, LogEntry, Integration
from sqlalchemy.orm import Session
from integrations.github_integration import GithubIntegration
from memory import CodeMemory

# Cost-optimized model configuration
# Use cheaper models for simpler tasks, expensive models only when needed
MODEL_CONFIG = {
    "simple_analysis": {
        "model": "xiaomi/mimo-v2-flash:free",  # Free model
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
    }
}

# COST OPTIMIZATION: Reduced limits for incident analysis
# Reserve ~5K tokens for system prompt and response, leaving ~20K for input
MAX_INCIDENT_PROMPT_TOKENS = 20000  # Reduced from 25K to 20K (~$0.06 vs $0.075 with cheaper model)

# Limit logs based on token count, not just count
MAX_TOKENS_FOR_LOGS = 10000  # Reduced from 15K to 10K tokens for logs
MAX_TOKENS_PER_LOG = 120  # Reduced from 150 to 120 tokens per log (~420 chars)

# COST OPTIMIZATION: Reduced limits to save costs while maintaining quality
# Reserve ~10K tokens for system prompt and response, leaving ~60K for input
# For code files, limit each file to prevent overflow
MAX_TOKENS_FOR_FILES = 60000  # Reduced from 80K to 60K (~$0.18 vs $0.24)
MAX_TOKENS_PER_FILE = 8000  # Reduced from 10K to 8K per file (~28K chars)

# Final safety check: truncate entire prompt if it exceeds 80K tokens (reduced for cost)
MAX_TOTAL_PROMPT_TOKENS = 80000  # Reduced from 100K to 80K (~$0.24 vs $0.30)


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


def should_use_expensive_model(incident: Incident, logs: list[LogEntry], root_cause: Optional[str]) -> bool:
    """
    Determine if we should use expensive model (Claude 3.5 Sonnet) or cheaper alternatives.
    
    Args:
        incident: The incident
        logs: Related log entries
        root_cause: Root cause analysis (if available)
        
    Returns:
        True if expensive model should be used
    """
    try:
        # Use expensive model if:
        # Use expensive model if:
        # 1. Complex error patterns (stack traces, multiple errors)
        has_stack_trace = False
        error_count = 0
        for log in (logs or [])[:10]:
            if log and log.message:
                if "Traceback" in log.message or "at " in log.message or "File \"" in log.message:
                    has_stack_trace = True
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


def extract_paths_from_stacktrace(text: str) -> list[str]:
    """Extract file paths from a stack trace string."""
    paths = []
    
    # Python stack trace pattern: File "/path/to/file.py", line 123
    python_pattern = r'File "([^"]+)", line \d+'
    paths.extend(re.findall(python_pattern, text))
    
    # Node.js/JS stack trace pattern: at FunctionName (/path/to/file.js:123:45)
    # or at /path/to/file.js:123:45
    js_pattern = r'at (?:.*? \()?([^:)]+(?:\.js|\.ts|\.jsx|\.tsx)):\d+:\d+\)?'
    paths.extend(re.findall(js_pattern, text))
    
    return paths


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
        
    # Filter and Normalize
    valid_paths = []
    for p in paths:
        # Filter out node_modules and .next (build artifacts)
        if "/node_modules/" in p or "/.next/" in p:
            continue
            
        valid_paths.append(normalize_path(p))
        
    return valid_paths


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
        
        # Limit query results to prevent memory issues (max 10,000 logs)
        query = query.limit(10000)
        
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
        
        # Limit final result to prevent context overflow (max 500 logs)
        if len(trace_logs) > 500:
            print(f"⚠️  Trace has {len(trace_logs)} logs, limiting to 500 most recent")
            trace_logs = trace_logs[-500:]
        
        print(f"✅ Found {len(trace_logs)} total logs from trace(s) (including {len(logs)} original)")
        return trace_logs
        
    except Exception as e:
        print(f"⚠️  Error querying trace logs: {e}")
        import traceback
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


def extract_file_paths_from_trace(logs: list[LogEntry]) -> list[str]:
    """
    Extract file paths from all logs in a trace.
    This provides better context by looking at all spans, not just error spans.
    
    Args:
        logs: List of log entries from the same trace
        
    Returns:
        List of unique file paths found across all logs
    """
    if not logs:
        return []
    
    all_paths = []
    
    for log in logs:
        try:
            paths = extract_file_paths_from_log(log)
            if paths:
                all_paths.extend(paths)
        except Exception as e:
            # Skip logs that cause errors during path extraction
            print(f"⚠️  Error extracting paths from log {log.id if hasattr(log, 'id') else 'unknown'}: {e}")
            continue
    
    # Remove duplicates while preserving order
    seen = set()
    unique_paths = []
    for path in all_paths:
        if path and isinstance(path, str) and path not in seen and not path.startswith("http"):
            seen.add(path)
            unique_paths.append(path)
    
    # Limit to prevent context overflow (max 50 file paths)
    if len(unique_paths) > 50:
        print(f"⚠️  Found {len(unique_paths)} file paths, limiting to 50 most relevant")
        unique_paths = unique_paths[:50]
    
    print(f"📁 Extracted {len(unique_paths)} unique file path(s) from {len(logs)} trace log(s)")
    return unique_paths


def analyze_repository_and_create_pr(
    incident: Incident,
    logs: list[LogEntry],
    root_cause: str,
    action_taken: str,
    github_integration: GithubIntegration,
    repo_name: str,
    db: Session
) -> Dict[str, Any]:
    """
    Analyze repository code and create a PR with fixes.
    
    Args:
        incident: The incident
        logs: Related log entries
        root_cause: Root cause analysis
        action_taken: Recommended action
        github_integration: GitHub integration instance
        repo_name: Repository name in format "owner/repo"
        db: Database session
        
    Returns:
        Dict with PR information or error
    """
    api_key = os.getenv("OPENCOUNCIL_API")
    if not api_key:
        return {"status": "error", "message": "OpenRouter API not configured"}
    
    try:
        # Get repository info
        repo_info = github_integration.get_repo_info(repo_name)
        if repo_info.get("status") != "success":
            return {"status": "error", "message": f"Could not access repository: {repo_info.get('message')}"}
        
        default_branch = repo_info.get("default_branch", "main")
        
        # Extract relevant file paths from logs and incident
        error_messages = [log.message for log in logs if log.message]
        service_name = incident.service_name or ""
        
        # ENHANCEMENT: Get all logs from the same trace(s) for better file path extraction
        trace_logs = logs
        try:
            trace_logs = get_trace_logs(logs, db, user_id=incident.user_id)
            if len(trace_logs) > len(logs):
                print(f"✅ Using {len(trace_logs)} trace logs (vs {len(logs)} incident logs) for file path extraction")
        except Exception as e:
            print(f"⚠️  Error gathering trace logs for file extraction: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to original logs on error
            trace_logs = logs
        
        # ENHANCEMENT: Extract file paths from all logs in the trace, not just error logs
        file_paths_from_logs = extract_file_paths_from_trace(trace_logs)
        
        # Remove duplicates while preserving order (already done in extract_file_paths_from_trace, but keep for safety)
        seen = set()
        unique_file_paths = []
        for path in file_paths_from_logs:
            if path and path not in seen and not path.startswith("http"):
                seen.add(path)
                unique_file_paths.append(path)
        
        # Search for relevant files
        relevant_files = []
        
        # First, try to use file paths directly from log metadata
        if unique_file_paths:
            print(f"📁 Found {len(unique_file_paths)} file path(s) in log metadata: {unique_file_paths[:5]}")
            for file_path in unique_file_paths[:10]:  # Limit to 10 paths
                # Verify file exists in repo and add it
                content = github_integration.get_file_contents(repo_name, file_path, ref=default_branch)
                if content:
                    relevant_files.append({
                        "path": file_path,
                        "name": os.path.basename(file_path),
                        "url": f"https://github.com/{repo_name}/blob/{default_branch}/{file_path}",
                        "repository": repo_name,
                        "content": content  # Store content to avoid re-fetching
                    })
                else:
                    # Try to find similar files if exact path doesn't exist
                    # This handles cases where the path might be from a different repo structure
                    print(f"⚠️  File path from log not found in repo: {file_path}")
        
        # Fallback: Use keyword search if no file paths found in metadata
        if not relevant_files:
            print("🔍 No file paths in metadata, falling back to keyword search")
            search_queries = []
            
            # Build search queries from error messages and service name
            if service_name:
                search_queries.append(service_name)
            if error_messages:
                # Extract keywords from error messages
                for msg in error_messages[:3]:  # Limit to first 3 messages
                    # Extract potential file names or function names
                    words = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', msg)
                    search_queries.extend(words[:5])  # Limit keywords
            
            # Determine language based on service name
            language = "python"  # Default
            if service_name:
                lower_name = service_name.lower()
                if "next" in lower_name or "react" in lower_name or "node" in lower_name or "ts" in lower_name:
                    language = "typescript"
                elif "go" in lower_name:
                    language = "go"
                elif "java" in lower_name:
                    language = "java"
            
            # Search for files related to the service
            for query in set(search_queries[:3]):  # Limit queries
                matches = github_integration.search_code(repo_name, query, language=language)
                relevant_files.extend(matches)
            
            # Also get main service files if service_name is available
            if service_name:
                # Try common file patterns based on language
                common_patterns = []
                if language == "python":
                    common_patterns = [f"{service_name}.py", "main.py", "app.py"]
                elif language == "typescript":
                    common_patterns = ["package.json", "tsconfig.json", "app/page.tsx", "pages/index.tsx"]
                
                repo_files = github_integration.get_repo_structure(repo_name, ref=default_branch, max_depth=3)
                for pattern in common_patterns:
                    matching_files = [f for f in repo_files if pattern.lower() in f.lower()]
                    for file_path in matching_files[:2]:  # Limit matches
                        if file_path not in [f["path"] for f in relevant_files]:
                            relevant_files.append({
                                "path": file_path,
                                "name": os.path.basename(file_path),
                                "url": f"https://github.com/{repo_name}/blob/{default_branch}/{file_path}",
                                "repository": repo_name
                            })
        
        # Limit to top 5 most relevant files
        relevant_files = relevant_files[:5]
        
        # Fetch file contents (use cached content if available, otherwise fetch)
        file_contents = {}
        for file_info in relevant_files:
            file_path = file_info["path"]
            # Use cached content if available (from log metadata paths), otherwise fetch
            content = file_info.get("content")
            if not content:
                content = github_integration.get_file_contents(repo_name, file_path, ref=default_branch)
            if content:
                file_contents[file_path] = content
        
        # Prepare context for AI to analyze and generate fixes
        # COST OPTIMIZATION: Reduced limits to save costs while maintaining quality
        # Using module-level constants for limits
        
        files_context_parts = []
        total_file_tokens = 0
        
        for path, content in file_contents.items():
            if not content:
                continue
            
            # Estimate tokens for this file
            file_tokens = estimate_tokens(content)
            
            # If single file exceeds limit, truncate it
            if file_tokens > MAX_TOKENS_PER_FILE:
                print(f"⚠️  File {path} is large ({file_tokens} tokens), truncating to {MAX_TOKENS_PER_FILE} tokens")
                content = truncate_to_token_limit(content, MAX_TOKENS_PER_FILE, "\n... [file truncated]")
                file_tokens = estimate_tokens(content)
            
            # Check if adding this file would exceed total limit
            if total_file_tokens + file_tokens > MAX_TOKENS_FOR_FILES:
                print(f"⚠️  Reached file content token limit ({total_file_tokens} tokens), skipping remaining files")
                break
            
            files_context_parts.append(f"File: {path}\n```\n{content}\n```")
            total_file_tokens += file_tokens
        
        files_context = "\n\n".join(files_context_parts)
        
        if not files_context:
            # If no files found, but we have a strong signal from logs, we might still want to proceed
            # especially if it's a "missing file" error.
            print("⚠️  No relevant code files found. Proceeding with empty context to allow file creation.")
            files_context = "No existing code files found. The error might be due to a missing file."

        # MEMORY: Retrieve context to help with coding
        memory_context_str = ""
        try:
            code_memory = CodeMemory()
            fingerprint = get_incident_fingerprint(incident, logs)
            memory_data = code_memory.retrieve_context(fingerprint)
            known_fixes = memory_data.get("known_fixes", [])

            if known_fixes:
                 memory_context_str = "\nPREVIOUS SUCCESSFUL FIXES FOR THIS ERROR:\n"
                 for i, fix in enumerate(known_fixes[:2]):
                     memory_context_str += f"Fix #{i+1} Description: {fix.get('description')}\n"
        except Exception as e:
            print(f"⚠️ Error retrieving memory for coding: {e}")
        
        # Build base prompt (estimate ~500 tokens)
        base_prompt = f"""You are an expert software engineer analyzing an incident and generating code fixes.

Incident Details:
- Title: {incident.title}
- Service: {incident.service_name}
- Root Cause: {root_cause}
- Recommended Action: {action_taken}

{memory_context_str}

Related Code Files:
{files_context}

Based on the root cause analysis and the code above, please:
1. Identify the specific code issues causing this incident
2. Generate fixed versions of the affected files
3. Provide a clear explanation of the changes

IMPORTANT: Respond with ONLY valid JSON. Do not use backticks or template literals inside JSON strings. Escape all special characters properly.

Respond in this exact JSON format:
{{
    "analysis": "Brief analysis of the code issues",
    "changes": {{
        "file_path_1": "complete fixed file content (use \\n for newlines, escape quotes)",
        "file_path_2": "complete fixed file content"
    }},
    "explanation": "Explanation of what was fixed and why"
}}

Only include files that need changes. Provide the COMPLETE file content for each changed file, not just diffs.
"""
        
        # Check total prompt size and truncate if needed
        prompt_tokens = estimate_tokens(base_prompt)
        print(f"📊 Code analysis prompt: ~{prompt_tokens} tokens (files: ~{total_file_tokens} tokens)")
        
        # Final safety check: truncate entire prompt if it exceeds limit
        if prompt_tokens > MAX_TOTAL_PROMPT_TOKENS:
            print(f"⚠️  Prompt exceeds limit ({prompt_tokens} tokens), truncating to {MAX_TOTAL_PROMPT_TOKENS} tokens")
            # Truncate files_context to fit
            base_without_files = base_prompt.replace(files_context, "")
            available_for_files = MAX_TOTAL_PROMPT_TOKENS - estimate_tokens(base_without_files)
            if available_for_files > 0:
                files_context = truncate_to_token_limit(files_context, available_for_files)
                code_analysis_prompt = base_without_files.replace("Related Code Files:\n", f"Related Code Files:\n{files_context}\n")
            else:
                code_analysis_prompt = base_without_files.replace("Related Code Files:\n", "Related Code Files:\n[Files context too large, truncated]\n")
        else:
            code_analysis_prompt = base_prompt
        
        import requests
        
        # Call OpenRouter API for code analysis
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", "https://healops.ai"),
                "X-Title": "HealOps Code Fix Generation",
            },
            json={
                "model": MODEL_CONFIG["code_generation"]["model"],  # Claude 3.5 Sonnet for code
                "messages": [
                    {
                        "role": "user",
                        "content": code_analysis_prompt
                    }
                ],
                "temperature": MODEL_CONFIG["code_generation"]["temperature"],
                "max_tokens": MODEL_CONFIG["code_generation"]["max_tokens"],
            },
            timeout=60
        )
        
        if response.status_code != 200:
            error_text = response.text[:300] if response.text else "Unknown error"
            return {
                "status": "error",
                "message": f"Code analysis failed: {error_text}"
            }
        
        try:
            result = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            print(f"⚠️  Failed to parse API response as JSON: {e}")
            return {
                "status": "error",
                "message": "Code analysis failed: Invalid response from AI service."
            }
        
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # COST TRACKING: Log token usage for code generation
        usage = result.get("usage", {}) or {}
        input_tokens = usage.get("prompt_tokens", 0) or 0
        output_tokens = usage.get("completion_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        
        try:
            estimated_cost = (input_tokens * 3.00 / 1_000_000) + (output_tokens * 15.00 / 1_000_000)
            print(f"💰 Code generation cost: ~${estimated_cost:.4f} | Tokens: {input_tokens} in + {output_tokens} out = {total_tokens} total")
        except Exception as cost_error:
            print(f"⚠️  Error calculating cost: {cost_error}")
            estimated_cost = 0.0
        
        # Parse JSON response with multiple strategies
        content = content.strip()
        
        # Remove markdown code blocks
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        # Try to extract JSON if it's embedded in text
        if not content.startswith("{"):
            # Look for the first { and last }
            start_idx = content.find("{")
            end_idx = content.rfind("}")
            if start_idx != -1 and end_idx != -1:
                content = content[start_idx:end_idx+1]
        
        # Preprocess: Convert backtick-delimited strings to proper JSON
        # This handles cases where AI uses `...` instead of "..." for multiline strings
        def fix_backtick_strings(text):
            """Replace backtick strings with properly escaped JSON strings."""
            import re
            # Find patterns like: "key": `value with newlines`
            pattern = r':\s*`([^`]*)`'
            
            def escape_content(match):
                content = match.group(1)
                # Escape backslashes first
                content = content.replace('\\', '\\\\')
                # Escape quotes
                content = content.replace('"', '\\"')
                # Convert newlines to \n
                content = content.replace('\n', '\\n')
                content = content.replace('\r', '\\r')
                content = content.replace('\t', '\\t')
                return f': "{content}"'
            
            return re.sub(pattern, escape_content, text, flags=re.DOTALL)
        
        content = fix_backtick_strings(content)
        
        try:
            code_analysis = json.loads(content)
            changes = code_analysis.get("changes", {})
            explanation = code_analysis.get("explanation", "Code fixes applied")
            
            print(f"📝 AI returned {len(changes)} file change(s): {list(changes.keys())}")
            
            if not changes:
                return {
                    "status": "skipped",
                    "message": "AI determined no code changes are needed"
                }
            
            # Create PR with the fixes
            branch_name = f"healops-fix-incident-{incident.id}"
            pr_title = f"Fix: {incident.title}"
            pr_body = f"""## Incident Fix

**Incident ID:** #{incident.id}
**Service:** {incident.service_name}
**Severity:** {incident.severity}

### Root Cause
{root_cause}

### Recommended Action
{action_taken}

### Changes Made
{explanation}

### Files Changed
{chr(10).join(f"- `{path}`" for path in changes.keys())}

---
*This PR was automatically generated by HealOps AI Analysis*
"""
            
            pr_result = github_integration.create_pr(
                repo_name=repo_name,
                title=pr_title,
                body=pr_body,
                head_branch=branch_name,
                base_branch=default_branch,
                changes=changes
            )
            
            if pr_result.get("status") == "success":
                pr_url = pr_result.get("pr_url")
                pr_number = pr_result.get("pr_number")
                
                # MEMORY: Store the successful fix to learn from it
                try:
                    print(f"🧠 Storing fix in CodeMemory for future learning...")
                    code_memory = CodeMemory()
                    fingerprint = get_incident_fingerprint(incident, logs)
                    # Store the files changed and the explanation
                    fix_data = json.dumps({
                        "changes": changes,
                        "pr_url": pr_url
                    })
                    code_memory.store_fix(fingerprint, explanation, fix_data)
                    print(f"✅ Fix stored in memory for fingerprint: {fingerprint}")
                except Exception as mem_err:
                    print(f"⚠️ Failed to store fix in memory: {mem_err}")

                # Send email notification
                try:
                    from email_service import send_pr_creation_email
                    from models import User
                    
                    # Get user email from incident
                    user_email = None
                    if incident.user_id:
                        user = db.query(User).filter(User.id == incident.user_id).first()
                        if user and user.email:
                            user_email = user.email
                    
                    if user_email:
                        # Prepare incident data for email
                        incident_data = {
                            "id": incident.id,
                            "title": incident.title,
                            "service_name": incident.service_name,
                            "severity": incident.severity,
                            "status": incident.status,
                            "user_id": incident.user_id,
                            "created_at": incident.created_at.isoformat() if incident.created_at else None,
                            "root_cause": root_cause,
                            "action_taken": action_taken,
                            "pr_files_changed": list(changes.keys())
                        }
                        
                        # Send email in background (don't block PR creation)
                        send_pr_creation_email(
                            recipient_email=user_email,
                            incident=incident_data,
                            pr_url=pr_url,
                            pr_number=pr_number,
                            db_session=db
                        )
                    else:
                        print(f"⚠️  No user email found for incident {incident.id}, skipping email notification")
                except Exception as e:
                    print(f"⚠️  Failed to send email notification: {e}")
                    # Don't fail PR creation if email fails
                    import traceback
                    traceback.print_exc()
                
                # Extract original content for the changed files
                original_contents = {}
                for file_path in changes.keys():
                    # Try exact match first
                    if file_path in file_contents:
                        original_contents[file_path] = file_contents[file_path]
                    else:
                        # Try normalized path match
                        found_normalized = False
                        norm_path = normalize_path(file_path)
                        for existing_path, content in file_contents.items():
                            if normalize_path(existing_path) == norm_path:
                                original_contents[file_path] = content
                                found_normalized = True
                                break

                        if not found_normalized:
                            # If for some reason we don't have it in file_contents (e.g. new file created entirely by AI not in relevant_files)
                            # Try to fetch it (will return None if it's a new file)
                            try:
                                content = github_integration.get_file_contents(repo_name, file_path, ref=default_branch)
                                if content:
                                    original_contents[file_path] = content
                                else:
                                    # Mark as empty if truly new file or not found, so UI doesn't say "not available"
                                    # but instead treats it as empty original
                                    original_contents[file_path] = ""
                            except Exception as e:
                                print(f"⚠️ Failed to fetch original content for {file_path}: {e}")
                                original_contents[file_path] = ""

                return {
                    "status": "success",
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "files_changed": list(changes.keys()),
                    "changes": changes, # Return full content for frontend diff viewer
                    "original_contents": original_contents, # Return original content for diff
                    "explanation": explanation
                }
            else:
                return {
                    "status": "error",
                    "message": f"Failed to create PR: {pr_result.get('message')}"
                }
                
        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to parse code analysis JSON: {content[:500]}")
            return {
                "status": "error",
                "message": f"Failed to parse AI response: {str(e)}"
            }
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Error in repository analysis: {e}")
        print(f"Full traceback: {error_trace}")
        return {
            "status": "error",
            "message": str(e)
        }


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
    api_key = os.getenv("OPENCOUNCIL_API")
    if not api_key:
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
        import traceback
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
            import traceback
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
        import requests
        
        # COST OPTIMIZATION: Use cheaper model for initial analysis
        # Only use expensive model if code generation is needed
        use_expensive = should_use_expensive_model(incident, logs, None)
        
        if use_expensive:
            model_config = MODEL_CONFIG["complex_analysis"]  # Claude 3 Haiku
            print("💰 Using Claude 3 Haiku for complex analysis")
        else:
            model_config = MODEL_CONFIG["simple_analysis"]  # Gemini Flash
            print("💰 Using Gemini Flash for simple analysis (cost-optimized)")
        
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
                "model": model_config["model"],
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": model_config["temperature"],
                "max_tokens": model_config["max_tokens"],
            },
            timeout=30
        )
        
        if response.status_code == 402:
            # Insufficient credits error - provide helpful message
            error_data = response.json() if response.text else {}
            error_msg = error_data.get("error", {}).get("message", "Insufficient credits")
            print(f"❌ OpenRouter API: Insufficient credits - {error_msg}")
            return {
                "root_cause": "AI analysis is currently unavailable due to insufficient API credits. Please add credits at https://openrouter.ai/settings/credits or contact your administrator.",
                "action_taken": None
            }
        elif response.status_code != 200:
            error_text = response.text[:300] if response.text else "Unknown error"
            try:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", error_text)
            except:
                error_msg = error_text
            
            print(f"❌ OpenRouter API error (status {response.status_code}): {error_msg}")
            # Return error message so UI stops loading
            return {
                "root_cause": f"Analysis failed: {error_msg}. Please check OpenRouter API configuration or try again later.",
                "action_taken": None
            }
        
        try:
            result = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            print(f"⚠️  Failed to parse API response as JSON: {e}")
            return {
                "root_cause": "Analysis failed: Invalid response from AI service. Please try again later.",
                "action_taken": None
            }
        
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # COST TRACKING: Log token usage for monitoring
        usage = result.get("usage", {}) or {}
        input_tokens = usage.get("prompt_tokens", 0) or 0
        output_tokens = usage.get("completion_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        
        # Estimate cost (approximate) - safely handle model_config
        try:
            model_name = model_config.get("model", "unknown") if isinstance(model_config, dict) else "unknown"
            if "mimo-v2-flash" in model_name and "free" in model_name:
                estimated_cost = 0.0  # Free model
            elif "grok-code-fast" in model_name:
                # Grok Code Fast 1: $0.20/M Input, $1.50/M Output
                estimated_cost = (input_tokens * 0.20 / 1_000_000) + (output_tokens * 1.50 / 1_000_000)
            elif "gemini-flash-1.5-8b" in model_name:
                estimated_cost = (input_tokens * 0.0375 / 1_000_000) + (output_tokens * 0.15 / 1_000_000)
            elif model_name == "google/gemini-flash-1.5":
                estimated_cost = (input_tokens * 0.075 / 1_000_000) + (output_tokens * 0.30 / 1_000_000)
            elif "deepseek" in model_name:
                # Pricing based on OpenRouter screenshot for DeepSeek V3
                estimated_cost = (input_tokens * 0.30 / 1_000_000) + (output_tokens * 1.20 / 1_000_000)
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
            
            result = {
                "root_cause": root_cause,
                "action_taken": action_taken,
                "cost_estimate": estimated_cost,
                "tokens_used": total_tokens,
                "model_used": model_config.get("model", "unknown") if isinstance(model_config, dict) else "unknown"
            }
            
            # COST OPTIMIZATION: Only create PR if action_taken suggests code changes
            # Skip expensive code generation for simple fixes like "restart service"
            should_generate_code = False

            # 1. Check action_taken for keywords
            if action_taken:
                code_action_keywords = ["fix", "update", "change", "modify", "add", "remove", "patch", "bug", "code", "revert", "rollback", "optimize", "refactor", "correct", "resolve", "handle", "implement"]
                should_generate_code = any(keyword in action_taken.lower() for keyword in code_action_keywords)
            
            # 2. Also check root_cause if action didn't trigger it, as the root cause might clearly indicate a code issue
            # even if the action is generic like "investigate"
            if not should_generate_code and root_cause:
                root_cause_keywords = ["exception", "error", "traceback", "stack", "undefined", "null", "crash", "fail", "broken", "bug", "syntax", "implementation", "deployment"]
                should_generate_code = any(keyword in root_cause.lower() for keyword in root_cause_keywords)

            # If GitHub integration is available and code changes are needed, try to analyze repo and create PR
            if incident.integration_id and should_generate_code:
                try:
                    integration = db.query(Integration).filter(Integration.id == incident.integration_id).first()
                    if integration and integration.provider == "GITHUB":
                        # Use repo_name from incident if available (assigned during creation),
                        # otherwise fall back to looking it up from integration config
                        repo_name = None
                        if hasattr(incident, 'repo_name') and incident.repo_name:
                            repo_name = incident.repo_name
                            print(f"📌 Using repo_name from incident: {repo_name}")
                        else:
                            # Fallback: Get repo name using service-to-repo mapping
                            repo_name = get_repo_name_from_integration(integration, service_name=incident.service_name)
                            if repo_name:
                                print(f"🔍 Found repo_name from integration config: {repo_name}")
                        
                        if repo_name:
                            print(f"🔍 Analyzing repository {repo_name} for incident {incident.id} (service: {incident.service_name})")
                            github_integration = GithubIntegration(integration_id=integration.id)
                            pr_result = analyze_repository_and_create_pr(
                                incident=incident,
                                logs=logs,
                                root_cause=root_cause,
                                action_taken=action_taken or "",
                                github_integration=github_integration,
                                repo_name=repo_name,
                                db=db
                            )
                            
                            if pr_result.get("status") == "success":
                                result["pr_url"] = pr_result.get("pr_url")
                                result["pr_number"] = pr_result.get("pr_number")
                                result["pr_files_changed"] = pr_result.get("files_changed", [])
                                result["changes"] = pr_result.get("changes", {})
                                result["original_contents"] = pr_result.get("original_contents", {})
                                print(f"✅ Created PR #{pr_result.get('pr_number')} at {pr_result.get('pr_url')}")
                            elif pr_result.get("status") == "error":
                                print(f"⚠️  PR creation failed: {pr_result.get('message')}")
                                result["pr_error"] = pr_result.get("message")
                        else:
                            print(f"⚠️  No repository name found for incident {incident.id} (integration {integration.id})")
                except Exception as e:
                    import traceback
                    print(f"⚠️  Error during GitHub analysis: {e}")
                    print(traceback.format_exc())
                    # Don't fail the whole analysis if PR creation fails
            
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
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Error calling OpenRouter API: {e}")
        print(f"Full traceback: {error_trace}")
        # Return a fallback message instead of None so the UI stops loading
        return {
            "root_cause": f"Analysis failed: {str(e)[:200]}. Please check OPENCOUNCIL_API configuration.",
            "action_taken": None
        }
