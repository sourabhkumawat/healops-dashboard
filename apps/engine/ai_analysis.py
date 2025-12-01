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


def get_repo_name_from_integration(integration: Integration) -> Optional[str]:
    """
    Extract repository name from integration config or project_id.
    
    Args:
        integration: Integration model instance
        
    Returns:
        Repository name in format "owner/repo" or None
    """
    # Check config first
    if integration.config and isinstance(integration.config, dict):
        repo_name = integration.config.get("repo_name") or integration.config.get("repository")
        if repo_name:
            return repo_name
    
    # Check project_id as fallback
    if integration.project_id:
        # project_id might be in format "owner/repo" or just "repo"
        return integration.project_id
    
    return None


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
        
        # Search for relevant files
        relevant_files = []
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
        
        # Search for Python files related to the service
        for query in set(search_queries[:3]):  # Limit queries
            matches = github_integration.search_code(repo_name, query, language="python")
            relevant_files.extend(matches)
        
        # Also get main service files if service_name is available
        if service_name:
            # Try common file patterns
            common_patterns = [
                f"{service_name}.py",
                f"main.py",
                f"app.py",
                f"service.py",
                f"handler.py"
            ]
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
        
        # Fetch file contents
        file_contents = {}
        for file_info in relevant_files:
            file_path = file_info["path"]
            content = github_integration.get_file_contents(repo_name, file_path, ref=default_branch)
            if content:
                file_contents[file_path] = content
        
        # Prepare context for AI to analyze and generate fixes
        files_context = "\n\n".join([
            f"File: {path}\n```\n{content[:2000]}\n```"  # Limit content per file
            for path, content in file_contents.items()
        ])
        
        if not files_context:
            return {
                "status": "skipped",
                "message": "No relevant code files found to analyze"
            }
        
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

Respond in JSON format:
{{
    "analysis": "Brief analysis of the code issues",
    "changes": {{
        "file_path_1": "complete fixed file content",
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
        
        # Parse JSON response
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        try:
            code_analysis = json.loads(content)
            changes = code_analysis.get("changes", {})
            explanation = code_analysis.get("explanation", "Code fixes applied")
            
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
                return {
                    "status": "success",
                    "pr_url": pr_result.get("pr_url"),
                    "pr_number": pr_result.get("pr_number"),
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
                        repo_name = get_repo_name_from_integration(integration)
                        if repo_name:
                            print(f"🔍 Analyzing repository {repo_name} for incident {incident.id}")
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
                            print(f"⚠️  No repository name found in integration {integration.id}")
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
