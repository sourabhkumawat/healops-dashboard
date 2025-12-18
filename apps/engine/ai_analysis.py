"""
AI Analysis module for incident root cause analysis using OpenRouter.
"""
import os
import json
import re
from typing import Dict, Any, Optional
from models import Incident, LogEntry, Integration
from sqlalchemy.orm import Session
from integrations.github_integration import GithubIntegration


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
        
        # Extract file paths from log metadata (filePath or file_path)
        file_paths_from_logs = []
        for log in logs:
            extracted_paths = extract_file_paths_from_log(log)
            for path in extracted_paths:
                # Skip if path looks like a URL or is empty
                if path and not path.startswith("http"):
                    file_paths_from_logs.append(path)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_file_paths = []
        for path in file_paths_from_logs:
            if path not in seen:
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
        files_context = "\n\n".join([
            f"File: {path}\n```\n{content[:2000]}\n```"  # Limit content per file
            for path, content in file_contents.items()
        ])
        
        if not files_context:
            # If no files found, but we have a strong signal from logs, we might still want to proceed
            # especially if it's a "missing file" error.
            print("⚠️  No relevant code files found. Proceeding with empty context to allow file creation.")
            files_context = "No existing code files found. The error might be due to a missing file."
        
        # Create enhanced prompt for code analysis and fix generation
        code_analysis_prompt = f"""You are an expert software engineer analyzing an incident and generating code fixes.

Incident Details:
- Title: {incident.title}
- Service: {incident.service_name}
- Root Cause: {root_cause}
- Recommended Action: {action_taken}

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
                "model": "anthropic/claude-3.5-sonnet",
                "messages": [
                    {
                        "role": "user",
                        "content": code_analysis_prompt
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 8000,  # Higher limit for code generation
            },
            timeout=60
        )
        
        if response.status_code != 200:
            error_text = response.text[:300] if response.text else "Unknown error"
            return {
                "status": "error",
                "message": f"Code analysis failed: {error_text}"
            }
        
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        
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
                            pr_number=pr_number
                        )
                    else:
                        print(f"⚠️  No user email found for incident {incident.id}, skipping email notification")
                except Exception as e:
                    print(f"⚠️  Failed to send email notification: {e}")
                    # Don't fail PR creation if email fails
                    import traceback
                    traceback.print_exc()
                
                return {
                    "status": "success",
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "files_changed": list(changes.keys()),
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
    
    # Prepare context from incident and logs
    log_context = "\n".join([
        f"[{log.timestamp or 'N/A'}] {log.level or 'UNKNOWN'}: {log.message or 'No message'}" 
        for log in (logs or [])[:20]  # Limit to last 20 logs
    ])
    
    if not log_context:
        log_context = "No related logs available."
    
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
                # Note: For lower costs, consider using cheaper models like:
                # "google/gemini-flash-1.5" or "anthropic/claude-3-haiku"
                "model": "anthropic/claude-3.5-sonnet",  # You can change this to any model
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3,  # Lower temperature for more deterministic analysis
                "max_tokens": 500,  # Limit tokens to reduce cost - sufficient for root cause analysis
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
            
            result = {
                "root_cause": root_cause,
                "action_taken": action_taken
            }
            
            # If GitHub integration is available, try to analyze repo and create PR
            if incident.integration_id:
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
