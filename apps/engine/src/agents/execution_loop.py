"""
Execution Loop - Iterative analyze-plan-execute-observe cycle.
Manus-style one-action-per-iteration loop with observation-driven execution.

Orchestrates the agent's iterative workflow, managing plan execution, observations,
error handling, and replanning.
"""
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import os
from src.core.event_stream import EventStream, EventType
from src.core.task_planner import TaskPlanner, StepStatus
from src.agents.workspace import Workspace
from src.agents.context_manager import ContextManager

class AgentLoop:
    """
    Manus-style iterative agent loop.
    
    Orchestrates: Analyze → Plan → Execute → Observe cycle.
    One action per iteration with observation before proceeding.
    """
    
    def __init__(
        self,
        incident_id: int,
        event_stream: EventStream,
        planner: TaskPlanner,
        workspace: Workspace,
        context_manager: ContextManager,
        knowledge_retriever=None,
        max_iterations: int = None,
        llm=None,
        github_integration=None,
        repo_name: str = None
    ):
        """
        Initialize agent loop.
        
        Args:
            incident_id: ID of the incident
            event_stream: EventStream instance
            planner: TaskPlanner instance
            workspace: Workspace instance
            context_manager: ContextManager instance
            knowledge_retriever: Optional KnowledgeRetriever instance
            max_iterations: Maximum iterations (default from env or 50)
            llm: Optional LLM for replanning
            github_integration: Optional GitHub integration for file access
            repo_name: Optional repository name for file access
        """
        self.incident_id = incident_id
        self.event_stream = event_stream
        self.planner = planner
        self.workspace = workspace
        self.context_manager = context_manager
        self.knowledge_retriever = knowledge_retriever
        self.max_iterations = max_iterations or int(os.getenv("MAX_AGENT_ITERATIONS", "50"))
        self.llm = llm  # LLM for replanning
        self.github_integration = github_integration
        self.repo_name = repo_name
        
        self.current_iteration = 0
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        self.current_context: Dict[str, Any] = {}
    
    def run(
        self,
        agent_executor: Callable,
        initial_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run the agent loop.
        
        Args:
            agent_executor: Function that executes agent actions
            initial_context: Initial context (root_cause, affected_files, etc.)
            
        Returns:
            Final result dictionary
        """
        self.current_context = initial_context
        
        # Log start
        self.event_stream.add_event(
            EventType.USER_REQUEST,
            {"request": "Fix incident", "context": initial_context}
        )
        
        while self.current_iteration < self.max_iterations:
            self.current_iteration += 1
            
            # Check if plan is complete
            if self.planner.is_complete():
                self.event_stream.add_event(
                    EventType.PLAN_STEP_COMPLETED,
                    {"message": "All plan steps completed", "iteration": self.current_iteration}
                )
                break
            
            # Get current step
            current_step = self.planner.get_current_step()
            if not current_step:
                # No more steps
                break
            
            # Mark step as in progress
            self.planner.mark_step_in_progress(current_step["step_number"])
            self.event_stream.add_event(
                EventType.PLAN_STEP_STARTED,
                {
                    "step_number": current_step["step_number"],
                    "description": current_step["description"],
                    "iteration": self.current_iteration
                }
            )
            
            # Build context for agent
            agent_context = self._build_context(current_step)
            
            # Execute agent action (one at a time)
            try:
                action_result = agent_executor(
                    step=current_step,
                    context=agent_context,
                    workspace=self.workspace
                )
            except Exception as e:
                action_result = {
                    "success": False,
                    "error": str(e),
                    "error_type": "execution_error"
                }
            
            # Observe result
            observation = self._observe_result(action_result, current_step)
            
            # Update planner based on observation
            if observation["success"]:
                self.planner.mark_step_completed(
                    current_step["step_number"],
                    observation.get("result")
                )
                self.consecutive_failures = 0
                self.planner.advance_to_next_step()
                
                self.event_stream.add_event(
                    EventType.PLAN_STEP_COMPLETED,
                    {
                        "step_number": current_step["step_number"],
                        "result": str(observation.get("result", ""))[:500],
                        "iteration": self.current_iteration
                    }
                )
            else:
                # Check for repeated file path errors
                error = observation.get("error", "")
                if "File not found" in error and current_step.get("retry_count", 0) >= 2:
                    # Same file path error 3+ times - mark as failed and add helpful error
                    self.planner.mark_step_failed(
                        current_step["step_number"],
                        f"{error}\n\nStopped after multiple attempts. Please check the file path format - use relative paths from repository root (e.g., 'dist/src/package.json' not '/app/dist/src/package.json')."
                    )
                    self.event_stream.add_event(
                        EventType.PLAN_STEP_FAILED,
                        {
                            "step_number": current_step["step_number"],
                            "error": error,
                            "message": "Failed after multiple retries with file path error",
                            "iteration": self.current_iteration
                        }
                    )
                    self.planner.advance_to_next_step()
                    continue
                
                # Check if should retry
                if self._should_retry(current_step, observation):
                    current_step["retry_count"] = current_step.get("retry_count", 0) + 1
                    self.event_stream.add_event(
                        EventType.ERROR,
                        {
                            "message": f"Retrying step {current_step['step_number']} (attempt {current_step['retry_count']})",
                            "error": observation.get("error", ""),
                            "error_hints": observation.get("error_hints"),
                            "iteration": self.current_iteration
                        }
                    )
                    # Don't advance, retry same step
                else:
                    # Mark as failed and advance
                    self.planner.mark_step_failed(
                        current_step["step_number"],
                        observation.get("error", "Unknown error")
                    )
                    self.consecutive_failures += 1
                    self.planner.advance_to_next_step()
                    
                    self.event_stream.add_event(
                        EventType.PLAN_STEP_FAILED,
                        {
                            "step_number": current_step["step_number"],
                            "error": observation.get("error", ""),
                            "iteration": self.current_iteration
                        }
                    )
                    
                    # Check if should replan
                    should_replan, reason = self._check_replan_conditions(observation)
                    if should_replan:
                        self._trigger_replan(reason, agent_executor)
            
            # Update workspace from action result
            if "code_executed" in action_result and action_result.get("code_executed"):
                self.workspace.update_workspace(
                    action_result["code_executed"],
                    action_result
                )
        
        # Final result
        return {
            "success": self.planner.is_complete(),
            "iterations": self.current_iteration,
            "plan_progress": self.planner.get_progress(),
            "workspace_state": self.workspace.get_workspace_state(),
            "events": self.event_stream.get_all_events()
        }
    
    def _build_context(self, current_step: Dict[str, Any]) -> str:
        """
        Build context for agent.
        
        Args:
            current_step: Current step dictionary
            
        Returns:
            Context string
        """
        # Clear context manager
        self.context_manager.clear()
        
        # Add root cause
        if self.current_context.get("root_cause"):
            self.context_manager.add_context(
                self.current_context["root_cause"],
                priority=10,
                category="root_cause"
            )
        
        # Pre-load file contents for files mentioned in the step
        # This is critical to prevent agents from making assumptions
        files_to_preload = set()
        if current_step.get("files_to_read"):
            files_to_preload.update(current_step["files_to_read"])
        if self.current_context.get("affected_files"):
            files_to_preload.update(self.current_context["affected_files"])
        
        # Pre-load actual file contents (not just paths)
        if files_to_preload:
            file_contents_context = self._preload_file_contents(list(files_to_preload))
            if file_contents_context:
                self.context_manager.add_context(
                    file_contents_context,
                    priority=9,  # High priority - actual code context
                    category="files"
                )
        
        # Also add file paths list for reference
        if self.current_context.get("affected_files") or current_step.get("files_to_read"):
            all_files = set(self.current_context.get("affected_files", []))
            if current_step.get("files_to_read"):
                all_files.update(current_step["files_to_read"])
            files_info = f"Files to analyze: {', '.join(sorted(all_files))}"
            self.context_manager.add_context(
                files_info,
                priority=8,
                category="files"
            )
        
        # Add memory if available
        if self.current_context.get("memory_data"):
            memory_text = self._format_memory_context(self.current_context["memory_data"])
            self.context_manager.add_context(
                memory_text,
                priority=7,
                category="memory"
            )
        
        # Add knowledge if retrieved
        if self.knowledge_retriever and current_step.get("description"):
            try:
                knowledge = self.knowledge_retriever.retrieve_relevant_knowledge(
                    current_step["description"],
                    k=3
                )
                if knowledge:
                    self.context_manager.add_knowledge(knowledge)
                    
                    # Log knowledge retrieval
                    for item in knowledge:
                        self.event_stream.add_event(
                            EventType.KNOWLEDGE_RETRIEVED,
                            {
                                "content": item["content"][:300],
                                "relevance": item["relevance_score"],
                                "source": item.get("source", "unknown")
                            }
                        )
            except Exception as e:
                print(f"Warning: Knowledge retrieval failed: {e}")
        
        # Build final context
        event_context = self.event_stream.to_context_string(max_events=10)
        workspace_state = self.workspace.get_workspace_state()
        
        return self.context_manager.build_context(
            event_stream_context=event_context,
            current_step=current_step,
            workspace_state=workspace_state
        )
    
    def _preload_file_contents(self, file_paths: List[str]) -> str:
        """
        Pre-load file contents from GitHub to provide full context.
        
        Args:
            file_paths: List of file paths to load
            
        Returns:
            Formatted string with file contents
        """
        if not file_paths:
            return ""
        
        # Get GitHub integration - prefer stored instance, fallback to context
        github_integration = self.github_integration
        repo_name = self.repo_name
        ref = "main"
        
        if not github_integration or not repo_name:
            # Try to get from agent tools context as fallback
            try:
                from code_execution_tools import get_context as get_tools_context
                tools_ctx = get_tools_context()
                github_integration = github_integration or tools_ctx.get("github_integration")
                repo_name = repo_name or tools_ctx.get("repo_name")
                ref = tools_ctx.get("ref", "main")
            except Exception:
                pass
        
        if not github_integration or not repo_name:
            return ""  # Cannot pre-load without GitHub integration
        
        contents_parts = []
        contents_parts.append("## PRE-LOADED FILE CONTENTS (Read these files completely before making changes):")
        contents_parts.append("")
        
        loaded_count = 0
        max_files_to_load = 10  # Limit to prevent context overflow
        max_file_size = 2000  # Limit file size (lines) to prevent context overflow
        
        for file_path in file_paths[:max_files_to_load]:
            try:
                content = github_integration.get_file_contents(repo_name, file_path, ref)
                if content:
                    lines = content.split('\n')
                    file_preview = content
                    
                    # If file is too large, show first and last portions
                    if len(lines) > max_file_size:
                        file_preview = '\n'.join(lines[:max_file_size//2]) + f"\n\n... ({len(lines) - max_file_size} lines omitted) ...\n\n" + '\n'.join(lines[-max_file_size//2:])
                    
                    contents_parts.append(f"### File: {file_path}")
                    contents_parts.append(f"```")
                    contents_parts.append(file_preview)
                    contents_parts.append(f"```")
                    contents_parts.append("")
                    loaded_count += 1
                else:
                    contents_parts.append(f"### File: {file_path}")
                    contents_parts.append(f"⚠️ Could not load file contents (file may not exist or may be inaccessible)")
                    contents_parts.append("")
            except Exception as e:
                contents_parts.append(f"### File: {file_path}")
                contents_parts.append(f"⚠️ Error loading file: {str(e)}")
                contents_parts.append("")
        
        if loaded_count == 0:
            return ""  # No files loaded
        
        if len(file_paths) > max_files_to_load:
            contents_parts.append(f"Note: Only showing first {max_files_to_load} files. Additional files should be read using read_file() tool.")
        
        contents_parts.append("**IMPORTANT**: Read and understand ALL file contents above before making any changes. Do not make assumptions based on file names alone.")
        
        return "\n".join(contents_parts)
    
    def _format_memory_context(self, memory_data: Dict[str, Any]) -> str:
        """
        Format memory data for context.
        
        Args:
            memory_data: Memory data dictionary
            
        Returns:
            Formatted string
        """
        parts = []
        
        if memory_data.get("known_fixes"):
            parts.append("### Known Fixes from Past Incidents:")
            for i, fix in enumerate(memory_data["known_fixes"][:3], 1):
                desc = fix.get('description', 'No description')
                patch = fix.get('patch', '')[:300]  # Limit patch preview
                parts.append(f"Fix #{i}: {desc}")
                if patch:
                    parts.append(f"  Patch preview: {patch}...")
            parts.append("")
        
        if memory_data.get("past_errors"):
            parts.append("### Past Error Context:")
            for i, err in enumerate(memory_data["past_errors"][:2], 1):
                context = err.get("context", "")[:500]
                parts.append(f"Context #{i}: {context}...")
        
        return "\n".join(parts) if parts else "No memory context available."
    
    def _observe_result(
        self,
        action_result: Dict[str, Any],
        current_step: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Observe and interpret action result.
        
        Args:
            action_result: Result from agent action
            current_step: Current step dictionary
            
        Returns:
            Observation dictionary with success, result, error, error_type
        """
        # Log observation
        self.event_stream.add_event(
            EventType.OBSERVATION,
            {
                "step_number": current_step["step_number"],
                "action_result": action_result
            }
        )
        
        # Check success
        if action_result.get("success", False):
            return {
                "success": True,
                "result": action_result.get("result") or action_result.get("message", "Action completed"),
                "data": action_result
            }
        
        # Classify error
        error = action_result.get("error", "Unknown error")
        error_type = self._classify_error(error)
        
        # Include error hints if available
        error_hints = action_result.get("error_hints")
        if error_hints:
            error = f"{error}\n\nHints:\n" + "\n".join(f"- {hint}" for hint in error_hints)
        
        return {
            "success": False,
            "error": error,
            "error_type": error_type,
            "error_hints": error_hints,
            "data": action_result
        }
    
    def _classify_error(self, error: str) -> str:
        """
        Classify error type.
        
        Args:
            error: Error message
            
        Returns:
            Error type (retryable, non_retryable, critical)
        """
        error_lower = error.lower()
        
        # Retryable errors
        retryable_keywords = ["timeout", "network", "connection", "temporary", "rate limit", "429", "503"]
        if any(keyword in error_lower for keyword in retryable_keywords):
            return "retryable"
        
        # Critical errors
        critical_keywords = ["critical", "fatal", "cannot proceed", "impossible"]
        if any(keyword in error_lower for keyword in critical_keywords):
            return "critical"
        
        # Non-retryable (syntax, logic errors)
        return "non_retryable"
    
    def _should_retry(
        self,
        current_step: Dict[str, Any],
        observation: Dict[str, Any]
    ) -> bool:
        """
        Determine if step should be retried.
        
        Args:
            current_step: Current step dictionary
            observation: Observation dictionary
            
        Returns:
            True if should retry
        """
        max_retries = int(os.getenv("MAX_RETRIES_PER_STEP", "3"))
        
        # Check retry count
        retry_count = current_step.get("retry_count", 0)
        if retry_count >= max_retries:
            return False
        
        # Check error type
        error_type = observation.get("error_type", "non_retryable")
        if error_type == "retryable":
            return True
        
        if error_type == "critical":
            return False
        
        # For non-retryable errors, allow one retry in case it's a transient issue
        return retry_count < 1
    
    def _check_replan_conditions(self, observation: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Check if replanning should be triggered.
        
        Args:
            observation: Latest observation
            
        Returns:
            Tuple of (should_replan, reason)
        """
        # Multiple consecutive failures
        if self.consecutive_failures >= self.max_consecutive_failures:
            return True, "multiple_consecutive_failures"
        
        # Critical error
        if observation.get("error_type") == "critical":
            return True, "critical_error_discovered"
        
        # Scope change (if detected in observation)
        if observation.get("scope_change"):
            return True, "scope_change"
        
        return False, None
    
    def _trigger_replan(self, reason: str, agent_executor: Callable):
        """
        Trigger replanning.
        
        Args:
            reason: Reason for replanning
            agent_executor: Agent executor function (for LLM access)
        """
        try:
            self.event_stream.add_event(
                EventType.PLAN_UPDATED,
                {
                    "reason": reason,
                    "replan_count": self.planner.replan_count + 1
                }
            )
            
            # Get updated context
            new_context = {
                "root_cause": self.current_context.get("root_cause"),
                "affected_files": self.current_context.get("affected_files", []),
                "failures": self._get_recent_failures(),
                "discoveries": self._get_recent_discoveries()
            }
            
            # Get knowledge context if available
            knowledge_context = None
            if self.knowledge_retriever:
                try:
                    knowledge = self.knowledge_retriever.retrieve_for_planning(
                        new_context.get("root_cause", ""),
                        new_context.get("affected_files", [])
                    )
                    if knowledge:
                        knowledge_context = "\n".join([k["content"][:200] for k in knowledge[:3]])
                except Exception:
                    pass
            
            # Replan using planner.replan()
            if self.llm:
                try:
                    new_plan = self.planner.replan(reason, new_context, self.llm, knowledge_context)
                    self.event_stream.add_event(
                        EventType.PLAN_CREATED,
                        {
                            "plan": new_plan,
                            "is_replan": True,
                            "reason": reason,
                            "steps_count": len(new_plan)
                        }
                    )
                    # Reset current step index to first incomplete step
                    for i, step in enumerate(self.planner.plan):
                        if step.get("status") == "pending":
                            self.planner.current_step_index = i
                            break
                except Exception as e:
                    self.event_stream.add_event(
                        EventType.ERROR,
                        {
                            "message": f"Replanning failed: {str(e)}",
                            "reason": reason
                        }
                    )
            else:
                # Log replan request if LLM not available
                self.event_stream.add_event(
                    EventType.PLAN_UPDATED,
                    {
                        "message": f"Replanning requested: {reason}",
                        "note": "LLM not available for replanning"
                    }
                )
            
        except Exception as e:
            self.event_stream.add_event(
                EventType.ERROR,
                {"message": f"Replanning failed: {str(e)}"}
            )
    
    def _get_recent_failures(self) -> List[Dict[str, Any]]:
        """Get recent failed steps."""
        failed_steps = [
            s for s in self.planner.plan
            if s.get("status") == StepStatus.FAILED.value
        ]
        return [
            {
                "step_number": s["step_number"],
                "description": s["description"],
                "errors": s.get("errors", [])
            }
            for s in failed_steps[-3:]  # Last 3 failures
        ]
    
    def _get_recent_discoveries(self) -> List[str]:
        """Get recent discoveries from observations."""
        # Extract discoveries from recent observations
        observations = self.event_stream.get_events_by_type(EventType.OBSERVATION)
        discoveries = []
        
        for obs in observations[-5:]:  # Last 5 observations
            data = obs.get("data", {})
            if data.get("discovery"):
                discoveries.append(data["discovery"])
        
        return discoveries

