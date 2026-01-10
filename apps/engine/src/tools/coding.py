"""
Interactive coding tools for CrewAI agents.
These tools enable Cursor-like code exploration and editing.
"""
from typing import Dict, Any, List, Optional
from src.integrations.github.integration import GithubIntegration
import re
import ast
import json
import os

# Try to import tool decorator - supports different CrewAI versions
try:
    from crewai_tools import tool
except ImportError:
    try:
        from crewai.tools import tool
    except ImportError:
        # Fallback: create a simple decorator
        def tool(description: str = ""):
            def decorator(func):
                func.tool_description = description
                func.is_tool = True
                return func
            return decorator

class CodingToolsContext:
    """Context manager for coding tools - holds GitHub integration and cache."""
    
    def __init__(self, github_integration: GithubIntegration, repo_name: str, ref: str = "main"):
        self.gh = github_integration
        self.repo_name = repo_name
        self.ref = ref
        self._file_cache: Dict[str, str] = {}
        self._import_graph: Dict[str, List[str]] = {}
    
    def get_file_contents(self, file_path: str) -> Optional[str]:
        """Get file contents with caching."""
        cache_key = f"{self.ref}:{file_path}"
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]
        
        content = self.gh.get_file_contents(self.repo_name, file_path, self.ref)
        if content:
            self._file_cache[cache_key] = content
        return content
    
    def _extract_imports(self, content: str, file_path: str) -> Dict[str, List[str]]:
        """Extract imports from code."""
        imports = {"external": [], "internal": [], "relative": []}
        
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            # Python imports
            if line.startswith("import ") or line.startswith("from "):
                if line.startswith("from .") or line.startswith("from .."):
                    imports["relative"].append(line)
                elif "/" in line or "\\" in line:
                    imports["internal"].append(line)
                else:
                    imports["external"].append(line)
            # TypeScript/JavaScript imports
            elif line.startswith("import ") and ("from" in line or "require" in line):
                if "./" in line or "../" in line:
                    imports["relative"].append(line)
                elif "@/" in line or "@/":
                    imports["internal"].append(line)
                else:
                    imports["external"].append(line)
        
        return imports

# Global context - will be set when crew starts
_global_context: Optional[CodingToolsContext] = None

def set_coding_tools_context(context: CodingToolsContext):
    """Set the global context for coding tools."""
    global _global_context
    _global_context = context

def get_context() -> CodingToolsContext:
    """Get the global context."""
    if _global_context is None:
        raise RuntimeError("Coding tools context not set. Call set_coding_tools_context() first.")
    return _global_context

@tool("Read a file from the repository")
def read_file(file_path: str) -> str:
    """
    Read the contents of a file from the repository.
    Always use this before editing files to understand current state.
    
    Args:
        file_path: Path to the file (e.g., "src/app.py" or "app/components/Button.tsx")
        
    Returns:
        File contents as string with file path header
    """
    ctx = get_context()
    content = ctx.get_file_contents(file_path)
    if content:
        return f"File: {file_path}\n```\n{content}\n```"
    return f"Error: Could not read file {file_path}"

@tool("Find where a symbol (function/class/variable) is defined")
def find_symbol_definition(symbol_name: str, current_file: Optional[str] = None) -> str:
    """
    Find where a symbol (function, class, or variable) is defined in the codebase.
    Essential for understanding dependencies before making changes.
    
    Args:
        symbol_name: Name of the symbol to find (e.g., "UserService", "authenticate")
        current_file: Optional current file path to check imports first
        
    Returns:
        Locations where symbol is defined with line numbers
    """
    ctx = get_context()
    results = []
    
    # If we have a current file, check its imports first
    if current_file:
        content = ctx.get_file_contents(current_file)
        if content:
            imports = ctx._extract_imports(content, current_file)
            for imp_list in imports.values():
                for imp in imp_list:
                    if symbol_name in imp:
                        results.append(f"Referenced in {current_file}: {imp}")
    
    # Search codebase
    search_results = ctx.gh.search_code(ctx.repo_name, symbol_name)
    for result in search_results[:5]:
        file_path = result["path"]
        content = ctx.get_file_contents(file_path)
        if content:
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                # Python
                if (f"def {symbol_name}" in line or 
                    f"class {symbol_name}" in line or
                    f"{symbol_name} = " in line):
                    results.append(f"{file_path}:{i} - {line.strip()}")
                # TypeScript/JavaScript
                elif (f"function {symbol_name}" in line or
                      f"class {symbol_name}" in line or
                      f"const {symbol_name}" in line or
                      f"let {symbol_name}" in line or
                      f"export {symbol_name}" in line):
                    results.append(f"{file_path}:{i} - {line.strip()}")
    
    return "\n".join(results) if results else f"Symbol '{symbol_name}' not found"

@tool("Get all imports and dependencies of a file")
def analyze_file_dependencies(file_path: str) -> str:
    """
    Analyze what a file imports and depends on.
    Critical for understanding impact of changes.
    
    Args:
        file_path: Path to the file to analyze
        
    Returns:
        JSON string with import information
    """
    ctx = get_context()
    content = ctx.get_file_contents(file_path)
    if not content:
        return f"Error: Could not read {file_path}"
    
    imports = ctx._extract_imports(content, file_path)
    return json.dumps({
        "file": file_path,
        "external_imports": imports["external"],
        "internal_imports": imports["internal"],
        "relative_imports": imports["relative"],
        "total_imports": len(imports["external"]) + len(imports["internal"]) + len(imports["relative"])
    }, indent=2)

@tool("Find files that import or use a specific file")
def find_file_dependents(file_path: str) -> str:
    """
    Find files that depend on the given file through imports.
    Important for understanding impact of changes.
    
    Args:
        file_path: Path to the file
        
    Returns:
        List of dependent file paths
    """
    ctx = get_context()
    all_files = ctx.gh.get_repo_structure(ctx.repo_name, ref=ctx.ref, max_depth=5)
    dependents = []
    
    # Extract module name from file path
    module_name = file_path.replace("/", ".").replace(".py", "").replace(".ts", "").replace(".tsx", "").replace(".js", "").replace(".jsx", "")
    base_name = os.path.basename(file_path).replace(".py", "").replace(".ts", "").replace(".tsx", "")
    
    for other_file in all_files[:50]:  # Limit search
        if other_file == file_path:
            continue
        content = ctx.get_file_contents(other_file)
        if content:
            # Check if file_path or module_name appears in imports
            imports = ctx._extract_imports(content, other_file)
            all_imports = imports["external"] + imports["internal"] + imports["relative"]
            
            for imp in all_imports:
                if module_name in imp or base_name in imp or file_path in imp:
                    dependents.append(other_file)
                    break
    
    return "\n".join(dependents) if dependents else f"No dependents found for {file_path}"

@tool("Search for code patterns or similar implementations")
def search_code_pattern(pattern: str, language: Optional[str] = None) -> str:
    """
    Search for code patterns, functions, or similar implementations in the codebase.
    Useful for finding similar error handling or patterns to match.
    
    Args:
        pattern: Search query (e.g., "try catch", "error handling", "null check")
        language: Optional language filter (e.g., "python", "typescript")
        
    Returns:
        Matching code snippets with file paths and line numbers
    """
    ctx = get_context()
    results = ctx.gh.search_code(ctx.repo_name, pattern, language)
    formatted = []
    
    for result in results[:10]:
        file_path = result["path"]
        content = ctx.get_file_contents(file_path)
        if content:
            lines = content.split("\n")
            matches = []
            for i, line in enumerate(lines, 1):
                if pattern.lower() in line.lower():
                    matches.append(f"  {i}: {line.strip()}")
            if matches:
                formatted.append(f"{file_path}:\n" + "\n".join(matches[:5]))
    
    return "\n".join(formatted) if formatted else f"No matches for pattern: {pattern}"

@tool("Apply incremental code edits (Cursor-style)")
def apply_incremental_edit(file_path: str, edits: str) -> str:
    """
    Apply incremental edits using Cursor's edit block format or JSON.
    
    Edit block format:
    <<<<<<< ORIGINAL
    old code here
    =======
    new code here
    >>>>>>> UPDATED
    
    Or JSON format:
    {
        "type": "replace",
        "start_line": 42,
        "end_line": 45,
        "content": "new code"
    }
    
    Args:
        file_path: Path to file to edit
        edits: Edit blocks or JSON string with edits
        
    Returns:
        Updated file content
    """
    ctx = get_context()
    content = ctx.get_file_contents(file_path)
    if not content:
        return f"Error: Could not read {file_path}"
    
    lines = content.split("\n")
    
    # Try to parse as edit blocks first
    if "<<<<<<< ORIGINAL" in edits:
        pattern = r'<<<<<<< ORIGINAL\n(.*?)\n=======\n(.*?)\n>>>>>>> UPDATED'
        matches = list(re.finditer(pattern, edits, re.DOTALL))
        
        for match in reversed(matches):  # Process in reverse
            old_code = match.group(1).strip()
            new_code = match.group(2).strip()
            
            old_lines = old_code.split("\n")
            for i in range(len(lines) - len(old_lines) + 1):
                if lines[i:i+len(old_lines)] == old_lines:
                    new_lines = new_code.split("\n")
                    lines = lines[:i] + new_lines + lines[i+len(old_lines):]
                    break
    else:
        # Try JSON format
        try:
            edit_data = json.loads(edits)
            if isinstance(edit_data, list):
                for edit in sorted(edit_data, key=lambda x: x.get("start_line", 0), reverse=True):
                    lines = _apply_single_edit(lines, edit)
            else:
                lines = _apply_single_edit(lines, edit_data)
        except json.JSONDecodeError:
            return f"Error: Invalid edit format. Use edit blocks or JSON."
    
    updated_content = "\n".join(lines)
    cache_key = f"{ctx.ref}:{file_path}"
    ctx._file_cache[cache_key] = updated_content
    return f"✅ Applied edits to {file_path}\n\nUpdated content:\n```\n{updated_content}\n```"

def _apply_single_edit(lines: List[str], edit: Dict) -> List[str]:
    """Apply a single edit operation."""
    edit_type = edit.get("type", "replace")
    start = edit.get("start_line", 1) - 1
    end = edit.get("end_line", start + 1) - 1
    
    if edit_type == "replace":
        new_lines = edit.get("content", "").split("\n")
        return lines[:start] + new_lines + lines[end + 1:]
    elif edit_type == "insert":
        new_lines = edit.get("content", "").split("\n")
        return lines[:start] + new_lines + lines[start:]
    elif edit_type == "delete":
        return lines[:start] + lines[end + 1:]
    return lines

@tool("Validate code syntax and check for errors")
def validate_code(file_path: str, content: Optional[str] = None) -> str:
    """
    Validate code syntax before applying changes.
    Checks for syntax errors and basic correctness.
    
    Args:
        file_path: Path to file
        content: Optional content to validate (if not provided, reads from file)
        
    Returns:
        Validation result message
    """
    ctx = get_context()
    if not content:
        content = ctx.get_file_contents(file_path)
    
    if not content:
        return f"Error: Could not read {file_path}"
    
    # Python syntax check
    if file_path.endswith(".py"):
        try:
            ast.parse(content)
            return f"✅ Python syntax is valid for {file_path}"
        except SyntaxError as e:
            return f"❌ Syntax error in {file_path}: {e.msg} at line {e.lineno}"
    
    # TypeScript/JavaScript basic validation
    elif file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
        # Basic bracket/brace matching
        open_braces = content.count("{") - content.count("}")
        open_brackets = content.count("[") - content.count("]")
        open_parens = content.count("(") - content.count(")")
        
        if open_braces != 0 or open_brackets != 0 or open_parens != 0:
            return f"❌ Unmatched brackets/braces in {file_path} (braces: {open_braces}, brackets: {open_brackets}, parens: {open_parens})"
        return f"✅ Basic syntax check passed for {file_path}"
    
    return f"⚠️  No validator available for {file_path}"

@tool("Get repository file structure")
def get_repo_structure(path: str = "", max_depth: int = 2) -> str:
    """
    Get the directory structure of the repository.
    Useful for understanding codebase layout.
    
    Args:
        path: Starting path (empty for root)
        max_depth: Maximum depth to traverse
        
    Returns:
        List of file paths
    """
    ctx = get_context()
    files = ctx.gh.get_repo_structure(ctx.repo_name, path, ref=ctx.ref, max_depth=max_depth)
    return "\n".join(files) if files else f"No files found in {path}"

@tool("Retrieve memory context for similar past fixes")
def retrieve_memory_context(error_signature: str) -> str:
    """
    Retrieve past errors and fixes from memory for similar incidents.
    
    Args:
        error_signature: Error signature/fingerprint
        
    Returns:
        Memory context with past fixes and errors
    """
    from memory import CodeMemory
    code_memory = CodeMemory()
    memory_data = code_memory.retrieve_context(error_signature)
    
    result = []
    if memory_data.get("known_fixes"):
        result.append("KNOWN FIXES:")
        for i, fix in enumerate(memory_data["known_fixes"][:3], 1):
            result.append(f"Fix #{i}: {fix.get('description', 'No description')}")
    
    if memory_data.get("past_errors"):
        result.append("\nPAST ERROR CONTEXT:")
        for i, err in enumerate(memory_data["past_errors"][:2], 1):
            context = err.get("context", "")[:500]
            result.append(f"Context #{i}: {context}...")
    
    return "\n".join(result) if result else f"No memory context found for error signature: {error_signature}"

