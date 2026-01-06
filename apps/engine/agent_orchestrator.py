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
except Exception as e:
    print(f"Warning: Failed to initialize LLMs: {e}")
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
        plan = planner.create_plan(root_cause, affected_files, coding_llm, knowledge_context)
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
        print(f"Error creating plan: {e}")
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
            code_memory=code_memory
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
                    from datetime import datetime
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
    code_memory: CodeMemory
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
    
    full_context = f"{system_prompt}\n\n{context}"
    
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
            repo_name=repo_name
        )
        
        return action_result
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "agent_name": agent.role if hasattr(agent, 'role') else "unknown"
        }


def _execute_with_codeact(
    agent,
    step: Dict[str, Any],
    context: str,
    workspace: Workspace,
    github_integration: GithubIntegration,
    repo_name: str
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
    # Build prompt that encourages code generation
    codeact_prompt = f"""
{context}

Generate Python code to complete this step: {step.get('description', '')}

You can use these functions:
- read_file(file_path) -> dict
- write_file(file_path, content) -> dict
- apply_incremental_edit(file_path, edits) -> dict
- validate_code(file_path, content=None) -> dict
- find_symbol_definition(symbol, current_file=None) -> dict
- update_todo(step_number, status, result=None) -> dict
- retrieve_memory(error_signature) -> dict

Import code_execution_tools at the start:
```python
import code_execution_tools
```

Generate executable Python code that completes the step.
Return ONLY the Python code, no explanations.
"""
    
    try:
        # Call agent LLM
        if hasattr(agent, 'llm'):
            llm = agent.llm
        else:
            llm = coding_llm
        
        if hasattr(llm, 'invoke'):
            response = llm.invoke(codeact_prompt)
            code = response.content if hasattr(response, 'content') else str(response)
        else:
            code = str(llm(codeact_prompt))
        
        # Extract code from response
        code = _extract_code_from_response(code)
        
        if not code:
            return {
                "success": False,
                "error": "No code generated by agent",
                "agent_response": code
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
        # Limit memory to 500MB (soft limit)
        max_memory = 500 * 1024 * 1024  # 500MB in bytes
        resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))
        
        # Limit CPU time to 60 seconds
        max_cpu_time = 60
        resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_time, max_cpu_time))
    except (ValueError, OSError) as e:
        # Resource limits not available on all systems (e.g., Windows)
        print(f"Warning: Could not set resource limits: {e}")
    
    # Create safe execution environment with restricted builtins
    safe_builtins = {
        'abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'float', 'int',
        'isinstance', 'len', 'list', 'max', 'min', 'range', 'repr', 'round',
        'set', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip'
    }
    
    restricted_builtins = {}
    for name in safe_builtins:
        if hasattr(__builtins__, name):
            restricted_builtins[name] = getattr(__builtins__, name)
    
    exec_globals = {
        "__builtins__": restricted_builtins,
        "code_execution_tools": __import__("code_execution_tools"),
        "read_file": __import__("code_execution_tools").read_file,
        "write_file": __import__("code_execution_tools").write_file,
        "apply_incremental_edit": __import__("code_execution_tools").apply_incremental_edit,
        "validate_code": __import__("code_execution_tools").validate_code,
        "find_symbol_definition": __import__("code_execution_tools").find_symbol_definition,
        "update_todo": __import__("code_execution_tools").update_todo,
        "retrieve_memory": __import__("code_execution_tools").retrieve_memory
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
        return {
            "success": False,
            "error": str(e),
            "error_type": "execution_error",
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
            return match.group(1).strip()
    
    # If no code block, check if entire response is code
    if "import " in response or "def " in response or "=" in response:
        # Might be code, return as-is
        return response.strip()
    
    return ""


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

