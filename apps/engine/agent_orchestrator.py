"""
Agent Orchestrator - Main orchestrator for the Healops AI agent system.
Implements Manus-style architecture with iterative agent loop, explicit planning, and event streaming.

This is the main entry point for running the agent system to resolve incidents.
"""
# Initialize Langtrace before imports
try:
    from langtrace_python_sdk import langtrace
    import os
    
    langtrace_api_key = os.getenv("LANGTRACE_API_KEY")
    if langtrace_api_key:
        try:
            langtrace.init(api_key=langtrace_api_key)
        except Exception:
            pass
except ImportError:
    pass

from typing import Dict, Any, List, Optional, Callable
import os
import json
import re
import subprocess
import tempfile
import signal
import resource
import time
from contextlib import contextmanager
from datetime import datetime

from event_stream import EventStream, EventType
from task_planner import TaskPlanner
from agent_workspace import Workspace
from agent_scratchpad import Scratchpad
from context_manager import ContextManager
from execution_loop import AgentLoop
from code_execution_tools import set_agent_tools_context, get_context
from knowledge_retriever import KnowledgeRetriever
from system_prompt import build_system_prompt
from memory import CodeMemory
from integrations.github_integration import GithubIntegration
from models import Incident, LogEntry
from sqlalchemy.orm import Session
from ai_analysis import get_incident_fingerprint
from agent_definitions import create_all_enhanced_agents
from coding_tools import set_coding_tools_context, CodingToolsContext
from memory_models import AgentWorkspace

# LLM Configuration
api_key = os.getenv("OPENCOUNCIL_API")
base_url = "https://openrouter.ai/api/v1"

try:
    from crewai import LLM
    # Initialize LLMs with OpenRouter
    # Note: CrewAI will automatically use LiteLLM if available for custom base_url
    if not api_key:
        print("⚠️  Warning: OPENCOUNCIL_API not set. LLMs will not be initialized.")
        flash_llm = None
        coding_llm = None
    else:
        try:
            flash_llm = LLM(
                model="openai/xiaomi/mimo-v2-flash:free",
                base_url=base_url,
                api_key=api_key
            )
            coding_llm = LLM(
                model="openai/x-ai/grok-code-fast-1",
                base_url=base_url,
                api_key=api_key
            )
            print("✅ LLMs initialized successfully")
        except ImportError as import_err:
            if "LiteLLM" in str(import_err) or "litellm" in str(import_err).lower():
                print(f"❌ Error: LiteLLM is required but not available. Please install it: pip install litellm")
                print(f"   Error details: {import_err}")
                flash_llm = None
                coding_llm = None
            else:
                raise
except Exception as e:
    print(f"⚠️  Warning: Failed to initialize LLMs: {e}")
    import traceback
    traceback.print_exc()
    flash_llm = None
    coding_llm = None


def run_robust_crew(
    incident: Incident,
    logs: List[LogEntry],
    root_cause: str,
    github_integration: GithubIntegration,
    repo_name: str,
    db: Session
) -> Dict[str, Any]:
    """
    Run the robust crew with Manus-style architecture.
    
    Args:
        incident: The incident to fix
        logs: Related log entries
        root_cause: Root cause analysis
        github_integration: GitHub integration instance
        repo_name: Repository name in format "owner/repo"
        db: Database session
        
    Returns:
        Result dictionary with fixes, events, and execution details
    """
    # Initialize all components
    event_stream = EventStream(incident.id)
    
    # Set up WebSocket broadcasting (lazy import to avoid circular dependency)
    def broadcast_callback(incident_id: int, event: Dict[str, Any]):
        """Broadcast event to WebSocket clients."""
        try:
            from main import agent_event_manager
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, schedule the coroutine
                    asyncio.create_task(agent_event_manager.broadcast(incident_id, event))
                else:
                    # If no loop, run in new thread
                    loop.run_until_complete(agent_event_manager.broadcast(incident_id, event))
            except Exception as e:
                print(f"Warning: Failed to broadcast event: {e}")
        except ImportError:
            # WebSocket manager not available (e.g., during testing)
            pass
        except Exception as e:
            print(f"Warning: WebSocket broadcasting not available: {e}")
    
    event_stream.set_websocket_broadcast(broadcast_callback)
    
    planner = TaskPlanner(incident.id, github_integration, repo_name)
    workspace = Workspace(incident.id)
    scratchpad = Scratchpad(incident.id, github_integration, repo_name)
    context_manager = ContextManager()
    
    # Initialize knowledge retriever
    knowledge_retriever = KnowledgeRetriever(github_integration, repo_name)
    
    # Set up coding tools context
    tools_context = CodingToolsContext(github_integration, repo_name)
    set_coding_tools_context(tools_context)
    
    # Set up agent tools context
    code_memory = CodeMemory()
    set_agent_tools_context({
        "github_integration": github_integration,
        "repo_name": repo_name,
        "workspace": workspace,
        "ref": "main",
        "memory": code_memory
    })
    
    # Extract file paths from logs
    affected_files = _extract_file_paths_from_logs(logs)
    
    # Get repository structure to provide available files to the agent
    available_files = []
    try:
        if github_integration:
            print(f"🔍 Fetching repository structure for: {repo_name}")
            # Check if client is initialized
            if not hasattr(github_integration, 'client') or github_integration.client is None:
                print("⚠️  Warning: GitHub client not initialized. Attempting to ensure client...")
                github_integration._ensure_client()
            
            if github_integration.client:
                # Try to get default branch first
                repo_info = github_integration.get_repo_info(repo_name)
                default_branch = repo_info.get("default_branch", "main") if repo_info.get("status") == "success" else "main"
                print(f"📂 Using branch: {default_branch}")
                
                available_files = github_integration.get_repo_structure(
                    repo_name, 
                    path="", 
                    ref=default_branch, 
                    max_depth=3  # Get up to 3 levels deep
                )
                print(f"📁 Found {len(available_files)} files in repository")
                if len(available_files) > 0:
                    print(f"   Sample files: {available_files[:5]}")
                    # Detect and show languages
                    languages = _detect_languages(available_files)
                    if languages:
                        print(f"   Languages detected: {', '.join(sorted(languages.keys()))}")
                        for lang, files in sorted(languages.items(), key=lambda x: -len(x[1]))[:5]:
                            print(f"      - {lang}: {len(files)} files")
                else:
                    print(f"⚠️  No files found. This might indicate:")
                    print(f"   - Repository is empty")
                    print(f"   - Access permissions issue")
                    print(f"   - Branch '{default_branch}' doesn't exist")
            else:
                print("❌ Error: GitHub client is not available. Cannot fetch repository structure.")
                print(f"   Integration ID: {incident.integration_id if incident else 'N/A'}")
        else:
            print("⚠️  Warning: No GitHub integration provided. Cannot fetch repository structure.")
    except Exception as e:
        print(f"❌ Error: Failed to get repository structure: {e}")
        import traceback
        traceback.print_exc()
    
    # Get error signature for memory
    error_signature = get_incident_fingerprint(incident, logs)
    
    # Retrieve memory
    memory_data = code_memory.retrieve_context(error_signature)
    
    # Retrieve learning pattern for this error type
    learning_pattern = None
    try:
        error_type = code_memory._extract_error_type(error_signature, root_cause)
        learning_pattern = code_memory.get_learning_pattern(error_type)
        if learning_pattern:
            print(f"🧠 Found learning pattern for {error_type} (confidence: {learning_pattern.get('confidence_score', 0)}%)")
            # Enhance affected_files with learned patterns
            learned_files = learning_pattern.get("typical_files_read", [])
            for file_path in learned_files[:5]:  # Add top 5 learned files
                if file_path not in affected_files:
                    affected_files.append(file_path)
    except Exception as e:
        print(f"Warning: Failed to retrieve learning pattern: {e}")
    
    # Index knowledge base
    try:
        # Index codebase patterns (limited to affected files + common files)
        files_to_index = affected_files[:20]  # Limit indexing
        knowledge_retriever.index_codebase_patterns(files_to_index)
        
        # Index past fixes
        if memory_data.get("known_fixes"):
            knowledge_retriever.index_past_fixes(memory_data["known_fixes"])
    except Exception as e:
        print(f"Warning: Knowledge indexing failed: {e}")
    
    # Retrieve knowledge for planning
    knowledge_context = None
    try:
        knowledge = knowledge_retriever.retrieve_for_planning(root_cause, affected_files)
        if knowledge:
            knowledge_context = "\n".join([k["content"][:200] for k in knowledge[:3]])
            # Inject knowledge events
            for item in knowledge:
                event_stream.add_event(
                    EventType.KNOWLEDGE_RETRIEVED,
                    {
                        "content": item["content"][:300],
                        "relevance": item["relevance_score"],
                        "source": item.get("source", "unknown")
                    }
                )
    except Exception as e:
        print(f"Warning: Knowledge retrieval failed: {e}")
    
    # Enhance knowledge context with learning pattern
    if learning_pattern and knowledge_context:
        pattern_info = f"Learning pattern suggests: Read {len(learning_pattern.get('typical_files_read', []))} files, Modify {len(learning_pattern.get('typical_files_modified', []))} files"
        knowledge_context = pattern_info + "\n" + knowledge_context
    
    # Create plan
    try:
        if coding_llm is None:
            raise ValueError("coding_llm is not available. Cannot create plan. Please check OPENCOUNCIL_API environment variable.")
        
        # Add available files to knowledge context for planning
        files_context = ""
        if available_files:
            # Detect languages
            languages = _detect_languages(available_files)
            
            # Show top 50 files to avoid overwhelming the context
            files_to_show = available_files[:50]
            files_context = f"\n\nAvailable files in repository ({len(available_files)} total, showing first {len(files_to_show)}):\n" + "\n".join(f"- {f}" for f in files_to_show)
            if len(available_files) > 50:
                files_context += f"\n... and {len(available_files) - 50} more files. Use list_files() function to explore the repository structure."
            
            # Add language information
            if languages:
                files_context += "\n\nRepository Languages Detected:\n"
                for lang, files in sorted(languages.items(), key=lambda x: -len(x[1])):
                    files_context += f"  - {lang}: {len(files)} files\n"
                
                # Add specific guidance for TypeScript
                if "TypeScript" in languages:
                    files_context += "\n⚠️ IMPORTANT: This repository uses TypeScript (.ts/.tsx files).\n"
                    files_context += "When creating a plan, ensure fixes are written in TypeScript with proper types.\n"
                    files_context += "Do NOT write JavaScript code for TypeScript files.\n"
        
        enhanced_knowledge_context = (knowledge_context or "") + files_context
        
        plan = planner.create_plan(root_cause, affected_files, coding_llm, enhanced_knowledge_context)
        workspace.set_plan(plan)
        scratchpad.initialize(plan)
        
        event_stream.add_event(
            EventType.PLAN_CREATED,
            {
                "plan": plan,
                "steps_count": len(plan)
            }
        )
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error creating plan: {e}")
        print(f"Full traceback:\n{error_trace}")
        return {
            "status": "error",
            "error": f"Failed to create plan: {str(e)}",
            "events": event_stream.get_all_events()
        }
    
    # Create agent loop
    agent_loop = AgentLoop(
        incident_id=incident.id,
        event_stream=event_stream,
        planner=planner,
        workspace=workspace,
        context_manager=context_manager,
        knowledge_retriever=knowledge_retriever,
        llm=coding_llm  # Pass LLM for replanning
    )
    
    # Create agents
    agents = create_all_enhanced_agents()
    
    # Create agent executor function
    def agent_executor(step: Dict[str, Any], context: str, workspace: Workspace) -> Dict[str, Any]:
        """Execute agent action for a step."""
        return _execute_agent_action(
            step=step,
            context=context,
            workspace=workspace,
            agents=agents,
            github_integration=github_integration,
            repo_name=repo_name,
            code_memory=code_memory,
            available_files=available_files
        )
    
    # Run agent loop
    initial_context = {
        "root_cause": root_cause,
        "affected_files": affected_files,
        "memory_data": memory_data,
        "error_signature": error_signature
    }
    
    try:
        result = agent_loop.run(agent_executor, initial_context)
        
        # Sync workspace to scratchpad
        scratchpad.sync_from_workspace(workspace)
        
        # Persist events to database (optional, for debugging)
        try:
            from memory_models import AgentEvent, AgentPlan, AgentWorkspace
            
            # Save all events
            for event in event_stream.get_all_events():
                # Skip compression events (they're summaries)
                if event.get("type") == EventType.COMPRESSION.value:
                    continue
                
                db_event = AgentEvent(
                    incident_id=incident.id,
                    event_type=event.get("type", "unknown"),
                    timestamp=event.get("timestamp"),
                    agent_name=event.get("agent"),
                    data=event.get("data", {})
                )
                db.add(db_event)
            
            # Save plan
            if planner.plan:
                db_plan = AgentPlan(
                    incident_id=incident.id,
                    plan=planner.plan,
                    plan_version=planner.replan_count + 1,
                    status="completed" if planner.is_complete() else "active"
                )
                if planner.is_complete():
                    db_plan.completed_at = datetime.utcnow()
                db.add(db_plan)
            
            # Persist workspace state (for learning, debugging, and recovery)
            # Extract fixes first to determine which files were modified
            fixes = _extract_fixes_from_workspace(workspace)
            files_modified = list(fixes.keys()) if fixes else []
            
            try:
                workspace_files = workspace.get_files_dict()
                files_read = list(workspace_files.keys())
                
                db_workspace = AgentWorkspace(
                    incident_id=incident.id,
                    files=workspace_files,  # All file contents
                    plan=workspace.todo,  # Plan state
                    notes=workspace.notes,  # Notes
                    files_read=files_read,  # All files that were read
                    files_modified=files_modified,  # Files that were modified
                    status="completed" if result["success"] else "partial",
                    completed_at=datetime.utcnow()
                )
                db.add(db_workspace)
                print(f"✅ Persisted workspace state for incident {incident.id} ({len(workspace_files)} files, {len(files_modified)} modified)")
            except Exception as workspace_error:
                # Workspace persistence is optional - don't fail if it doesn't work
                print(f"Warning: Failed to persist workspace to database: {workspace_error}")
            
            db.commit()
        except Exception as e:
            # Event persistence is optional - don't fail if it doesn't work
            print(f"Warning: Failed to persist events to database: {e}")
            db.rollback()
        
        # Store fix with workspace context for learning (if successful)
        if result["success"] and fixes:
            try:
                saved_workspace = db.query(AgentWorkspace).filter(
                    AgentWorkspace.incident_id == incident.id
                ).order_by(AgentWorkspace.created_at.desc()).first()
                
                if saved_workspace:
                    workspace_context = {
                        "files_read": saved_workspace.files_read or [],
                        "files_modified": saved_workspace.files_modified or [],
                        "context_files": list(set(saved_workspace.files_read or []) - set(saved_workspace.files_modified or [])),
                        "changes": fixes,
                        "incident_id": incident.id
                    }
                    code_memory.store_fix_with_workspace(
                        error_signature=error_signature,
                        fix_description=f"Fixed {root_cause}",
                        code_patch=json.dumps(fixes),
                        workspace_context=workspace_context,
                        incident_id=incident.id
                    )
                    print(f"🧠 Stored fix with workspace context for learning")
            except Exception as e:
                print(f"Warning: Failed to store fix with workspace context: {e}")
        
        return {
            "status": "success" if result["success"] else "partial",
            "success": result["success"],
            "iterations": result["iterations"],
            "plan_progress": result["plan_progress"],
            "fixes": fixes,
            "events": result["events"],
            "workspace_state": result["workspace_state"],
            "error_signature": error_signature
        }
    except Exception as e:
        event_stream.add_event(
            EventType.ERROR,
            {"message": f"Agent loop failed: {str(e)}"}
        )
        
        # Try to persist error events even on failure
        try:
            from memory_models import AgentEvent
            for event in event_stream.get_all_events()[-10:]:  # Last 10 events
                if event.get("type") != EventType.COMPRESSION.value:
                    db_event = AgentEvent(
                        incident_id=incident.id,
                        event_type=event.get("type", "unknown"),
                        timestamp=event.get("timestamp"),
                        agent_name=event.get("agent"),
                        data=event.get("data", {})
                    )
                    db.add(db_event)
            db.commit()
        except Exception:
            db.rollback()
        
        return {
            "status": "error",
            "error": str(e),
            "events": event_stream.get_all_events(),
            "workspace_state": workspace.get_workspace_state()
        }


def _execute_agent_action(
    step: Dict[str, Any],
    context: str,
    workspace: Workspace,
    agents: Dict[str, Any],
    github_integration: GithubIntegration,
    repo_name: str,
    code_memory: CodeMemory,
    available_files: List[str] = None
) -> Dict[str, Any]:
    """
    Execute agent action for a step.
    Supports both CrewAI tool calls and CodeAct code generation.
    
    Args:
        step: Current step dictionary
        context: Context string for agent
        workspace: Workspace instance
        agents: Dictionary of agents
        github_integration: GitHub integration
        repo_name: Repository name
        code_memory: Code memory instance
        
    Returns:
        Action result dictionary
    """
    step_description = step.get("description", "")
    
    # Determine which agent to use based on step
    agent = _select_agent_for_step(step, agents)
    
    if not agent:
        return {
            "success": False,
            "error": "No suitable agent found for step",
            "agent_name": None
        }
    
    # Build full context with system prompt
    system_prompt = build_system_prompt(
        incident_id=workspace.incident_id,
        root_cause=context.split("Root cause:")[1].split("\n")[0].strip() if "Root cause:" in context else "Unknown",
        affected_files=step.get("files_to_read", []),
        plan_summary=workspace.todo[0].get("description", "") if workspace.todo else "",
        recent_events="",
        current_step_number=step.get("step_number", 1),
        current_step_description=step.get("description", "")
    )
    
    # Escape braces in system_prompt and context to prevent f-string format specifier errors
    def escape_braces_for_fstring(text: str) -> str:
        """Escape braces in text to prevent f-string format specifier errors."""
        if not text:
            return text
        # Replace { with {{ and } with }}, but be careful not to double-escape
        text = text.replace('}}', '\0PLACEHOLDER_DOUBLE_CLOSE\0')
        text = text.replace('{{', '\0PLACEHOLDER_DOUBLE_OPEN\0')
        text = text.replace('{', '{{')
        text = text.replace('}', '}}')
        text = text.replace('\0PLACEHOLDER_DOUBLE_OPEN\0', '{{')
        text = text.replace('\0PLACEHOLDER_DOUBLE_CLOSE\0', '}}')
        return text
    
    safe_system_prompt = escape_braces_for_fstring(system_prompt)
    safe_context = escape_braces_for_fstring(context)
    full_context = f"{safe_system_prompt}\n\n{safe_context}"
    
    # Try to execute agent
    try:
        # Check if agent should generate code (CodeAct) or use tools
        # For now, we'll use a hybrid approach
        action_result = _execute_with_codeact(
            agent=agent,
            step=step,
            context=full_context,
            workspace=workspace,
            github_integration=github_integration,
            repo_name=repo_name,
            available_files=available_files or []
        )
        
        return action_result
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "agent_name": agent.role if hasattr(agent, 'role') else "unknown"
        }


def _detect_languages(available_files: List[str]) -> Dict[str, List[str]]:
    """Detect programming languages from file extensions."""
    language_map = {
        "TypeScript": [".ts", ".tsx"],
        "JavaScript": [".js", ".jsx", ".mjs", ".cjs"],
        "Python": [".py", ".pyw"],
        "Java": [".java"],
        "Go": [".go"],
        "Rust": [".rs"],
        "C/C++": [".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"],
        "C#": [".cs"],
        "Ruby": [".rb"],
        "PHP": [".php"],
        "Swift": [".swift"],
        "Kotlin": [".kt", ".kts"],
        "Scala": [".scala"],
        "HTML": [".html", ".htm"],
        "CSS": [".css", ".scss", ".sass", ".less"],
        "JSON": [".json"],
        "YAML": [".yaml", ".yml"],
        "Markdown": [".md", ".markdown"],
        "Shell": [".sh", ".bash", ".zsh"],
        "Docker": [".dockerfile", "Dockerfile"],
        "Config": [".toml", ".ini", ".conf", ".config"]
    }
    
    detected = {}
    for file_path in available_files:
        # Get file extension
        if "." in file_path:
            ext = "." + file_path.split(".")[-1].lower()
            # Check each language
            for lang, extensions in language_map.items():
                if ext in extensions:
                    if lang not in detected:
                        detected[lang] = []
                    detected[lang].append(file_path)
                    break
    
    return detected


def _format_available_files(available_files: List[str]) -> str:
    """Format available files list for prompt (avoids f-string backslash issue)."""
    if not available_files:
        return "Repository structure not available. Use list_files() function to explore the repository."
    
    # Detect languages
    languages = _detect_languages(available_files)
    
    newline = "\n"
    
    # Build language summary
    lang_summary = []
    if languages:
        lang_summary.append("Repository Languages Detected:")
        for lang, files in sorted(languages.items(), key=lambda x: -len(x[1])):
            lang_summary.append(f"  - {lang}: {len(files)} files")
        lang_summary.append("")
    
    # Build file list
    files_list = newline.join(f"- {f}" for f in available_files[:100])
    result = newline.join(lang_summary) if lang_summary else ""
    result += f"Available files in repository ({len(available_files)} total):{newline}{files_list}"
    
    if len(available_files) > 100:
        result += f"{newline}... and {len(available_files) - 100} more files. Use list_files() to explore."
    
    return result


def _execute_with_codeact(
    agent,
    step: Dict[str, Any],
    context: str,
    workspace: Workspace,
    github_integration: GithubIntegration,
    repo_name: str,
    available_files: List[str] = None
) -> Dict[str, Any]:
    """
    Execute agent with CodeAct support.
    
    Args:
        agent: Agent instance
        step: Step dictionary
        context: Context string
        workspace: Workspace instance
        github_integration: GitHub integration
        repo_name: Repository name
        
    Returns:
        Execution result
    """
    # Detect languages from available files
    languages = _detect_languages(available_files or [])
    primary_languages = [lang for lang, files in sorted(languages.items(), key=lambda x: -len(x[1]))[:3]]
    lang_context = ""
    if primary_languages:
        lang_context = f"\n\n## REPOSITORY LANGUAGE CONTEXT:\n"
        lang_context += f"This repository primarily uses: {', '.join(primary_languages)}\n"
        if "TypeScript" in primary_languages:
            lang_context += "\n⚠️ IMPORTANT: This repository uses TypeScript (.ts/.tsx files).\n"
            lang_context += "When working with TypeScript files:\n"
            lang_context += "- Use TypeScript syntax with type annotations\n"
            lang_context += "- Define interfaces and types where appropriate\n"
            lang_context += "- Use proper TypeScript features (generics, enums, etc.)\n"
            lang_context += "- Do NOT write JavaScript code for .ts files\n"
        if "JavaScript" in primary_languages and "TypeScript" not in primary_languages:
            lang_context += "\n⚠️ IMPORTANT: This repository uses JavaScript (.js/.jsx files).\n"
            lang_context += "When working with JavaScript files, use JavaScript syntax (no type annotations).\n"
    
    # Build prompt that encourages code generation
    # Escape any braces in context/lang_context to prevent f-string format specifier errors
    # This prevents issues if context contains things like {var: "value"} which would be interpreted as format specifiers
    def escape_braces(text: str) -> str:
        """Escape braces in text to prevent f-string format specifier errors."""
        if not text:
            return text
        # Replace { with {{ and } with }}, but be careful not to double-escape
        # First, replace }} with a placeholder, then { with {{, then } with }}, then restore }}
        text = text.replace('}}', '\0PLACEHOLDER_DOUBLE_CLOSE\0')
        text = text.replace('{{', '\0PLACEHOLDER_DOUBLE_OPEN\0')
        text = text.replace('{', '{{')
        text = text.replace('}', '}}')
        text = text.replace('\0PLACEHOLDER_DOUBLE_OPEN\0', '{{')
        text = text.replace('\0PLACEHOLDER_DOUBLE_CLOSE\0', '}}')
        return text
    
    # Format available files BEFORE escaping to avoid any issues
    try:
        available_files_text = _format_available_files(available_files)
    except Exception as e:
        print(f"⚠️  Warning: Error formatting available files: {e}")
        available_files_text = "Repository structure not available. Use list_files() function to explore."
    
    # Escape all dynamic content that will be inserted into the f-string
    safe_context = escape_braces(context)
    safe_lang_context = escape_braces(lang_context)
    safe_description = escape_braces(str(step.get('description', '')))
    safe_available_files = escape_braces(available_files_text)
    
    # Build the prompt with try-catch to catch any f-string format specifier errors
    try:
        codeact_prompt = f"""
{safe_context}{safe_lang_context}

Generate Python code to complete this step: {safe_description}

## SAFE EXECUTION ENVIRONMENT

Your code will run in a restricted, safe execution environment with the following capabilities:

### Available Built-in Functions:
You can use these Python built-ins: abs, all, any, bool, dict, enumerate, float, int, isinstance, len, list, max, min, print, range, repr, round, set, sorted, str, sum, tuple, type, zip

### Available Standard Library Modules:
These modules are PRE-IMPORTED and available directly (no need to import):
- json: For JSON parsing and serialization
- re: For regular expressions
- os: For operating system interface (limited)
- sys: For system-specific parameters (limited)
- datetime: For date and time operations
- time: For time-related functions
- collections: For specialized container datatypes
- itertools: For iterator functions
- functools: For higher-order functions
- operator: For standard operators as functions

You can use them directly without importing:
```python
# No need to import - they're already available!
result = json.loads('{"key": "value"}')
match = re.search(r'pattern', text)
current_time = datetime.now()
```

If you prefer, you can still import them (they're also available via safe_import), but it's not necessary.

**CRITICAL - F-String Format Specifiers (READ CAREFULLY):**
- NEVER use format specifiers with quoted strings like {{var: "value"}} - this causes runtime errors
- CORRECT examples:
  * f"Error: {{error}}" - simple interpolation
  * f"Error: {{error!r}}" - use repr() for debugging  
  * f"Data: {{json.dumps(data)}}" - for JSON output
  * f"Number: {{num:.2f}}" - numeric format specifier (OK)
- WRONG examples (DO NOT USE):
  * f"Error: {{error: \"value\"}}" - INVALID, causes "Invalid format specifier" error
  * f"Data: {{data: json}}" - INVALID, format specifiers can't be variable names
  * f"Key: {{key: \"value\"}}" - INVALID, quoted strings in format specifiers are not allowed
- Format specifiers are ONLY for types: :d (int), :.2f (float), :s (string), :x (hex), etc.
- Format specifiers are NOT for values, variable names, or JSON - use functions instead!

### Available Code Execution Tools:
These functions are pre-imported and available directly:

1. **read_file(file_path: str) -> dict**
   - Read a file from the repository
   - Returns: {{"success": bool, "content": str, "file_path": str, "lines": int, "error": str}}
   - Example: `result = read_file("src/main.py")`

2. **write_file(file_path: str, content: str) -> dict**
   - Write content to a file in the workspace (not committed yet)
   - Returns: {{"success": bool, "message": str, "file_path": str, "lines": int, "error": str}}
   - Example: `result = write_file("src/main.py", new_content)`

3. **apply_incremental_edit(file_path: str, edits: str) -> dict**
   - Apply incremental edits to a file using edit blocks
   - Returns: {{"success": bool, "message": str, "updated_content": str, "error": str}}
   - Example: `result = apply_incremental_edit("src/main.py", edit_blocks)`

4. **validate_code(file_path: str, content: str = None) -> dict**
   - Validate code syntax (Python, JavaScript, TypeScript)
   - Returns: {{"success": bool, "errors": list, "warnings": list, "error": str}}
   - Example: `result = validate_code("src/main.py")`

5. **find_symbol_definition(symbol: str, current_file: str = None) -> dict**
   - Find definition of a symbol (function, class, variable)
   - Returns: {{"success": bool, "definitions": list, "symbol": str, "error": str}}
   - Example: `result = find_symbol_definition("MyClass", "src/main.py")`

6. **update_todo(step_number: int, status: str, result: str = None) -> dict**
   - Update plan step status (pending, in_progress, completed, failed)
   - Returns: {{"success": bool, "message": str, "error": str}}
   - Example: `update_todo(1, "completed", "Fixed null pointer issue")`

7. **retrieve_memory(error_signature: str) -> dict**
   - Retrieve past fixes and error patterns from memory
   - Returns: {{"success": bool, "past_errors": list, "known_fixes": list, "error": str}}
   - Example: `result = retrieve_memory("null_pointer_user_service")`

8. **list_files(directory: str = "", max_depth: int = 2) -> dict**
   - List files in the repository directory
   - Returns: {{"success": bool, "files": list[str], "directory": str, "count": int, "error": str}}
   - Example: `result = list_files("src")` or `result = list_files()` for root directory

### Repository File Structure:
{safe_available_files}

**IMPORTANT**: Always use the exact file paths from the list above. Do NOT guess or assume file paths. If you need to find a file, use list_files() first.

### Code Execution Guidelines:

1. **Always check return values**: All tool functions return dictionaries with "success" field
   ```python
   result = read_file("file.py")
   if result["success"]:
       content = result["content"]
   else:
       print(f"Error: {{result['error']}}")
   ```

2. **Use print() for debugging**: You can use print() to output debug information
   ```python
   print(f"Reading file: {{file_path}}")
   ```

3. **Error handling**: Always handle errors gracefully
   ```python
   try:
       result = read_file("file.py")
       if not result["success"]:
           print(f"Failed: {{result['error']}}")
   except Exception as e:
       print(f"Exception: {{e}}")
   ```

4. **Workspace concept**: Files written with write_file() are stored in workspace, not committed to git yet

5. **File paths - CRITICAL**: 
   - ALWAYS use relative paths from repository root
   - CORRECT: "dist/src/package.json", "src/main.py", "package.json"
   - WRONG: "/app/dist/src/package.json", "/src/main.py", "/package.json"
   - Absolute paths (starting with /) will be automatically converted, but may fail
   - If a file path fails, try removing leading slashes and common prefixes like "app/", "dist/", "workspace/"
   - Example: If "/app/dist/src/package.json" fails, try "dist/src/package.json" or "src/package.json"

6. **Language Detection - CRITICAL**:
   - The repository structure shows which languages are used (see "Repository Languages Detected" above)
   - ALWAYS match the file extension and language when reading/writing files:
     * `.ts` or `.tsx` files = TypeScript - write TypeScript code with types, interfaces, and type annotations
     * `.js` or `.jsx` files = JavaScript - write JavaScript code (no type annotations)
     * `.py` files = Python - write Python code
   - When fixing a `.ts` file, you MUST write TypeScript with proper types, interfaces, and type annotations
   - When fixing a `.js` file, write JavaScript (no type annotations)
   - Check the file extension BEFORE writing code to ensure you use the correct language
   - Example: If reading "src/utils/helper.ts", you MUST write TypeScript code, not JavaScript
   - Example: If reading "src/components/Button.tsx", you MUST write TypeScript/React code with proper types

### Security Restrictions:
- Cannot import dangerous modules (subprocess, socket, etc.)
- Cannot access file system directly (use provided tools)
- Cannot make network requests
- Code execution has timeout (30 seconds default)
- Memory limit: 500MB
- CPU time limit: 60 seconds

### Code Generation Requirements:
- Generate ONLY executable Python code
- No explanations or markdown outside code blocks
- Use the provided tools for all file operations
- Check success status of all tool calls
- Handle errors appropriately

Generate the Python code to complete the step now:
"""
    except ValueError as format_error:
        # Catch f-string format specifier errors when building the prompt
        error_msg = str(format_error)
        if "Invalid format specifier" in error_msg or "format specifier" in error_msg.lower():
            print(f"❌ Error building prompt (f-string format specifier): {format_error}")
            print(f"   Context preview: {context[:200]}")
            print(f"   Description: {step.get('description', '')[:200]}")
            # Try to find what caused it
            problematic_text = ""
            if ": \"value\"" in context or ": \"value\"" in str(step.get('description', '')):
                problematic_text = "Found ': \"value\"' pattern in context or description"
            return {
                "success": False,
                "error": f"Error building prompt due to invalid format specifier. The context or step description may contain invalid f-string syntax like {{var: \"value\"}}. Error: {error_msg}. {problematic_text}",
                "error_type": "prompt_build_error",
                "format_error": error_msg
            }
        else:
            raise
    except Exception as prompt_error:
        error_msg = str(prompt_error)
        if "format specifier" in error_msg.lower():
            print(f"❌ Error building prompt (format specifier): {prompt_error}")
            return {
                "success": False,
                "error": f"Error building prompt due to format specifier issue: {error_msg}",
                "error_type": "prompt_build_error"
            }
        print(f"❌ Unexpected error building prompt: {prompt_error}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": f"Error building prompt: {error_msg}",
            "error_type": "prompt_build_error"
        }
    
    try:
        # Call agent LLM
        if hasattr(agent, 'llm') and agent.llm is not None:
            llm = agent.llm
        elif coding_llm is not None:
            llm = coding_llm
        else:
            return {
                "success": False,
                "error": "No LLM available. Please check OPENCOUNCIL_API environment variable and ensure LLMs are initialized.",
                "agent_name": agent.role if hasattr(agent, 'role') else "unknown"
            }
        
        try:
            if hasattr(llm, 'invoke'):
                response = llm.invoke(codeact_prompt)
                code = response.content if hasattr(response, 'content') else str(response)
            elif hasattr(llm, 'call'):
                # Some LLM interfaces use 'call' method
                response = llm.call(codeact_prompt)
                code = response.content if hasattr(response, 'content') else str(response)
            else:
                # Last resort: raise error instead of trying to call as function
                raise AttributeError("LLM object does not have 'invoke' or 'call' method. Cannot generate code.")
            
            # Check if LLM returned an error message instead of code
            if isinstance(code, tuple):
                code = str(code[0]) if code else ""
            if "Invalid format specifier" in str(code):
                print(f"⚠️  Warning: LLM response contains error message: {code[:200]}")
                # Try to extract actual code if it exists
                if "```" in str(code):
                    # Might have code block with error message
                    pass  # Let extraction handle it
                else:
                    return {
                        "success": False,
                        "error": f"LLM generated error message instead of code. This suggests the prompt may contain invalid format specifiers. Response: {str(code)[:300]}",
                        "error_type": "llm_error_response"
                    }
        except ImportError as import_err:
            if "LiteLLM" in str(import_err) or "litellm" in str(import_err).lower():
                return {
                    "success": False,
                    "error": f"LiteLLM is required but not available. Error: {import_err}. Please install litellm: pip install litellm",
                    "agent_name": agent.role if hasattr(agent, 'role') else "unknown"
                }
            else:
                raise
        
        # Extract code from response
        code = _extract_code_from_response(code)
        
        if not code:
            return {
                "success": False,
                "error": "No code generated by agent",
                "agent_response": code
            }
        
        # Validate code syntax before execution
        syntax_error = _validate_code_syntax(code)
        if syntax_error:
            print(f"❌ Syntax validation failed: {syntax_error}")
            print(f"   Generated code preview (first 500 chars):\n{code[:500]}")
            return {
                "success": False,
                "error": f"Code syntax error: {syntax_error}",
                "error_type": "syntax_error",
                "code_preview": code[:500] if len(code) > 500 else code
            }
        
        # Additional check for invalid f-string format specifiers (runtime errors)
        invalid_fstring_pattern = r'f["\'].*?\{[^}]*?:\s*["\'][^"\']*["\']'
        if re.search(invalid_fstring_pattern, code):
            print(f"❌ Detected invalid f-string format specifier in generated code")
            # Find the problematic line
            lines = code.split('\n')
            for i, line in enumerate(lines, 1):
                if re.search(invalid_fstring_pattern, line):
                    print(f"   Problematic line {i}: {line[:150]}")
                    return {
                        "success": False,
                        "error": f"Invalid f-string format specifier detected at line {i}. Format specifiers cannot contain quoted strings like {{var: \"value\"}}. Use {{var}} or {{var!r}} instead, or use json.dumps() for JSON output.",
                        "error_type": "invalid_fstring",
                        "problematic_line": line[:150],
                        "line_number": i
                    }
        
        # Execute code safely
        execution_result = _execute_code_safely(code, workspace, github_integration, repo_name)
        
        return {
            "success": execution_result.get("success", False),
            "result": execution_result.get("result"),
            "error": execution_result.get("error"),
            "code_executed": code,
            "execution_result": execution_result
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"CodeAct execution failed: {str(e)}"
        }


@contextmanager
def _timeout_context(seconds: int):
    """
    Context manager for code execution timeout.
    Works on both Unix (signal-based) and Windows (threading-based).
    
    Args:
        seconds: Timeout in seconds
    """
    if hasattr(signal, 'SIGALRM'):
        # Unix/Linux: Use signal-based timeout
        def timeout_handler(signum, frame):
            raise TimeoutError(f"Code execution exceeded {seconds} seconds")
        
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows: Use threading-based timeout (less reliable but works)
        import threading
        
        timeout_occurred = threading.Event()
        
        def timeout_worker():
            timeout_occurred.wait(seconds)
            if not timeout_occurred.is_set():
                # Timeout occurred - raise in main thread
                # Note: This is a best-effort approach on Windows
                # The actual execution might continue, but we'll mark it as timed out
                pass
        
        timer_thread = threading.Thread(target=timeout_worker, daemon=True)
        timer_thread.start()
        
        start_time = time.time()
        try:
            yield
            # Check if timeout occurred
            if time.time() - start_time > seconds:
                raise TimeoutError(f"Code execution exceeded {seconds} seconds")
        finally:
            timeout_occurred.set()
            timer_thread.join(timeout=0.1)


def _execute_code_safely(
    code: str,
    workspace: Workspace,
    github_integration: GithubIntegration,
    repo_name: str
) -> Dict[str, Any]:
    """
    Execute code safely with workspace auto-update, timeout, and resource limits.
    
    Args:
        code: Python code to execute
        workspace: Workspace instance
        github_integration: GitHub integration
        repo_name: Repository name
        
    Returns:
        Execution result
    """
    # Set resource limits (memory, CPU time)
    try:
        import resource
        # Limit memory to 500MB (soft limit)
        max_memory = 500 * 1024 * 1024  # 500MB in bytes
        
        # Limit CPU time to 60 seconds
        max_cpu_time = 60
        
        # Get current limits first to avoid exceeding hard limits
        try:
            current_memory = resource.getrlimit(resource.RLIMIT_AS)
            current_cpu = resource.getrlimit(resource.RLIMIT_CPU)
            
            # Only set if the new limit is less than or equal to the current hard limit
            if max_memory <= current_memory[1]:  # current_memory[1] is hard limit
                resource.setrlimit(resource.RLIMIT_AS, (max_memory, current_memory[1]))
            else:
                # Use current soft limit if our desired limit exceeds hard limit
                resource.setrlimit(resource.RLIMIT_AS, (current_memory[0], current_memory[1]))
            
            if max_cpu_time <= current_cpu[1]:  # current_cpu[1] is hard limit
                resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_time, current_cpu[1]))
            else:
                # Use current soft limit if our desired limit exceeds hard limit
                resource.setrlimit(resource.RLIMIT_CPU, (current_cpu[0], current_cpu[1]))
        except (OSError, ValueError) as limit_error:
            # If we can't get or set limits, that's okay - continue without them
            # This can happen on macOS with certain system configurations
            pass
    except ImportError:
        # Resource module not available (e.g., Windows)
        pass
    except (ValueError, OSError) as e:
        # Resource limits not available on all systems or configurations
        # This is not a critical error, so we just continue silently
        pass
    
    # Create safe execution environment with restricted builtins
    safe_builtins = {
        'abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'float', 'int',
        'isinstance', 'len', 'list', 'max', 'min', 'print', 'range', 'repr', 'round',
        'set', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip', '__import__'
    }
    
    # Create a safe __import__ function that only allows importing whitelisted modules
    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        """Safe import that only allows importing code_execution_tools and standard library modules."""
        # Whitelist of allowed modules
        allowed_modules = {
            'code_execution_tools',
            'json', 're', 'os', 'sys', 'datetime', 'time',
            'collections', 'itertools', 'functools', 'operator'
        }
        
        # Check if the module is in the whitelist
        if name in allowed_modules:
            return __import__(name, globals, locals, fromlist, level)
        else:
            raise ImportError(f"Import of '{name}' is not allowed in the restricted execution environment")
    
    # Import builtins module to access built-in functions reliably
    import builtins as builtins_module
    
    restricted_builtins = {}
    for name in safe_builtins:
        if name == '__import__':
            restricted_builtins[name] = safe_import
        elif hasattr(builtins_module, name):
            # Get from builtins module directly (most reliable)
            restricted_builtins[name] = getattr(builtins_module, name)
        elif isinstance(__builtins__, dict) and name in __builtins__:
            # Fallback: if __builtins__ is a dict, check it directly
            restricted_builtins[name] = __builtins__[name]
        elif hasattr(__builtins__, name):
            # Fallback: if __builtins__ is a module, use getattr
            restricted_builtins[name] = getattr(__builtins__, name)
    
    # Import code_execution_tools once for efficiency
    code_execution_tools_module = __import__("code_execution_tools")
    
    # Pre-import commonly used standard library modules
    import json
    import re
    import os
    import sys
    from datetime import datetime
    import time
    import collections
    import itertools
    import functools
    import operator
    
    exec_globals = {
        "__builtins__": restricted_builtins,
        "code_execution_tools": code_execution_tools_module,
        "read_file": code_execution_tools_module.read_file,
        "write_file": code_execution_tools_module.write_file,
        "apply_incremental_edit": code_execution_tools_module.apply_incremental_edit,
        "validate_code": code_execution_tools_module.validate_code,
        "find_symbol_definition": code_execution_tools_module.find_symbol_definition,
        "update_todo": code_execution_tools_module.update_todo,
        "retrieve_memory": code_execution_tools_module.retrieve_memory,
        "list_files": code_execution_tools_module.list_files,
        # Pre-imported standard library modules for convenience
        "json": json,
        "re": re,
        "os": os,
        "sys": sys,
        "datetime": datetime,
        "time": time,
        "collections": collections,
        "itertools": itertools,
        "functools": functools,
        "operator": operator
    }
    
    exec_locals = {}
    
    # Get timeout from env or default to 30 seconds
    timeout_seconds = int(os.getenv("CODE_EXECUTION_TIMEOUT", "30"))
    
    try:
        # Execute code with timeout
        with _timeout_context(timeout_seconds):
            exec(code, exec_globals, exec_locals)
        
        # Get result from locals
        result = exec_locals.get("result") or "Code executed successfully"
        
        # Extract file operations from execution
        files_updated = {}
        # This would be populated by agent_tools functions
        
        return {
            "success": True,
            "result": result,
            "files": files_updated
        }
    except TimeoutError as e:
        return {
            "success": False,
            "error": f"Execution timeout: {str(e)}",
            "error_type": "timeout",
            "result": None
        }
    except MemoryError as e:
        return {
            "success": False,
            "error": f"Memory limit exceeded: {str(e)}",
            "error_type": "memory_limit",
            "result": None
        }
    except Exception as e:
        error_str = str(e)
        
        # Provide helpful hints for common errors
        error_hints = []
        if "File not found" in error_str and "/" in error_str:
            error_hints.append("File path error detected. Remember to use relative paths from repository root.")
            error_hints.append("Example: Use 'dist/src/package.json' instead of '/app/dist/src/package.json'")
            error_hints.append("Try removing leading slashes and common prefixes like 'app/', 'dist/', 'workspace/'")
        elif "Invalid format specifier" in error_str:
            error_hints.append("F-string format specifier error detected.")
            error_hints.append("Common issue: Using {var: \"value\"} in f-strings is invalid.")
            error_hints.append("Fix: Use {var} for simple interpolation, or {var!r} for repr(), or {var!s} for str().")
            error_hints.append("Example: f'Error: {error}' not f'Error: {error: \"value\"}'")
            error_hints.append("If you need JSON, use json.dumps() instead of f-string format specifiers.")
        
        return {
            "success": False,
            "error": error_str,
            "error_type": "execution_error",
            "error_hints": error_hints if error_hints else None,
            "result": None
        }


def _extract_code_from_response(response: str) -> str:
    """Extract Python code from agent response."""
    # Try to find code blocks
    code_patterns = [
        r'```python\n(.*?)```',
        r'```\n(.*?)```',
        r'```python:.*?\n(.*?)```'
    ]
    
    for pattern in code_patterns:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            code = match.group(1).strip()
            # Validate code for common syntax errors
            code = _validate_and_fix_code(code)
            return code
    
    # If no code block, check if entire response is code
    if "import " in response or "def " in response or "=" in response:
        # Might be code, return as-is
        code = response.strip()
        code = _validate_and_fix_code(code)
        return code
    
    return ""


def _validate_and_fix_code(code: str) -> str:
    """
    Validate and fix common code issues before execution.
    
    Args:
        code: Python code string
        
    Returns:
        Fixed code string
    """
    # Check for invalid f-string format specifiers like {var: "value"}
    # Pattern 1: f"{var: "value"}" - format specifier with quoted string
    invalid_pattern1 = r'f["\'].*?\{[^}]*?:\s*["\'][^"\']*["\']'
    # Pattern 2: f'{var: "value"}' - same but with single quotes
    invalid_pattern2 = r'f[\'].*?\{[^}]*?:\s*["\'][^"\']*["\']'
    
    if re.search(invalid_pattern1, code) or re.search(invalid_pattern2, code):
        print("⚠️  Warning: Detected potentially invalid f-string format specifier in generated code")
        # Find the problematic line
        lines = code.split('\n')
        for i, line in enumerate(lines, 1):
            if re.search(invalid_pattern1, line) or re.search(invalid_pattern2, line):
                print(f"   Line {i}: {line[:100]}")
                break
        # Don't try to auto-fix - let it fail with a clear error message
    
    # Check for other common issues
    # Remove any markdown formatting that might have leaked in
    code = re.sub(r'^```python\s*\n', '', code, flags=re.MULTILINE)
    code = re.sub(r'\n```\s*$', '', code, flags=re.MULTILINE)
    
    return code


def _validate_code_syntax(code: str) -> Optional[str]:
    """
    Validate Python code syntax before execution using AST parsing.
    
    Args:
        code: Python code string
        
    Returns:
        Error message if syntax is invalid, None if valid
    """
    import ast
    
    try:
        # Try to parse the code as Python
        ast.parse(code)
        return None
    except SyntaxError as e:
        error_msg = f"Syntax error at line {e.lineno}: {e.msg}"
        if "Invalid format specifier" in e.msg:
            error_msg += "\n\nCommon fix: Replace {var: \"value\"} with {var} or {var!r} in f-strings."
            error_msg += "\nExample: f'Error: {error}' not f'Error: {error: \"value\"}'"
            error_msg += "\nFor JSON output, use json.dumps(): f'Data: {json.dumps(data)}'"
        return error_msg
    except Exception as e:
        return f"Code validation error: {str(e)}"


def _select_agent_for_step(step: Dict[str, Any], agents: Dict[str, Any]) -> Optional[Any]:
    """Select appropriate agent for step."""
    description = step.get("description", "").lower()
    
    # Map step types to agents
    if "read" in description or "explore" in description:
        return agents.get("exploration", {}).get("codebase_explorer")
    elif "fix" in description or "generate" in description or "edit" in description:
        return agents.get("fix_generation", {}).get("code_fixer_primary")
    elif "validate" in description or "check" in description:
        return agents.get("validation", {}).get("syntax_validator")
    elif "analyze" in description or "dependency" in description:
        return agents.get("exploration", {}).get("dependency_analyzer")
    else:
        # Default to code fixer
        return agents.get("fix_generation", {}).get("code_fixer_primary")


def _extract_file_paths_from_logs(logs: List[LogEntry]) -> List[str]:
    """Extract file paths from log entries."""
    file_paths = []
    for log in logs:
        if log.metadata_json and isinstance(log.metadata_json, dict):
            if "file" in log.metadata_json:
                file_paths.append(log.metadata_json["file"])
            if "filename" in log.metadata_json:
                file_paths.append(log.metadata_json["filename"])
            if "filePath" in log.metadata_json:
                file_paths.append(log.metadata_json["filePath"])
    return list(set(file_paths))  # Remove duplicates


def _extract_fixes_from_workspace(workspace: Workspace) -> Dict[str, Any]:
    """Extract fixes from workspace."""
    fixes = {}
    files = workspace.get_files_dict()
    
    for file_path, content in files.items():
        fixes[file_path] = {
            "file_path": file_path,
            "content": content,
            "type": "full_content"  # Would be "incremental_edit" in real implementation
        }
    
    return fixes


def retrieve_workspace(incident_id: int, db: Session) -> Optional[Dict[str, Any]]:
    """
    Retrieve persisted workspace state for an incident.
    
    Useful for:
    - Debugging failed fixes
    - Analyzing agent behavior
    - Learning from past incidents
    - Recovery from crashes
    
    Args:
        incident_id: ID of the incident
        db: Database session
        
    Returns:
        Dictionary with workspace state or None if not found
    """
    try:
        from memory_models import AgentWorkspace
        
        workspace = db.query(AgentWorkspace).filter(
            AgentWorkspace.incident_id == incident_id
        ).order_by(AgentWorkspace.created_at.desc()).first()
        
        if not workspace:
            return None
        
        return {
            "incident_id": workspace.incident_id,
            "files": workspace.files,  # All file contents
            "plan": workspace.plan,  # Plan state
            "notes": workspace.notes,  # Notes
            "files_read": workspace.files_read or [],  # Files that were read
            "files_modified": workspace.files_modified or [],  # Files that were modified
            "status": workspace.status,
            "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
            "completed_at": workspace.completed_at.isoformat() if workspace.completed_at else None
        }
    except Exception as e:
        print(f"Error retrieving workspace: {e}")
        return None

