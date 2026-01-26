"""
Task Planner Module for explicit task decomposition (Manus-style planning).
Breaks high-level goals into numbered, executable steps.
"""
from typing import List, Dict, Any, Optional
from enum import Enum
import json
import copy
import os
from datetime import datetime

class StepStatus(Enum):
    """Status of a plan step."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskPlanner:
    """
    Manus-style planner that breaks high-level goals into ordered steps.
    
    Each step has a description, expected output, and status tracking.
    Supports dynamic re-planning when requirements change or steps fail.
    """
    
    def __init__(self, incident_id: int, github_integration, repo_name: str):
        """
        Initialize task planner.
        
        Args:
            incident_id: ID of the incident
            github_integration: GitHub integration instance
            repo_name: Repository name in format "owner/repo"
        """
        self.incident_id = incident_id
        self.gh = github_integration
        self.repo_name = repo_name
        self.plan: List[Dict[str, Any]] = []
        self.current_step_index = 0
        self.plan_history: List[Dict[str, Any]] = []
        self.replan_count = 0
        self.max_replans = int(os.getenv("MAX_REPLANS", "3"))
    
    def create_plan(
        self, 
        root_cause: str, 
        affected_files: List[str], 
        llm,
        knowledge_context: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate a plan using LLM.
        
        Args:
            root_cause: Root cause analysis
            affected_files: List of affected file paths
            llm: LLM instance for plan generation
            knowledge_context: Optional knowledge context from RAG
            
        Returns:
            List of plan steps
        """
        planning_prompt = f"""
You are a planning assistant. Break down the following incident fix into ordered steps.

Root Cause: {root_cause}
Affected Files: {', '.join(affected_files) if affected_files else 'None'}

{f'Relevant Knowledge: {knowledge_context}' if knowledge_context else ''}

CRITICAL PLANNING REQUIREMENTS:
1. The FIRST step MUST be a comprehensive exploration phase that reads ALL affected files COMPLETELY
2. Steps must be specific and actionable - no vague descriptions
3. Each step that involves code changes MUST first read all relevant files
4. Include steps to trace dependencies and understand the complete codebase context
5. Only after full understanding should fixes be generated

Generate a numbered list of steps. Each step should:
1. Be specific and actionable
2. Have a clear completion criterion
3. List ALL files that need to be read (not just the immediate affected ones)
4. Have an expected output description
5. For code modification steps, ensure previous steps have already read the necessary context

MANDATORY FIRST STEPS:
- Step 1 should read ALL affected files completely
- Step 2 should trace dependencies and read related files
- Step 3 should analyze patterns and understand the codebase structure
- Only then proceed to fix generation

Format as JSON array:
[
    {{
        "step_number": 1,
        "description": "Read and analyze ALL affected files completely to understand the full context",
        "files_to_read": ["file1.py", "file2.py", "related_file.py"],
        "expected_output": "Complete understanding of all affected file contents, their structure, and purpose"
    }},
    {{
        "step_number": 2,
        "description": "Trace dependencies: find and read all files that import or are imported by affected files",
        "files_to_read": ["dependency1.py", "dependency2.py"],
        "expected_output": "Complete dependency graph and understanding of how files interact"
    }},
    {{
        "step_number": 3,
        "description": "Analyze root cause in the context of the complete codebase",
        "files_to_read": [],
        "expected_output": "Root cause analysis based on complete code understanding, not assumptions"
    }},
    {{
        "step_number": 4,
        "description": "Generate code fix based on complete understanding",
        "files_to_read": ["file1.py", "file2.py"],
        "expected_output": "Code fix that addresses root cause correctly"
    }},
    ...
]

Return ONLY the JSON array, no other text.
"""
        
        try:
            # Check if LLM is available
            if llm is None:
                raise ValueError("LLM is not available. Cannot create plan.")
            
            # Call LLM to generate plan
            # CrewAI LLM uses 'call' method, LangChain uses 'invoke'
            if hasattr(llm, 'call'):
                # CrewAI LLM interface
                response = llm.call(planning_prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            elif hasattr(llm, 'invoke'):
                # LangChain LLM interface
                response = llm.invoke(planning_prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            else:
                # Last resort: try to use it as callable (but this will fail for CrewAI LLMs)
                raise AttributeError("LLM object does not have 'call' or 'invoke' method. Cannot generate plan.")
            
            # Extract JSON from response
            plan_json = self._extract_json(response_text)
            plan_data = json.loads(plan_json)
            
            # Ensure plan_data is a list
            if not isinstance(plan_data, list):
                raise ValueError(f"Expected plan_data to be a list, got {type(plan_data)}: {plan_data}")
            
            # Initialize plan with status
            self.plan = []
            for step_data in plan_data:
                # Ensure step_data is a dict
                if not isinstance(step_data, dict):
                    print(f"Warning: Skipping invalid step_data (not a dict): {step_data}")
                    continue
                
                step = {
                    "step_number": step_data.get("step_number", len(self.plan) + 1),
                    "description": step_data.get("description", ""),
                    "files_to_read": step_data.get("files_to_read", []),
                    "expected_output": step_data.get("expected_output", ""),
                    "status": StepStatus.PENDING.value,
                    "result": None,
                    "errors": [],
                    "retry_count": 0,
                    "started_at": None,
                    "completed_at": None
                }
                self.plan.append(step)
            
            return self.plan
            
        except Exception as e:
            print(f"Error creating plan: {e}")
            # Fallback to simple plan
            self.plan = self._create_fallback_plan(affected_files, root_cause)
            return self.plan
    
    def get_current_step(self) -> Optional[Dict[str, Any]]:
        """
        Get the current step to execute.
        
        Returns:
            Current step dictionary or None if all steps complete
        """
        if self.current_step_index < len(self.plan):
            return self.plan[self.current_step_index]
        return None
    
    def mark_step_completed(self, step_number: int, result: Any):
        """
        Mark a step as completed.
        
        Args:
            step_number: Step number to mark
            result: Result of the step execution
        """
        for step in self.plan:
            if step["step_number"] == step_number:
                step["status"] = StepStatus.COMPLETED.value
                step["result"] = str(result)[:500] if result else None  # Truncate long results
                step["completed_at"] = datetime.utcnow().isoformat()
                break
    
    def mark_step_failed(self, step_number: int, error: str):
        """
        Mark a step as failed.
        
        Args:
            step_number: Step number to mark
            error: Error message
        """
        for step in self.plan:
            if step["step_number"] == step_number:
                step["status"] = StepStatus.FAILED.value
                step["errors"].append(error)
                break
    
    def mark_step_in_progress(self, step_number: int):
        """
        Mark a step as in progress.
        
        Args:
            step_number: Step number to mark
        """
        for step in self.plan:
            if step["step_number"] == step_number:
                step["status"] = StepStatus.IN_PROGRESS.value
                step["started_at"] = datetime.utcnow().isoformat()
                break
    
    def advance_to_next_step(self):
        """Move to next step."""
        if self.current_step_index < len(self.plan):
            self.plan[self.current_step_index]["status"] = StepStatus.IN_PROGRESS.value
        self.current_step_index += 1
    
    def is_complete(self) -> bool:
        """
        Check if all steps are completed.
        
        Returns:
            True if all steps are completed or skipped
        """
        return all(
            step["status"] in [StepStatus.COMPLETED.value, StepStatus.SKIPPED.value]
            for step in self.plan
        )
    
    def get_progress(self) -> Dict[str, Any]:
        """
        Get plan progress summary.
        
        Returns:
            Dictionary with progress statistics
        """
        total = len(self.plan)
        completed = sum(1 for s in self.plan if s["status"] == StepStatus.COMPLETED.value)
        failed = sum(1 for s in self.plan if s["status"] == StepStatus.FAILED.value)
        in_progress = sum(1 for s in self.plan if s["status"] == StepStatus.IN_PROGRESS.value)
        pending = sum(1 for s in self.plan if s["status"] == StepStatus.PENDING.value)
        
        return {
            "total_steps": total,
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "pending": pending,
            "completion_percentage": (completed / total * 100) if total > 0 else 0
        }
    
    def to_todo_md(self) -> str:
        """
        Generate todo.md format for file-based tracking.
        
        Returns:
            Markdown string with plan and progress
        """
        lines = ["# Fix Plan", "", f"Incident ID: {self.incident_id}", ""]
        
        progress = self.get_progress()
        lines.append(f"Progress: {progress['completed']}/{progress['total_steps']} steps completed ({progress['completion_percentage']:.1f}%)")
        lines.append("")
        lines.append("## Steps")
        lines.append("")
        
        for step in self.plan:
            status_icon = {
                StepStatus.PENDING.value: "⬜",
                StepStatus.IN_PROGRESS.value: "🔄",
                StepStatus.COMPLETED.value: "✅",
                StepStatus.FAILED.value: "❌",
                StepStatus.SKIPPED.value: "⏭️"
            }.get(step["status"], "⬜")
            
            lines.append(f"{status_icon} **Step {step['step_number']}**: {step['description']}")
            
            if step.get("files_to_read"):
                lines.append(f"   📁 Files: {', '.join(step['files_to_read'])}")
            
            if step.get("result"):
                result_preview = str(step["result"])[:200]
                lines.append(f"   📝 Result: {result_preview}...")
            
            if step.get("errors"):
                for error in step["errors"][:3]:  # Show max 3 errors
                    error_preview = str(error)[:150]
                    lines.append(f"   ❌ Error: {error_preview}...")
            
            if step.get("started_at"):
                lines.append(f"   ⏰ Started: {step['started_at']}")
            
            if step.get("completed_at"):
                lines.append(f"   ✅ Completed: {step['completed_at']}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def replan(
        self, 
        reason: str, 
        new_context: Dict[str, Any], 
        llm,
        knowledge_context: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Regenerate plan based on new information or failures.
        
        Args:
            reason: Why replanning is needed (e.g., "multiple_failures", "user_change", "new_info")
            new_context: Updated context (root cause, affected files, failures)
            llm: LLM instance for plan generation
            knowledge_context: Optional knowledge context from RAG
            
        Returns:
            New plan with steps
        """
        if self.replan_count >= self.max_replans:
            raise Exception(f"Max replan attempts ({self.max_replans}) reached")
        
        # Store current plan in history
        self.plan_history.append({
            "version": len(self.plan_history) + 1,
            "plan": copy.deepcopy(self.plan),
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            "current_step_index": self.current_step_index
        })
        
        # Build replanning prompt
        completed_steps = [s for s in self.plan if s["status"] == StepStatus.COMPLETED.value]
        failed_steps = [s for s in self.plan if s["status"] == StepStatus.FAILED.value]
        
        replan_prompt = f"""
Previous plan failed or needs adjustment.

Reason for replanning: {reason}

Original Root Cause: {new_context.get('root_cause', 'Unknown')}
Affected Files: {', '.join(new_context.get('affected_files', []))}

Completed Steps ({len(completed_steps)}):
{self._format_steps_for_prompt(completed_steps)}

Failed Steps ({len(failed_steps)}):
{self._format_steps_for_prompt(failed_steps)}

{f'Relevant Knowledge: {knowledge_context}' if knowledge_context else ''}

Generate a NEW plan that:
1. Preserves completed steps (don't redo them)
2. Addresses the failures with different approaches
3. Continues from where we left off

Format as JSON array (same format as before).
Return ONLY the JSON array.
"""
        
        try:
            # Check if LLM is available
            if llm is None:
                raise ValueError("LLM is not available. Cannot replan.")
            
            # Generate new plan
            # CrewAI LLM uses 'call' method, LangChain uses 'invoke'
            if hasattr(llm, 'call'):
                # CrewAI LLM interface
                response = llm.call(replan_prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            elif hasattr(llm, 'invoke'):
                # LangChain LLM interface
                response = llm.invoke(replan_prompt)
                response_text = response.content if hasattr(response, 'content') else str(response)
            else:
                # Last resort: try to use it as callable (but this will fail for CrewAI LLMs)
                raise AttributeError("LLM object does not have 'call' or 'invoke' method. Cannot replan.")
            
            plan_json = self._extract_json(response_text)
            
            # Validate and fix JSON before parsing
            try:
                # Try to parse as-is first
                new_plan_data = json.loads(plan_json)
            except json.JSONDecodeError as json_error:
                # If JSON parsing fails, try to fix common issues
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"JSON parsing error in replanning: {json_error}. Attempting to fix...")
                
                # Try to fix invalid escape sequences
                try:
                    import logging
                    logger = logging.getLogger(__name__)
                    # Replace invalid escape sequences (e.g., \ followed by non-escape char)
                    import re
                    # Fix common invalid escapes: \ followed by non-escape character
                    fixed_json = re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r'\\\\', plan_json)
                    new_plan_data = json.loads(fixed_json)
                    logger.info("Successfully fixed JSON escape sequences")
                except (json.JSONDecodeError, Exception) as fix_error:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Failed to fix JSON: {fix_error}. Original error: {json_error}")
                    # Try to extract valid JSON array using more lenient parsing
                    try:
                        # Use ast.literal_eval as fallback for malformed JSON
                        import ast
                        new_plan_data = ast.literal_eval(plan_json)
                        if not isinstance(new_plan_data, list):
                            raise ValueError("Parsed data is not a list")
                    except Exception as ast_error:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.error(f"All JSON parsing attempts failed: {ast_error}")
                        raise ValueError(
                            f"Failed to parse replanning response as JSON. "
                            f"Original error: {json_error}. "
                            f"Response preview: {response_text[:200]}..."
                        )
            
            # Convert to plan format
            new_plan = []
            for step_data in new_plan_data:
                step = {
                    "step_number": step_data.get("step_number", len(new_plan) + 1),
                    "description": step_data.get("description", ""),
                    "files_to_read": step_data.get("files_to_read", []),
                    "expected_output": step_data.get("expected_output", ""),
                    "status": StepStatus.PENDING.value,
                    "result": None,
                    "errors": [],
                    "retry_count": 0,
                    "started_at": None,
                    "completed_at": None
                }
                new_plan.append(step)
            
            # Merge with completed steps from old plan
            self._merge_plans(self.plan, new_plan)
            
            self.replan_count += 1
            return self.plan
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception(
                f"Error during replanning: {e}",
                extra={"reason": reason, "replan_count": self.replan_count}
            )
            # Keep current plan but mark as needing attention
            # Log the response text for debugging (truncated)
            if 'response_text' in locals():
                logger.debug(f"Replanning response (first 500 chars): {response_text[:500]}")
            return self.plan
    
    def update_plan(self, step_number: int, updates: Dict[str, Any]):
        """
        Update specific plan step without full replanning.
        
        Args:
            step_number: Step number to update
            updates: Dictionary of fields to update
        """
        for step in self.plan:
            if step["step_number"] == step_number:
                step.update(updates)
                break
    
    def _merge_plans(self, old_plan: List[Dict], new_plan: List[Dict]):
        """
        Merge old and new plans, preserving completed steps.
        
        Args:
            old_plan: Previous plan
            new_plan: Newly generated plan
        """
        completed_steps = [s for s in old_plan if s["status"] == StepStatus.COMPLETED.value]
        
        # Add completed steps at the beginning of new plan
        merged_plan = []
        step_numbers_used = set()
        
        # Add completed steps first
        for completed in completed_steps:
            merged_plan.append(completed)
            step_numbers_used.add(completed["step_number"])
        
        # Add new plan steps, adjusting step numbers if needed
        next_step_num = max(step_numbers_used) + 1 if step_numbers_used else 1
        for new_step in new_plan:
            # Check if equivalent step exists
            equivalent = self._find_equivalent_step(new_step, completed_steps)
            if not equivalent:
                new_step["step_number"] = next_step_num
                next_step_num += 1
                merged_plan.append(new_step)
        
        self.plan = merged_plan
        # Reset current step index to first incomplete step
        for i, step in enumerate(self.plan):
            if step["status"] == StepStatus.PENDING.value:
                self.current_step_index = i
                break
    
    def _find_equivalent_step(self, step: Dict, completed_steps: List[Dict]) -> Optional[Dict]:
        """
        Find if a step is equivalent to a completed step.
        
        Args:
            step: Step to check
            completed_steps: List of completed steps
            
        Returns:
            Equivalent completed step or None
        """
        step_desc_lower = step.get("description", "").lower()
        for completed in completed_steps:
            completed_desc_lower = completed.get("description", "").lower()
            # Simple similarity check - could be enhanced
            if step_desc_lower == completed_desc_lower:
                return completed
        return None
    
    def _format_steps_for_prompt(self, steps: List[Dict]) -> str:
        """Format steps for inclusion in prompt."""
        if not steps:
            return "None"
        
        formatted = []
        for step in steps:
            formatted.append(f"Step {step['step_number']}: {step['description']}")
            if step.get("errors"):
                formatted.append(f"  Errors: {', '.join(step['errors'][:2])}")
        return "\n".join(formatted)
    
    def _extract_json(self, text: str) -> str:
        """
        Extract JSON array from LLM response.
        
        Args:
            text: LLM response text
            
        Returns:
            JSON string
        """
        import re
        # Try to find JSON array
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            return json_match.group(0)
        
        # Try to find JSON wrapped in code blocks
        code_block_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1)
        
        # Return as-is and hope it's valid JSON
        return text.strip()
    
    def _create_fallback_plan(self, affected_files: List[str], root_cause: str) -> List[Dict[str, Any]]:
        """
        Create a simple fallback plan if LLM planning fails.
        
        Args:
            affected_files: List of affected files
            root_cause: Root cause description
            
        Returns:
            Simple plan with mandatory exploration steps
        """
        return [
            {
                "step_number": 1,
                "description": f"Read and understand ALL affected files completely: {', '.join(affected_files[:5]) if affected_files else 'None'}. Read the complete file contents, understand the code structure, purpose, and how each file fits into the system.",
                "files_to_read": affected_files[:10] if affected_files else [],
                "expected_output": "Complete understanding of all affected file contents, their structure, purpose, and code patterns",
                "status": StepStatus.PENDING.value,
                "result": None,
                "errors": [],
                "retry_count": 0,
                "started_at": None,
                "completed_at": None
            },
            {
                "step_number": 2,
                "description": "Trace dependencies: Find and read all files that are imported by or import the affected files. Use find_symbol_definition for all referenced symbols.",
                "files_to_read": [],  # Will be discovered during execution
                "expected_output": "Complete dependency graph showing how files interact and where all symbols are defined",
                "status": StepStatus.PENDING.value,
                "result": None,
                "errors": [],
                "retry_count": 0,
                "started_at": None,
                "completed_at": None
            },
            {
                "step_number": 3,
                "description": f"Analyze root cause in context of complete codebase: {root_cause[:150]}. Based on the files read and dependencies traced, identify the specific root cause.",
                "files_to_read": [],
                "expected_output": "Root cause analysis based on complete code understanding, not assumptions, with specific code locations",
                "status": StepStatus.PENDING.value,
                "result": None,
                "errors": [],
                "retry_count": 0,
                "started_at": None,
                "completed_at": None
            },
            {
                "step_number": 4,
                "description": "Generate code fix based on complete understanding. Use incremental edits to fix only what's necessary.",
                "files_to_read": affected_files[:5] if affected_files else [],
                "expected_output": "Code fix with incremental edits that addresses the root cause correctly",
                "status": StepStatus.PENDING.value,
                "result": None,
                "errors": [],
                "retry_count": 0,
                "started_at": None,
                "completed_at": None
            },
            {
                "step_number": 5,
                "description": "Validate fix: Check syntax, analyze impact on dependencies, and verify the fix is correct",
                "files_to_read": [],
                "expected_output": "Validation results confirming fix is syntactically correct and doesn't break dependencies",
                "status": StepStatus.PENDING.value,
                "result": None,
                "errors": [],
                "retry_count": 0,
                "started_at": None,
                "completed_at": None
            }
        ]
    
    def summarize_completed_steps(self) -> str:
        """
        Summarize completed steps to save context.
        
        Returns:
            Summary string of completed steps
        """
        completed = [s for s in self.plan if s["status"] == StepStatus.COMPLETED.value]
        
        if not completed:
            return ""
        
        summary = f"Completed {len(completed)}/{len(self.plan)} steps:\n"
        for step in completed:
            summary += f"- Step {step['step_number']}: {step['description']}\n"
            if step.get('result'):
                result_preview = str(step['result'])[:100]
                summary += f"  Result: {result_preview}...\n"
        
        return summary

import os

