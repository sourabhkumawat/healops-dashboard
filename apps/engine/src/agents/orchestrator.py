"""
Agent Orchestrator - Main orchestrator for the Healops AI agent system.
Implements Manus-style architecture with iterative agent loop, explicit planning, and event streaming.

This is the main entry point for running the agent system to resolve incidents.
"""
from typing import Dict, Any, List, Optional, Callable, Tuple
import os
import json
import re
import subprocess
import tempfile
import signal
import resource
import time
import logging
from contextlib import contextmanager
from datetime import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

# Configure logging
logger = logging.getLogger(__name__)

# Constants
AGENT_STATUS_AVAILABLE = "available"
AGENT_STATUS_WORKING = "working"
AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_DISABLED = "disabled"

MAX_COMPLETED_TASKS = 50
DEFAULT_PHASE = "fix_generation"
DEFAULT_AGENT_KEY = "code_fixer_primary"

# Timeout Configuration (all in seconds)
# These can be overridden via environment variables
# 
# Recommended timeout values based on typical execution patterns:
# - LLM_CALL_TIMEOUT: 60s - Complex code generation can take 20-45s
# - CODE_EXECUTION_TIMEOUT: 30s - Most code operations complete in 5-15s
# - AGENT_STEP_TIMEOUT: 180s (3min) - Each step = LLM call + code execution + tool calls
# - CREW_EXECUTION_TIMEOUT: 1200s (20min) - Allows for 50 iterations × ~20s avg
# - HTTP_LLM_API_TIMEOUT: 60s - LLM APIs need more time for complex requests
# - HTTP_GITHUB_API_TIMEOUT: 30s - GitHub operations are usually fast
#
# To override, set environment variables:
#   export LLM_CALL_TIMEOUT=90
#   export CREW_EXECUTION_TIMEOUT=1800
#   etc.
LLM_CALL_TIMEOUT = int(os.getenv("LLM_CALL_TIMEOUT", "60"))  # 60 seconds for LLM API calls
CODE_EXECUTION_TIMEOUT = int(os.getenv("CODE_EXECUTION_TIMEOUT", "30"))  # 30 seconds for code execution
AGENT_STEP_TIMEOUT = int(os.getenv("AGENT_STEP_TIMEOUT", "180"))  # 3 minutes per agent step
CREW_EXECUTION_TIMEOUT = int(os.getenv("CREW_EXECUTION_TIMEOUT", "1200"))  # 20 minutes for entire crew
HTTP_LLM_API_TIMEOUT = int(os.getenv("HTTP_LLM_API_TIMEOUT", "60"))  # 60 seconds for LLM HTTP requests
HTTP_GITHUB_API_TIMEOUT = int(os.getenv("HTTP_GITHUB_API_TIMEOUT", "30"))  # 30 seconds for GitHub API

# Agent role mapping constants
AGENT_PHASE_FIX_GENERATION = "fix_generation"
AGENT_PHASE_EXPLORATION = "exploration"
AGENT_PHASE_VALIDATION = "validation"

from src.core.event_stream import EventStream, EventType
from src.core.task_planner import TaskPlanner
from src.agents.workspace import Workspace
from src.agents.scratchpad import Scratchpad
from src.agents.context_manager import ContextManager
from src.agents.execution_loop import AgentLoop
from src.tools.code_execution import set_agent_tools_context, get_context
from src.memory.knowledge_retriever import KnowledgeRetriever
from src.core.system_prompt import build_system_prompt
from src.memory.memory import CodeMemory
from src.integrations.github.integration import GithubIntegration
from src.database.models import Incident, LogEntry, AgentEmployee
from sqlalchemy.orm import Session
from src.core.ai_analysis import get_incident_fingerprint
from src.agents.definitions import create_all_enhanced_agents
from src.tools.coding import set_coding_tools_context, CodingToolsContext
from src.memory.models import AgentWorkspace
from src.utils.db_retry import execute_with_retry
from src.utils.observability import log_phase, log_phase_start

# LLM Configuration
from src.core.openrouter_client import get_api_key
api_key = get_api_key()
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
                model="openrouter/xiaomi/mimo-v2-flash",  # LiteLLM format: openrouter/<openrouter-model-id>
                base_url=base_url,
                api_key=api_key
            )
            coding_llm = LLM(
                model="openrouter/x-ai/grok-code-fast-1",  # LiteLLM format
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
    # Observability: run start
    log_phase("run_start", incident_id=incident.id, repo_name=repo_name, logs_count=len(logs))

    # Initialize all components
    event_stream = EventStream(incident.id)
    
    # Set up WebSocket broadcasting (lazy import to avoid circular dependency)
    def broadcast_callback(incident_id: int, event: Dict[str, Any]):
        """Broadcast event to WebSocket clients using thread-safe async broadcasting."""
        try:
            from main import agent_event_manager, get_main_event_loop
            import asyncio
            
            # Get the main event loop (thread-safe)
            main_loop = get_main_event_loop()
            
            if main_loop is None:
                # No main event loop available, skip broadcasting
                return
            
            try:
                # Use run_coroutine_threadsafe for thread-safe async execution
                # This works from any thread, including ThreadPoolExecutor threads
                future = asyncio.run_coroutine_threadsafe(
                    agent_event_manager.broadcast(incident_id, event),
                    main_loop
                )
                # Don't wait for completion to avoid blocking
                # The future will be scheduled on the main event loop
            except RuntimeError as e:
                # Event loop is closed or not running
                logger.debug(f"Event loop not available for broadcasting: {e}")
            except Exception as e:
                logger.warning(f"Failed to broadcast event: {e}")
        except ImportError:
            # WebSocket manager not available (e.g., during testing)
            pass
        except Exception as e:
            logger.debug(f"WebSocket broadcasting not available: {e}")
    
    event_stream.set_websocket_broadcast(broadcast_callback)
    
    planner = TaskPlanner(incident.id, github_integration, repo_name)
    workspace = Workspace(incident.id)
    scratchpad = Scratchpad(incident.id, github_integration, repo_name)
    context_manager = ContextManager()
    
    # Initialize knowledge retriever with integration_id for proper table name resolution
    integration_id = incident.integration_id if incident else None
    knowledge_retriever = KnowledgeRetriever(
        github_integration=github_integration,
        repo_name=repo_name,
        integration_id=integration_id
    )
    
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
    _t0 = log_phase_start("memory_retrieve_start", incident_id=incident.id)
    memory_data = code_memory.retrieve_context(error_signature)
    log_phase(
        "memory_retrieved",
        incident_id=incident.id,
        duration_sec=time.time() - _t0,
        fixes_count=len(memory_data.get("known_fixes", [])),
        past_errors_count=len(memory_data.get("past_errors", [])),
    )
    
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
    
    # Scope files for the coding agent: only affected + learned files initially to save cost and time.
    # Agent can use list_files() / read_file() when it needs to view other files.
    scoped_files_for_agent = list(dict.fromkeys(affected_files)) if affected_files else (available_files[:50] if available_files else [])
    if scoped_files_for_agent and scoped_files_for_agent != available_files:
        print(f"📁 Scoped coding agent to {len(scoped_files_for_agent)} relevant file(s); use list_files() only when needed.")
    
    # Index knowledge base
    # Note: Full repository indexing happens at connection time via CocoIndex
    # This is mainly for past fixes indexing and triggering incremental updates if needed
    _t_index = log_phase_start("knowledge_index_start", incident_id=incident.id)
    try:
        # CocoIndex handles incremental updates automatically
        # This call may trigger updates or is a no-op if index exists
        if affected_files:
            files_to_index = affected_files[:20]  # For backward compatibility
            knowledge_retriever.index_codebase_patterns(files_to_index)
        
        # Index past fixes (these are added to the CocoIndex vector store)
        fixes_to_index = memory_data.get("known_fixes") or []
        if fixes_to_index:
            knowledge_retriever.index_past_fixes(fixes_to_index)
        log_phase(
            "knowledge_indexed",
            incident_id=incident.id,
            duration_sec=time.time() - _t_index,
            fixes_indexed=len(fixes_to_index),
        )
    except Exception as e:
        log_phase(
            "knowledge_index_failed",
            incident_id=incident.id,
            duration_sec=time.time() - _t_index,
            error=str(e)[:200],
        )
        print(f"Warning: Knowledge indexing failed: {e}")
    
    # Retrieve knowledge for planning
    knowledge_context = None
    _t_retrieve = log_phase_start("knowledge_retrieve_start", incident_id=incident.id)
    try:
        knowledge = knowledge_retriever.retrieve_for_planning(root_cause, affected_files)
        log_phase(
            "knowledge_retrieved",
            incident_id=incident.id,
            duration_sec=time.time() - _t_retrieve,
            result_count=len(knowledge) if knowledge else 0,
        )
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
        log_phase(
            "knowledge_retrieve_failed",
            incident_id=incident.id,
            duration_sec=time.time() - _t_retrieve,
            error=str(e)[:200],
        )
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
        
        _t_plan = log_phase_start("plan_create_start", incident_id=incident.id)
        plan = planner.create_plan(root_cause, affected_files, coding_llm, enhanced_knowledge_context)
        log_phase(
            "plan_created",
            incident_id=incident.id,
            duration_sec=time.time() - _t_plan,
            steps_count=len(plan),
        )
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
        llm=coding_llm,  # Pass LLM for replanning
        github_integration=github_integration,
        repo_name=repo_name
    )
    
    # Create agents
    agents = create_all_enhanced_agents()
    
    # Load AgentEmployee records and map them to CrewAI agents
    agent_employee_map = _get_agent_employee_mapping(db, agents)
    if agent_employee_map:
        print(f"✅ Loaded {len(agent_employee_map)} AgentEmployee mapping(s) to CrewAI agents")
        
        # Update all participating agents to "working" status when crew starts
        task_description = (
            f"Resolving incident #{incident.id}: "
            f"{incident.title or root_cause[:100]}"
        )
        for crewai_role, mapping in agent_employee_map.items():
            agent_employee = mapping["agent_employee"]
            _update_agent_employee_status(
                db=db,
                crewai_role=crewai_role,
                status=AGENT_STATUS_WORKING,
                current_task=task_description
            )
            logger.info(
                "Set agent status to working",
                extra={
                    "agent_name": agent_employee.name,
                    "incident_id": incident.id,
                    "crewai_role": crewai_role
                }
            )
    else:
        print("ℹ️  No AgentEmployee records found - using CrewAI agents directly")
    
    # Create agent executor function (pass scoped files to save cost; agent can list_files() when needed)
    def agent_executor(step: Dict[str, Any], context: str, workspace: Workspace) -> Dict[str, Any]:
        """Execute agent action for a step."""
        return _execute_agent_action(
            step=step,
            context=context,
            workspace=workspace,
            agents=agents,
            agent_employee_map=agent_employee_map,
            github_integration=github_integration,
            repo_name=repo_name,
            available_files=scoped_files_for_agent,
            db=db
        )
    
    # Run agent loop with overall execution timeout
    initial_context = {
        "root_cause": root_cause,
        "affected_files": affected_files,
        "memory_data": memory_data,
        "error_signature": error_signature
    }
    
    crew_start_time = time.time()
    log_phase("crew_start", incident_id=incident.id, plan_steps=len(planner.plan) if planner.plan else 0)

    try:
        # Wrap agent loop execution with timeout
        def _run_agent_loop():
            return agent_loop.run(agent_executor, initial_context)
        
        # Use ThreadPoolExecutor for overall crew timeout
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_agent_loop)
            try:
                result = future.result(timeout=CREW_EXECUTION_TIMEOUT)
            except FutureTimeoutError:
                future.cancel()
                elapsed_time = time.time() - crew_start_time
                raise TimeoutError(
                    f"Crew execution exceeded {CREW_EXECUTION_TIMEOUT} seconds "
                    f"(ran for {elapsed_time:.1f}s). This prevents runaway processes."
                )
        
        # Sync workspace to scratchpad
        scratchpad.sync_from_workspace(workspace)
        
        # Persist events to database (optional, for debugging)
        try:
            from src.memory.models import AgentEvent, AgentPlan, AgentWorkspace
            
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
        
        # Update all participating agents back to "available" status when crew completes
        if agent_employee_map:
            task_description = (
                f"Resolved incident #{incident.id}: "
                f"{incident.title or root_cause[:100]}"
            )
            for crewai_role, mapping in agent_employee_map.items():
                agent_employee = mapping["agent_employee"]
                _update_agent_employee_status(
                    db=db,
                    crewai_role=crewai_role,
                    status=AGENT_STATUS_AVAILABLE,
                    current_task=None,
                    task_completed=task_description if result["success"] else None
                )
                logger.info(
                    "Set agent status to available after completion",
                    extra={
                        "agent_name": agent_employee.name,
                        "incident_id": incident.id,
                        "success": result["success"],
                        "crewai_role": crewai_role
                    }
                )
        
        log_phase(
            "crew_completed",
            incident_id=incident.id,
            duration_sec=time.time() - crew_start_time,
            success=result["success"],
            iterations=result["iterations"],
            fixes_count=len(fixes),
        )
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
    except TimeoutError as timeout_err:
        # Handle timeout errors specifically
        elapsed_time = time.time() - crew_start_time if 'crew_start_time' in locals() else 0
        error_msg = str(timeout_err)
        log_phase(
            "crew_timeout",
            incident_id=incident.id,
            duration_sec=elapsed_time,
            timeout_sec=CREW_EXECUTION_TIMEOUT,
        )
        event_stream.add_event(
            EventType.ERROR,
            {
                "message": f"Crew execution timeout: {error_msg}",
                "elapsed_time": elapsed_time,
                "timeout_type": "crew_execution_timeout"
            }
        )
        return {
            "status": "error",
            "error": error_msg,
            "error_type": "timeout",
            "elapsed_time": elapsed_time,
            "events": event_stream.get_all_events(),
            "workspace_state": workspace.get_workspace_state()
        }
    except Exception as e:
        log_phase(
            "crew_failed",
            incident_id=incident.id,
            duration_sec=time.time() - crew_start_time,
            error=str(e)[:200],
        )
        event_stream.add_event(
            EventType.ERROR,
            {"message": f"Agent loop failed: {str(e)}"}
        )
        
        # Try to persist error events even on failure
        try:
            from src.memory.models import AgentEvent
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
        
        # Update all participating agents back to "available" status on error
        try:
            if agent_employee_map:
                for crewai_role, mapping in agent_employee_map.items():
                    agent_employee = mapping["agent_employee"]
                    _update_agent_employee_status(
                        db=db,
                        crewai_role=crewai_role,
                        status=AGENT_STATUS_AVAILABLE,
                        current_task=None
                    )
                    logger.warning(
                        "Set agent status to available after error",
                        extra={
                            "agent_name": agent_employee.name,
                            "incident_id": incident.id,
                            "crewai_role": crewai_role
                        }
                    )
        except Exception as status_error:
            logger.exception(
                "Failed to update agent status on error",
                extra={"incident_id": incident.id},
                exc_info=status_error
            )
        
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
    available_files: List[str] = None,
    agent_employee_map: Optional[Dict[str, Any]] = None,
    db: Optional[Session] = None
) -> Dict[str, Any]:
    """
    Execute agent action for a step.
    Supports both CrewAI tool calls and CodeAct code generation.
    (Code memory is provided to tools via set_agent_tools_context, not passed here.)

    Args:
        step: Current step dictionary
        context: Context string for agent
        workspace: Workspace instance
        agents: Dictionary of agents
        github_integration: GitHub integration
        repo_name: Repository name
        available_files: List of available files in repository
        agent_employee_map: Optional mapping of AgentEmployee records to CrewAI agents
        db: Optional database session for status updates

    Returns:
        Action result dictionary
    """
    step_description = step.get("description", "")
    
    # Determine which agent to use based on step (with AgentEmployee mapping if available)
    agent, agent_employee = _select_agent_for_step(step, agents, agent_employee_map, db)
    
    if not agent:
        return {
            "success": False,
            "error": "No suitable agent found for step",
            "agent_name": None,
            "agent_employee": None
        }
    
    # Extract agent name for logging
    agent_name = None
    if agent_employee:
        agent_name = agent_employee.name
        print(f"🤖 Using AgentEmployee: {agent_name} ({agent_employee.crewai_role}) for step: {step_description[:50]}...")
    elif hasattr(agent, 'role'):
        agent_name = agent.role
    
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
    
    # Try to execute agent with per-step timeout
    try:
        # Wrap agent execution with per-step timeout
        def _execute_step():
            # Check if agent should generate code (CodeAct) or use tools
            # For now, we'll use a hybrid approach
            return _execute_with_codeact(
                agent=agent,
                step=step,
                context=full_context,
                workspace=workspace,
                github_integration=github_integration,
                repo_name=repo_name,
                available_files=available_files or []
            )
        
        # Use ThreadPoolExecutor for per-step timeout
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_execute_step)
            try:
                action_result = future.result(timeout=AGENT_STEP_TIMEOUT)
            except FutureTimeoutError:
                future.cancel()
                raise TimeoutError(
                    f"Agent step exceeded {AGENT_STEP_TIMEOUT} seconds. "
                    f"Step: {step_description[:100]}"
                )
        
        # Add AgentEmployee info to result
        if agent_employee:
            action_result["agent_employee"] = {
                "id": agent_employee.id,
                "name": agent_employee.name,
                "email": agent_employee.email,
                "crewai_role": agent_employee.crewai_role
            }
        
        # Update AgentEmployee status to "available" when task completes
        if agent_employee and db:
            task_description = step.get("description", "Unknown task")
            _update_agent_employee_status(
                db=db,
                crewai_role=agent_employee.crewai_role,
                status=AGENT_STATUS_AVAILABLE,
                current_task=None,  # Clear current task
                task_completed=task_description if action_result.get("success") else None
            )
        
        return action_result
    except TimeoutError as timeout_err:
        # Handle step timeout specifically
        error_msg = str(timeout_err)
        if agent_employee and db:
            _update_agent_employee_status(
                db=db,
                crewai_role=agent_employee.crewai_role,
                status=AGENT_STATUS_AVAILABLE,
                current_task=None
            )
        
        return {
            "success": False,
            "error": error_msg,
            "error_type": "timeout",
            "timeout_type": "agent_step_timeout",
            "agent_name": agent_name or (agent.role if hasattr(agent, 'role') else "unknown")
        }
    except Exception as e:
        # Update AgentEmployee status to "available" on error (task failed)
        if agent_employee and db:
            _update_agent_employee_status(
                db=db,
                crewai_role=agent_employee.crewai_role,
                status=AGENT_STATUS_AVAILABLE,
                current_task=None
            )
        
        error_result = {
            "success": False,
            "error": str(e),
            "agent_name": agent_name or (agent.role if hasattr(agent, 'role') else "unknown")
        }
        
        if agent_employee:
            error_result["agent_employee"] = {
                "id": agent_employee.id,
                "name": agent_employee.name,
                "email": agent_employee.email,
                "crewai_role": agent_employee.crewai_role
            }
        
        return error_result


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
    # Use string concatenation instead of f-string to avoid format specifier errors
    # This prevents issues if context contains things like {var: "value"} which would be interpreted as format specifiers
    def escape_braces_for_display(text: str) -> str:
        """Escape braces in text for display in the prompt."""
        if not text:
            return text
        # Replace { with {{ and } with }} so they display as literal braces
        # First, replace }} with a placeholder to avoid double-escaping
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
    
    # Escape all dynamic content that will be inserted into the prompt
    safe_context = escape_braces_for_display(context)
    safe_lang_context = escape_braces_for_display(lang_context)
    safe_description = escape_braces_for_display(str(step.get('description', '')))
    safe_available_files = escape_braces_for_display(available_files_text)
    
    # Build the prompt using string concatenation instead of f-string to avoid format specifier errors
    # This way, we can safely include any text without worrying about f-string interpretation
    try:
        static_template = """
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
"""
        codeact_prompt = (
            "\n" +
            safe_context +
            safe_lang_context +
            "\n\n" +
            "Generate Python code to complete this step: " +
            safe_description +
            static_template +
            safe_available_files +
            """

### ⚠️ CRITICAL: USE PRE-LOADED FILE CONTENTS

The context above includes a section "## PRE-LOADED FILE CONTENTS" with actual file contents that have been loaded for you.

**BEFORE generating any code or making changes:**
1. **READ AND UNDERSTAND** the pre-loaded file contents completely
2. **DO NOT** skip reading these files - they contain the actual code context you need
3. **DO NOT** make assumptions based on file names or error messages alone
4. **TRACE DEPENDENCIES**: For any symbols you see in pre-loaded files, use find_symbol_definition() to find where they're defined
5. **READ DEPENDENCIES**: Read any imported files or dependencies that are referenced

**If the context includes pre-loaded files:**
- Start by analyzing those files completely
- Understand the code structure, patterns, and purpose
- Only after full understanding should you generate fixes
- If you need additional files not pre-loaded, use read_file() to read them

**IMPORTANT**: Always use the exact file paths from the list above. Do NOT guess or assume file paths.

**SCOPED FILES (cost & time saving):** The files listed above are the ones most relevant to this incident. Work from these first. Use list_files() or read_file() only when you need to view another file not in the list.

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
        )
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
            # Use timeout wrapper for LLM calls
            try:
                code = _call_llm_with_timeout(llm, codeact_prompt, timeout_seconds=LLM_CALL_TIMEOUT)
            except TimeoutError as llm_timeout:
                return {
                    "success": False,
                    "error": f"LLM call timeout: {str(llm_timeout)}",
                    "error_type": "timeout",
                    "timeout_type": "llm_call_timeout",
                    "agent_name": agent.role if hasattr(agent, 'role') else "unknown"
                }
            
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
        print(code)
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


def _call_llm_with_timeout(llm, prompt: str, timeout_seconds: int = None) -> Any:
    """
    Call LLM with timeout protection.
    
    Args:
        llm: LLM instance (CrewAI LLM)
        prompt: Prompt string
        timeout_seconds: Timeout in seconds (defaults to LLM_CALL_TIMEOUT)
        
    Returns:
        LLM response
        
    Raises:
        TimeoutError: If LLM call exceeds timeout
    """
    if timeout_seconds is None:
        timeout_seconds = LLM_CALL_TIMEOUT
    
    def _call_llm():
        """Execute LLM call in thread."""
        if hasattr(llm, 'invoke'):
            response = llm.invoke(prompt)
            return response.content if hasattr(response, 'content') else str(response)
        elif hasattr(llm, 'call'):
            response = llm.call(prompt)
            return response.content if hasattr(response, 'content') else str(response)
        else:
            raise AttributeError("LLM object does not have 'invoke' or 'call' method")
    
    # Use ThreadPoolExecutor for timeout
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_call_llm)
        try:
            response = future.result(timeout=timeout_seconds)
            return response
        except FutureTimeoutError:
            future.cancel()
            raise TimeoutError(f"LLM call exceeded {timeout_seconds} seconds")


def _timeout_context_threading_based(seconds: int):
    """
    Threading-based timeout (used when signal is not allowed, e.g. non-main thread).
    Does not interrupt running code; raises TimeoutError only after the block completes
    if it took longer than seconds.
    """
    timeout_occurred = threading.Event()

    @contextmanager
    def _ctx():
        start_time = time.time()
        try:
            yield
            if time.time() - start_time > seconds:
                raise TimeoutError(f"Code execution exceeded {seconds} seconds")
        finally:
            timeout_occurred.set()

    return _ctx()


@contextmanager
def _timeout_context(seconds: int):
    """
    Context manager for code execution timeout.
    Uses signal on Unix only when in the main thread (signal only works there);
    otherwise uses threading-based timeout.
    On Windows, always uses threading-based timeout.
    
    Args:
        seconds: Timeout in seconds
    """
    use_signal = (
        hasattr(signal, 'SIGALRM')
        and threading.current_thread() is threading.main_thread()
    )
    if use_signal:
        # Unix/Linux, main thread: Use signal-based timeout
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
        # Non-main thread or Windows: Use threading-based timeout (no signal)
        with _timeout_context_threading_based(seconds):
            yield


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
        """Safe import that only allows importing standard library modules."""
        # Whitelist of allowed modules (standard library only)
        allowed_modules = {
            'json', 're', 'os', 'sys', 'datetime', 'time',
            'collections', 'itertools', 'functools', 'operator',
            'math', 'random', 'string', 'copy', 'hashlib'
        }
        
        # Check if the module is in the whitelist
        if name in allowed_modules:
            return __import__(name, globals, locals, fromlist, level)
        else:
            raise ImportError(f"Import of '{name}' is not allowed in the restricted execution environment. Only standard library modules are allowed. Use the pre-loaded tool functions instead.")
    
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
    # Use the new path after restructuring
    import sys
    if '.' not in sys.path:
        sys.path.insert(0, '.')
    from src.tools.code_execution import (
        read_file, write_file, apply_incremental_edit,
        validate_code, find_symbol_definition, update_todo,
        retrieve_memory, list_files
    )
    # Create a module-like object for compatibility
    class CodeExecutionToolsModule:
        pass
    code_execution_tools_module = CodeExecutionToolsModule()
    code_execution_tools_module.read_file = read_file
    code_execution_tools_module.write_file = write_file
    code_execution_tools_module.apply_incremental_edit = apply_incremental_edit
    code_execution_tools_module.validate_code = validate_code
    code_execution_tools_module.find_symbol_definition = find_symbol_definition
    code_execution_tools_module.update_todo = update_todo
    code_execution_tools_module.retrieve_memory = retrieve_memory
    code_execution_tools_module.list_files = list_files
    
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
    
    # Use configured timeout constant
    timeout_seconds = CODE_EXECUTION_TIMEOUT
    
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


# Type aliases for better code clarity
AgentMapping = Dict[str, Any]
AgentEmployeeMapping = Dict[str, Dict[str, Any]]


def _map_crewai_role_to_agent_key(crewai_role: str) -> Tuple[str, str]:
    """
    Map crewai_role from AgentEmployee to agent dictionary keys.
    
    Args:
        crewai_role: The crewai_role value (e.g., "code_fixer_primary")
    
    Returns:
        Tuple of (phase, agent_key) for accessing agents dict. Returns default
        values if role is not found.
    
    Example:
        >>> _map_crewai_role_to_agent_key("code_fixer_primary")
        ('fix_generation', 'code_fixer_primary')
    """
    role_mapping: Dict[str, Tuple[str, str]] = {
        "code_fixer_primary": (AGENT_PHASE_FIX_GENERATION, "code_fixer_primary"),
        "code_fixer_alternative": (AGENT_PHASE_FIX_GENERATION, "code_fixer_alternative"),
        "fix_strategist": (AGENT_PHASE_FIX_GENERATION, "fix_strategist"),
        "confidence_scorer": (AGENT_PHASE_FIX_GENERATION, "confidence_scorer"),
        "codebase_explorer": (AGENT_PHASE_EXPLORATION, "codebase_explorer"),
        "dependency_analyzer": (AGENT_PHASE_EXPLORATION, "dependency_analyzer"),
        "pattern_matcher": (AGENT_PHASE_EXPLORATION, "pattern_matcher"),
        "syntax_validator": (AGENT_PHASE_VALIDATION, "syntax_validator"),
        "impact_analyzer": (AGENT_PHASE_VALIDATION, "impact_analyzer"),
        "pattern_consistency_validator": (AGENT_PHASE_VALIDATION, "pattern_consistency_validator"),
        "decision_maker": (AGENT_PHASE_VALIDATION, "decision_maker"),
        # Legacy role mappings (from agents.py)
        "rca_analyst": (AGENT_PHASE_EXPLORATION, "dependency_analyzer"),  # Approximate mapping
        "log_parser": (AGENT_PHASE_EXPLORATION, "codebase_explorer"),  # Approximate mapping
        "safety_officer": (AGENT_PHASE_VALIDATION, "impact_analyzer"),  # Approximate mapping
    }
    
    return role_mapping.get(crewai_role, (DEFAULT_PHASE, DEFAULT_AGENT_KEY))


def _get_agent_employee_mapping(
    db: Session, 
    agents: Dict[str, Dict[str, Any]]
) -> AgentEmployeeMapping:
    """
    Get AgentEmployee records and map them to CrewAI agent instances.
    
    Args:
        db: Database session for querying AgentEmployee records
        agents: Dictionary of CrewAI agents organized by phase
    
    Returns:
        Dictionary mapping crewai_role to agent mapping dict containing:
        - agent_employee: AgentEmployee instance
        - crewai_agent: CrewAI agent instance
        - phase: Agent phase (e.g., "fix_generation")
        - agent_key: Agent key within phase
    
    Raises:
        No exceptions raised - errors are logged and empty dict returned
        for backward compatibility.
    """
    agent_employee_map: AgentEmployeeMapping = {}
    
    try:
        # Query all active AgentEmployee records (optimized query)
        agent_employees = (
            db.query(AgentEmployee)
            .filter(AgentEmployee.status != AGENT_STATUS_DISABLED)
            .all()
        )
        
        if not agent_employees:
            logger.debug("No active AgentEmployee records found")
            return agent_employee_map
        
        for agent_employee in agent_employees:
            phase, agent_key = _map_crewai_role_to_agent_key(agent_employee.crewai_role)
            crewai_agent = agents.get(phase, {}).get(agent_key)
            
            if crewai_agent:
                agent_employee_map[agent_employee.crewai_role] = {
                    "agent_employee": agent_employee,
                    "crewai_agent": crewai_agent,
                    "phase": phase,
                    "agent_key": agent_key
                }
                logger.info(
                    "Mapped AgentEmployee to CrewAI agent",
                    extra={
                        "agent_name": agent_employee.name,
                        "crewai_role": agent_employee.crewai_role,
                        "phase": phase,
                        "agent_key": agent_key
                    }
                )
            else:
                logger.warning(
                    "No CrewAI agent found for role",
                    extra={
                        "crewai_role": agent_employee.crewai_role,
                        "phase": phase,
                        "agent_key": agent_key
                    }
                )
    
    except Exception as e:
        logger.exception("Failed to load AgentEmployee records", exc_info=e)
        # Continue without AgentEmployee mapping (backward compatible)
    
    return agent_employee_map


def _update_agent_employee_status(
    db: Session, 
    crewai_role: str, 
    status: str, 
    current_task: Optional[str] = None,
    task_completed: Optional[str] = None
) -> None:
    """
    Update AgentEmployee status when agent starts/completes tasks.
    
    Updates the database record and optionally posts status to Slack.
    All errors are logged but do not raise exceptions to ensure the
    main workflow continues.
    
    Uses retry logic with exponential backoff to handle database connection failures.
    
    Args:
        db: Database session for updating AgentEmployee records
        crewai_role: The crewai_role to identify the agent
        status: New status (AGENT_STATUS_AVAILABLE, AGENT_STATUS_WORKING, etc.)
        current_task: Current task description (if working). None clears the task.
        task_completed: Task that was just completed (to add to completed_tasks)
    
    Note:
        This function is designed to be non-blocking. Slack posting failures
        are logged but do not affect the database update.
    """
    def _update_operation():
        """Inner function that performs the actual database update."""
        agent_employee = db.query(AgentEmployee).filter(
            AgentEmployee.crewai_role == crewai_role
        ).first()
        
        if not agent_employee:
            logger.warning(
                "AgentEmployee not found for crewai_role",
                extra={"crewai_role": crewai_role}
            )
            return
        
        # Update status and timestamp
        agent_employee.status = status
        agent_employee.updated_at = datetime.utcnow()
        
        # Update current task (always update, including clearing when None)
        # Note: Since current_task is an optional parameter, we always update it
        # when the function is called (even if None, which means clear the task)
        agent_employee.current_task = current_task
        
        # Add completed task to history
        if task_completed:
            completed_tasks = agent_employee.completed_tasks or []
            if not isinstance(completed_tasks, list):
                completed_tasks = []
            
            completed_tasks.append({
                "task": task_completed,
                "completed_at": datetime.utcnow().isoformat()
            })
            # Keep only last N completed tasks to prevent unbounded growth
            agent_employee.completed_tasks = completed_tasks[-MAX_COMPLETED_TASKS:]
        
        db.commit()
        
        logger.info(
            "Updated AgentEmployee status",
            extra={
                "agent_name": agent_employee.name,
                "crewai_role": crewai_role,
                "status": status,
                "has_current_task": current_task is not None,
                "task_completed": task_completed is not None
            }
        )
    
    try:
        # Use retry logic for database operations
        execute_with_retry(
            db=db,
            operation=_update_operation,
            operation_name=f"update_agent_employee_status({crewai_role})"
        )
    except Exception as e:
        logger.exception(
            "Failed to update AgentEmployee status after retries",
            extra={"crewai_role": crewai_role, "status": status},
            exc_info=e
        )
        try:
            db.rollback()
        except Exception:
            pass  # Ignore rollback errors




def _determine_agent_role_from_step(step_description: str) -> str:
    """
    Determine the appropriate agent role based on step description.
    
    Uses keyword matching to identify the type of work required.
    
    Args:
        step_description: Lowercase step description text
    
    Returns:
        crewai_role string identifying the agent type needed
    """
    description = step_description.lower()
    
    # Priority order matters - check more specific patterns first
    if any(keyword in description for keyword in ("read", "explore")):
        return "codebase_explorer"
    elif any(keyword in description for keyword in ("fix", "generate", "edit")):
        return "code_fixer_primary"
    elif any(keyword in description for keyword in ("validate", "check")):
        return "syntax_validator"
    elif any(keyword in description for keyword in ("analyze", "dependency")):
        return "dependency_analyzer"
    
    # Default to code fixer for unknown step types
    return DEFAULT_AGENT_KEY


def _select_agent_for_step(
    step: Dict[str, Any], 
    agents: Dict[str, Dict[str, Any]], 
    agent_employee_map: Optional[AgentEmployeeMapping] = None,
    db: Optional[Session] = None
) -> Tuple[Optional[Any], Optional[AgentEmployee]]:
    """
    Select appropriate agent for step, optionally using AgentEmployee records.
    
    This function intelligently selects the best agent for a given step based on:
    1. Step description keywords
    2. Available AgentEmployee mappings
    3. Fallback to direct agent lookup
    
    Args:
        step: Step dictionary containing at minimum a "description" field
        agents: Dictionary of CrewAI agents organized by phase
        agent_employee_map: Optional mapping of crewai_role to agent mapping dict
        db: Optional database session for status updates
    
    Returns:
        Tuple of (CrewAI Agent instance, AgentEmployee instance or None).
        Both values may be None if no suitable agent is found.
    """
    description = step.get("description", "")
    if not description:
        logger.warning("Step has no description, using default agent")
    
    # Determine which agent role is needed
    target_crewai_role = _determine_agent_role_from_step(description)
    
    # Try to use AgentEmployee mapping if available
    if agent_employee_map and target_crewai_role in agent_employee_map:
        mapping = agent_employee_map[target_crewai_role]
        selected_crewai_agent = mapping["crewai_agent"]
        selected_agent_employee = mapping["agent_employee"]
        
        # Update AgentEmployee status to "working"
        if db and selected_agent_employee:
            _update_agent_employee_status(
                db=db,
                crewai_role=target_crewai_role,
                status=AGENT_STATUS_WORKING,
                current_task=step.get("description", "Unknown task")
            )
        
        return selected_crewai_agent, selected_agent_employee
    
    # Fallback to direct agent selection if no AgentEmployee mapping
    phase, agent_key = _map_crewai_role_to_agent_key(target_crewai_role)
    selected_crewai_agent = agents.get(phase, {}).get(agent_key)
    
    if not selected_crewai_agent:
        logger.warning(
            "No agent found for step",
            extra={
                "target_crewai_role": target_crewai_role,
                "phase": phase,
                "agent_key": agent_key
            }
        )
    
    return selected_crewai_agent, None


def _extract_file_paths_from_logs(logs: List[LogEntry]) -> List[str]:
    """
    Extract file paths from log entries.
    
    Searches for file paths in log metadata using common field names.
    Returns a deduplicated list of file paths.
    
    Args:
        logs: List of LogEntry instances to extract file paths from
    
    Returns:
        List of unique file paths found in log metadata
    
    Example:
        >>> logs = [LogEntry(metadata_json={"file": "src/main.py"})]
        >>> _extract_file_paths_from_logs(logs)
        ['src/main.py']
    """
    file_paths: List[str] = []
    file_path_fields = ("file", "filename", "filePath")
    
    for log in logs:
        if not (log.metadata_json and isinstance(log.metadata_json, dict)):
            continue
        
        for field in file_path_fields:
            file_path = log.metadata_json.get(field)
            if file_path and isinstance(file_path, str):
                file_paths.append(file_path)
    
    # Remove duplicates while preserving order (Python 3.7+ dict preserves insertion order)
    return list(dict.fromkeys(file_paths))


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
        from src.memory.models import AgentWorkspace
        
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

