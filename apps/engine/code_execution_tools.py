"""
Code Execution Tools - Standardized tool functions for CodeAct code generation.
These functions can be imported and used in agent-generated Python code.

Provides safe, standardized functions that agents can call through generated code,
including file operations, code validation, memory retrieval, and plan updates.
"""
from typing import Dict, Any, Optional, List
import re
import ast
import json

# Global context - set by agent_orchestrator
_agent_tools_context: Optional[Dict[str, Any]] = None

def set_agent_tools_context(context: Dict[str, Any]):
    """
    Set the global context for agent tools.
    
    Args:
        context: Dictionary with 'github_integration', 'repo_name', 'workspace', 'ref'
    """
    global _agent_tools_context
    _agent_tools_context = context

def get_context() -> Dict[str, Any]:
    """Get the global context."""
    if _agent_tools_context is None:
        raise RuntimeError("Agent tools context not set. Call set_agent_tools_context() first.")
    return _agent_tools_context

def read_file(file_path: str) -> Dict[str, Any]:
    """
    Read a file from the repository.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Dictionary with success, content, error
    """
    try:
        ctx = get_context()
        gh = ctx.get("github_integration")
        repo_name = ctx.get("repo_name")
        ref = ctx.get("ref", "main")
        workspace = ctx.get("workspace")
        
        if not gh or not repo_name:
            return {"success": False, "error": "GitHub integration not available"}
        
        # Read file
        content = gh.get_file_contents(repo_name, file_path, ref)
        
        if content is None:
            return {"success": False, "error": f"File not found: {file_path}"}
        
        # Cache in workspace
        if workspace:
            workspace.set_file(file_path, content)
        
        return {
            "success": True,
            "content": content,
            "file_path": file_path,
            "lines": len(content.split("\n"))
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def write_file(file_path: str, content: str) -> Dict[str, Any]:
    """
    Write a file to the repository (in workspace, not committed yet).
    
    Args:
        file_path: Path to the file
        content: Content to write
        
    Returns:
        Dictionary with success, message, error
    """
    try:
        ctx = get_context()
        workspace = ctx.get("workspace")
        
        if not workspace:
            return {"success": False, "error": "Workspace not available"}
        
        # Store in workspace
        workspace.set_file(file_path, content)
        
        return {
            "success": True,
            "message": f"File {file_path} written to workspace",
            "file_path": file_path,
            "lines": len(content.split("\n"))
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def apply_incremental_edit(file_path: str, edits: str) -> Dict[str, Any]:
    """
    Apply incremental edits to a file.
    
    Args:
        file_path: Path to the file
        edits: Edit blocks in format: "```python:file_path\n# ... existing code ...\n# new code\n```"
        
    Returns:
        Dictionary with success, updated_content, error
    """
    try:
        ctx = get_context()
        workspace = ctx.get("workspace")
        
        if not workspace:
            return {"success": False, "error": "Workspace not available"}
        
        # Get current file content
        current_content = workspace.get_file(file_path)
        if current_content is None:
            # Try to read from GitHub
            gh = ctx.get("github_integration")
            repo_name = ctx.get("repo_name")
            ref = ctx.get("ref", "main")
            if gh and repo_name:
                current_content = gh.get_file_contents(repo_name, file_path, ref)
                if current_content:
                    workspace.set_file(file_path, current_content)
        
        if current_content is None:
            return {"success": False, "error": f"File not found in workspace: {file_path}"}
        
        # Parse edit blocks
        updated_content = _apply_edit_blocks(current_content, edits, file_path)
        
        # Update workspace
        workspace.set_file(file_path, updated_content)
        
        return {
            "success": True,
            "message": f"Applied edits to {file_path}",
            "file_path": file_path,
            "updated_content": updated_content[:500] + "..." if len(updated_content) > 500 else updated_content
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def validate_code(file_path: str, content: str = None) -> Dict[str, Any]:
    """
    Validate code syntax.
    
    Args:
        file_path: Path to the file
        content: Optional content to validate (if not provided, uses workspace)
        
    Returns:
        Dictionary with success, errors, warnings
    """
    try:
        ctx = get_context()
        workspace = ctx.get("workspace")
        
        # Get content
        if content is None:
            if workspace:
                content = workspace.get_file(file_path)
            if content is None:
                return {"success": False, "error": f"File not found: {file_path}"}
        
        # Determine file type
        file_ext = file_path.split(".")[-1].lower()
        
        errors = []
        warnings = []
        
        if file_ext in ["py", "python"]:
            # Python syntax check
            try:
                ast.parse(content)
            except SyntaxError as e:
                errors.append({
                    "line": e.lineno,
                    "column": e.offset,
                    "message": e.msg,
                    "text": e.text
                })
        elif file_ext in ["js", "jsx", "ts", "tsx"]:
            # JavaScript/TypeScript - basic check (would need proper parser)
            # For now, just check for basic syntax issues
            if content.count("{") != content.count("}"):
                errors.append({
                    "line": 0,
                    "message": "Mismatched braces"
                })
            if content.count("(") != content.count(")"):
                errors.append({
                    "line": 0,
                    "message": "Mismatched parentheses"
                })
        
        return {
            "success": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "file_path": file_path
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def find_symbol_definition(symbol: str, current_file: str = None) -> Dict[str, Any]:
    """
    Find definition of a symbol.
    
    Args:
        symbol: Symbol name to find
        current_file: Optional current file path for context
        
    Returns:
        Dictionary with success, definitions, error
    """
    try:
        ctx = get_context()
        gh = ctx.get("github_integration")
        repo_name = ctx.get("repo_name")
        ref = ctx.get("ref", "main")
        
        if not gh or not repo_name:
            return {"success": False, "error": "GitHub integration not available"}
        
        # Search for symbol in codebase
        # This is a simplified version - would need proper symbol resolution
        # For now, search for the symbol in files
        definitions = []
        
        # Try to search in current file first
        if current_file:
            content = gh.get_file_contents(repo_name, current_file, ref)
            if content:
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    if re.search(rf'\b{re.escape(symbol)}\s*=', line) or \
                       re.search(rf'def\s+{re.escape(symbol)}', line) or \
                       re.search(rf'class\s+{re.escape(symbol)}', line):
                        definitions.append({
                            "file_path": current_file,
                            "line": i,
                            "content": line.strip()
                        })
        
        return {
            "success": True,
            "definitions": definitions,
            "symbol": symbol
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def update_todo(step_number: int, status: str, result: str = None) -> Dict[str, Any]:
    """
    Update plan progress.
    
    Args:
        step_number: Step number to update
        status: New status (pending, in_progress, completed, failed)
        result: Optional result text
        
    Returns:
        Dictionary with success, message
    """
    try:
        ctx = get_context()
        workspace = ctx.get("workspace")
        
        if not workspace:
            return {"success": False, "error": "Workspace not available"}
        
        workspace.update_todo_step(step_number, status, result)
        
        return {
            "success": True,
            "message": f"Updated step {step_number} to {status}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def retrieve_memory(error_signature: str) -> Dict[str, Any]:
    """
    Retrieve past fixes from CodeMemory.
    
    Args:
        error_signature: Error signature to look up
        
    Returns:
        Dictionary with success, past_errors, known_fixes
    """
    try:
        ctx = get_context()
        memory = ctx.get("memory")
        
        if not memory:
            return {"success": False, "error": "Memory not available"}
        
        result = memory.retrieve_context(error_signature)
        
        return {
            "success": True,
            "past_errors": result.get("past_errors", []),
            "known_fixes": result.get("known_fixes", [])
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def _apply_edit_blocks(current_content: str, edits: str, file_path: str) -> str:
    """
    Apply edit blocks to file content.
    
    Args:
        current_content: Current file content
        edits: Edit blocks string
        file_path: File path for context
        
    Returns:
        Updated content
    """
    # Parse edit blocks
    # Format: ```python:file_path\n# ... existing code ...\n# new code\n```
    lines = current_content.split("\n")
    
    # Find edit blocks
    edit_pattern = r'```(?:\w+)?:?' + re.escape(file_path) + r'?\n(.*?)```'
    matches = re.finditer(edit_pattern, edits, re.DOTALL)
    
    for match in matches:
        edit_content = match.group(1)
        # Parse edit content to find line ranges and new content
        # This is simplified - would need proper edit block parsing
        # For now, try to find line markers like "# Line 10-15" or similar
        
        # Simple approach: if edit contains "# ... existing code ...", it's a replacement
        if "# ... existing code ..." in edit_content or "# existing code" in edit_content:
            # Extract new code after the marker
            parts = edit_content.split("# ... existing code ...")
            if len(parts) == 2:
                # Replace section
                new_code = parts[1].strip()
                # Find matching section in current content (simplified)
                # In practice, would need more sophisticated matching
                lines = current_content.split("\n")
                # For now, append new code (this is a simplified implementation)
                return current_content + "\n" + new_code
        else:
            # Append new code
            return current_content + "\n" + edit_content.strip()
    
    # If no edit blocks found, return original
    return current_content

