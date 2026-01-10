"""
Agent Workspace - In-memory workspace management during agent execution.
Manus-style workspace that tracks files, plan state, and notes.

Manages the agent's working state, including file contents, plan progress,
and observations during the incident resolution process.
"""
from typing import Dict, Any, List, Optional
import json
import re
import ast

class Workspace:
    """
    Manus-style workspace for managing in-memory state during execution.
    
    Tracks file contents, todo state, and notes. Automatically updates
    based on code execution and file operations.
    """
    
    def __init__(self, incident_id: int):
        """
        Initialize workspace.
        
        Args:
            incident_id: ID of the incident
        """
        self.incident_id = incident_id
        self.files: Dict[str, str] = {}  # file_path -> content
        self.todo: Optional[List[Dict[str, Any]]] = None  # Plan steps
        self.notes: List[Dict[str, Any]] = []  # Notes with category and timestamp
    
    def update_workspace(self, code: str, execution_result: Dict[str, Any]):
        """
        Update workspace based on code execution.
        
        Detects file operations from code using AST parsing (more accurate) 
        with regex fallback, and updates workspace accordingly.
        
        Args:
            code: Python code that was executed
            execution_result: Result from code execution
        """
        # Try AST parsing first (more accurate)
        operations = self._extract_file_operations_ast(code)
        
        if operations:
            # Use AST-extracted operations
            for op in operations:
                func_name = op.get("function")
                file_path = op.get("file_path")
                
                if file_path and "files" in execution_result and file_path in execution_result["files"]:
                    self.files[file_path] = execution_result["files"][file_path]
                
                # Handle todo updates
                if func_name == "update_todo" and op.get("step_number"):
                    self._update_todo_step(op["step_number"], op.get("status", "in_progress"))
        else:
            # Fallback to regex if AST parsing fails or finds nothing
            self._update_workspace_regex(code, execution_result)
    
    def _extract_file_operations_ast(self, code: str) -> List[Dict[str, Any]]:
        """
        Extract file operations from code using AST parsing.
        More accurate than regex - handles variables, multi-line calls, etc.
        
        Args:
            code: Python code to parse
            
        Returns:
            List of operation dictionaries with function name and file path
        """
        operations = []
        
        try:
            tree = ast.parse(code)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check if it's a direct function call
                    func_name = None
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        # Handle agent_tools.read_file() style
                        if isinstance(node.func.value, ast.Name) and node.func.value.id == "agent_tools":
                            func_name = node.func.attr
                    
                    if func_name in ["read_file", "write_file", "apply_incremental_edit", "validate_code"]:
                        # Extract file path from first argument
                        if node.args:
                            file_path = self._extract_string_from_ast(node.args[0])
                            if file_path:
                                operations.append({
                                    "function": func_name,
                                    "file_path": file_path
                                })
                    
                    elif func_name == "update_todo":
                        # Extract step number and status
                        step_number = None
                        status = None
                        
                        if len(node.args) >= 1:
                            step_number = self._extract_number_from_ast(node.args[0])
                        if len(node.args) >= 2:
                            status = self._extract_string_from_ast(node.args[1])
                        
                        if step_number:
                            operations.append({
                                "function": func_name,
                                "step_number": step_number,
                                "status": status
                            })
        
        except SyntaxError:
            # Code might be incomplete or invalid, fallback to regex
            return []
        except Exception as e:
            # AST parsing failed, fallback to regex
            print(f"Warning: AST parsing failed: {e}")
            return []
        
        return operations
    
    def _extract_string_from_ast(self, node: ast.AST) -> Optional[str]:
        """
        Extract string value from AST node.
        Handles string literals and simple variable references.
        
        Args:
            node: AST node to extract from
            
        Returns:
            String value or None
        """
        if isinstance(node, ast.Str):  # Python < 3.8
            return node.s
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):  # Python 3.8+
            return node.value
        elif isinstance(node, ast.Name):
            # Variable reference - can't resolve at parse time
            # Would need execution context, but we'll return None for now
            return None
        return None
    
    def _extract_number_from_ast(self, node: ast.AST) -> Optional[int]:
        """
        Extract number value from AST node.
        
        Args:
            node: AST node to extract from
            
        Returns:
            Number value or None
        """
        if isinstance(node, ast.Num):  # Python < 3.8
            return node.n
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):  # Python 3.8+
            return int(node.value)
        return None
    
    def _update_workspace_regex(self, code: str, execution_result: Dict[str, Any]):
        """
        Fallback method using regex for file operation detection.
        Used when AST parsing fails or finds nothing.
        
        Args:
            code: Python code that was executed
            execution_result: Result from code execution
        """
        # Detect file reads
        read_patterns = [
            r'read_file\(["\']([^"\']+)["\']\)',
            r'agent_tools\.read_file\(["\']([^"\']+)["\']\)',
        ]
        
        for pattern in read_patterns:
            matches = re.findall(pattern, code)
            for file_path in matches:
                if "files" in execution_result and file_path in execution_result["files"]:
                    self.files[file_path] = execution_result["files"][file_path]
        
        # Detect file writes
        write_patterns = [
            r'write_file\(["\']([^"\']+)["\']\s*,\s*([^)]+)\)',
            r'agent_tools\.write_file\(["\']([^"\']+)["\']\s*,\s*([^)]+)\)',
        ]
        
        for pattern in write_patterns:
            matches = re.findall(pattern, code)
            for match in matches:
                file_path = match[0] if isinstance(match, tuple) else match
                if "files" in execution_result and file_path in execution_result["files"]:
                    self.files[file_path] = execution_result["files"][file_path]
        
        # Detect incremental edits
        edit_patterns = [
            r'apply_incremental_edit\(["\']([^"\']+)["\']',
            r'agent_tools\.apply_incremental_edit\(["\']([^"\']+)["\']',
        ]
        
        for pattern in edit_patterns:
            matches = re.findall(pattern, code)
            for file_path in matches:
                if "files" in execution_result and file_path in execution_result["files"]:
                    self.files[file_path] = execution_result["files"][file_path]
        
        # Detect todo updates
        todo_patterns = [
            r'update_todo\((\d+)\s*,\s*["\']([^"\']+)["\']',
            r'agent_tools\.update_todo\((\d+)\s*,\s*["\']([^"\']+)["\']',
        ]
        
        for pattern in todo_patterns:
            matches = re.findall(pattern, code)
            for match in matches:
                if isinstance(match, tuple):
                    step_number = int(match[0])
                    status = match[1]
                    self._update_todo_step(step_number, status)
    
    def set_plan(self, plan: List[Dict[str, Any]]):
        """
        Set the plan/todo in workspace.
        
        Args:
            plan: List of plan steps
        """
        self.todo = plan
    
    def update_todo_step(self, step_number: int, status: str, result: Optional[str] = None):
        """
        Update a todo step status.
        
        Args:
            step_number: Step number to update
            status: New status
            result: Optional result text
        """
        self._update_todo_step(step_number, status, result)
    
    def _update_todo_step(self, step_number: int, status: str, result: Optional[str] = None):
        """Internal method to update todo step."""
        if not self.todo:
            return
        
        for step in self.todo:
            if step.get("step_number") == step_number:
                step["status"] = status
                if result:
                    step["result"] = result
                break
    
    def get_file(self, file_path: str) -> Optional[str]:
        """
        Get file content from workspace cache.
        
        Args:
            file_path: Path to file
            
        Returns:
            File content or None if not in cache
        """
        return self.files.get(file_path)
    
    def set_file(self, file_path: str, content: str):
        """
        Set file content in workspace.
        
        Args:
            file_path: Path to file
            content: File content
        """
        self.files[file_path] = content
    
    def add_note(self, note: str, category: str = "general"):
        """
        Add a note to workspace.
        
        Args:
            note: Note text
            category: Note category (general, error, observation, etc.)
        """
        from datetime import datetime
        self.notes.append({
            "note": note,
            "category": category,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def get_workspace_state(self) -> str:
        """
        Get current workspace state as string for context.
        
        Returns:
            Formatted workspace state string
        """
        lines = []
        lines.append(f"Workspace State (Incident #{self.incident_id}):")
        lines.append("")
        
        # Files
        if self.files:
            lines.append(f"Files in workspace ({len(self.files)}):")
            for file_path in list(self.files.keys())[:10]:  # Limit to 10 files
                lines.append(f"  - {file_path}")
            if len(self.files) > 10:
                lines.append(f"  ... and {len(self.files) - 10} more files")
            lines.append("")
        
        # Todo/Plan
        if self.todo:
            completed = sum(1 for s in self.todo if s.get("status") == "completed")
            total = len(self.todo)
            lines.append(f"Plan Progress: {completed}/{total} steps completed")
            lines.append("")
        
        # Notes
        if self.notes:
            lines.append(f"Notes ({len(self.notes)}):")
            for note in self.notes[-5:]:  # Last 5 notes
                lines.append(f"  [{note['category']}] {note['note'][:100]}...")
            lines.append("")
        
        return "\n".join(lines)
    
    def get_files_dict(self) -> Dict[str, str]:
        """Get all files in workspace."""
        return self.files.copy()
    
    def clear_files(self):
        """Clear all files from workspace."""
        self.files = {}
    
    def clear_notes(self):
        """Clear all notes."""
        self.notes = []

