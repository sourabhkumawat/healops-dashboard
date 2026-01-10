"""
QA Review Tools for PR Review Agent

Tools for reviewing pull requests, analyzing code quality, detecting antipatterns,
and providing feedback.
"""
from typing import Dict, Any, Optional, List
import os
import re
from src.integrations.github.integration import GithubIntegration
from src.database.database import SessionLocal
from src.database.models import Integration, AgentEmployee

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


def _get_github_integration(user_id: Optional[int] = None, integration_id: Optional[int] = None) -> Optional[GithubIntegration]:
    """Get GitHub integration instance."""
    db = SessionLocal()
    try:
        if integration_id:
            integration = db.query(Integration).filter(Integration.id == integration_id).first()
        elif user_id:
            # Get the first GitHub integration for the user
            integration = db.query(Integration).filter(
                Integration.user_id == user_id,
                Integration.provider == "GITHUB"
            ).first()
        else:
            return None
        
        if not integration:
            return None
        
        return GithubIntegration(integration_id=integration.id)
    finally:
        db.close()


@tool("Review a pull request and return detailed analysis including files changed and review status")
def review_pr(repo_name: str, pr_number: int, user_id: Optional[int] = None, integration_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Review a pull request and return detailed analysis.
    
    Args:
        repo_name: Repository name in format "owner/repo"
        pr_number: PR number
        user_id: Optional user ID for GitHub integration
        integration_id: Optional integration ID
        
    Returns:
        Dictionary with PR details, files changed, and review analysis
    """
    github = _get_github_integration(user_id, integration_id)
    if not github:
        return {"success": False, "error": "GitHub integration not found"}
    
    result = github.get_pr_details(repo_name, pr_number)
    if result.get("status") != "success":
        return {"success": False, "error": result.get("message", "Failed to get PR details")}
    
    # Extract useful information for review
    review_data = {
        "success": True,
        "pr_number": result.get("pr_number"),
        "title": result.get("title"),
        "body": result.get("body"),
        "author": result.get("author"),
        "state": result.get("state"),
        "files_changed": len(result.get("files", [])),
        "files": []
    }
    
    # Process each file for review
    for file_info in result.get("files", []):
        file_review = {
            "filename": file_info.get("filename"),
            "status": file_info.get("status"),
            "additions": file_info.get("additions"),
            "deletions": file_info.get("deletions"),
            "changes": file_info.get("changes"),
            "patch": file_info.get("patch")  # Unified diff
        }
        review_data["files"].append(file_review)
    
    return review_data


@tool("Get file contents from a PR branch for review")
def get_pr_file_contents(repo_name: str, pr_number: int, file_path: str, user_id: Optional[int] = None, integration_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Get file contents from a PR (from the head branch).
    
    Args:
        repo_name: Repository name in format "owner/repo"
        pr_number: PR number
        file_path: Path to file in repo
        user_id: Optional user ID for GitHub integration
        integration_id: Optional integration ID
        
    Returns:
        Dictionary with file contents
    """
    github = _get_github_integration(user_id, integration_id)
    if not github:
        return {"success": False, "error": "GitHub integration not found"}
    
    content = github.get_pr_file_contents(repo_name, pr_number, file_path)
    if content is None:
        return {"success": False, "error": f"Failed to get file contents: {file_path}"}
    
    return {
        "success": True,
        "file_path": file_path,
        "content": content,
        "lines": len(content.split("\n"))
    }


@tool("Comment on a pull request, optionally as an inline comment on a specific line")
def comment_on_pr(repo_name: str, pr_number: int, body: str, commit_id: Optional[str] = None, path: Optional[str] = None, line: Optional[int] = None, user_id: Optional[int] = None, integration_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Comment on a pull request.
    
    Args:
        repo_name: Repository name in format "owner/repo"
        pr_number: PR number
        body: Comment text
        commit_id: Optional commit SHA for inline comments
        path: Optional file path for inline comments
        line: Optional line number for inline comments
        user_id: Optional user ID for GitHub integration
        integration_id: Optional integration ID
        
    Returns:
        Dictionary with comment details
    """
    github = _get_github_integration(user_id, integration_id)
    if not github:
        return {"success": False, "error": "GitHub integration not found"}
    
    result = github.comment_on_pr(repo_name, pr_number, body, commit_id, path, line)
    if result.get("status") != "success":
        return {"success": False, "error": result.get("message", "Failed to comment on PR")}
    
    return {
        "success": True,
        "comment_id": result.get("comment_id") or result.get("review_id"),
        "type": result.get("type", "general"),
        "message": result.get("message", "Comment created")
    }


@tool("Request changes on a PR, rejecting it and asking for fixes")
def request_pr_changes(repo_name: str, pr_number: int, body: str, user_id: Optional[int] = None, integration_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Request changes on a PR (reject and ask for fixes).
    
    Args:
        repo_name: Repository name in format "owner/repo"
        pr_number: PR number
        body: Review comment body explaining what needs to be fixed
        user_id: Optional user ID for GitHub integration
        integration_id: Optional integration ID
        
    Returns:
        Dictionary with review details
    """
    github = _get_github_integration(user_id, integration_id)
    if not github:
        return {"success": False, "error": "GitHub integration not found"}
    
    result = github.request_pr_changes(repo_name, pr_number, body)
    if result.get("status") != "success":
        return {"success": False, "error": result.get("message", "Failed to request changes")}
    
    return {
        "success": True,
        "review_id": result.get("review_id"),
        "state": result.get("state"),
        "message": result.get("message", "Changes requested")
    }


@tool("Approve a pull request after review")
def approve_pr(repo_name: str, pr_number: int, body: Optional[str] = None, user_id: Optional[int] = None, integration_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Approve a pull request.
    
    Args:
        repo_name: Repository name in format "owner/repo"
        pr_number: PR number
        body: Optional approval comment
        user_id: Optional user ID for GitHub integration
        integration_id: Optional integration ID
        
    Returns:
        Dictionary with review details
    """
    github = _get_github_integration(user_id, integration_id)
    if not github:
        return {"success": False, "error": "GitHub integration not found"}
    
    result = github.approve_pr(repo_name, pr_number, body)
    if result.get("status") != "success":
        return {"success": False, "error": result.get("message", "Failed to approve PR")}
    
    return {
        "success": True,
        "review_id": result.get("review_id"),
        "state": result.get("state"),
        "message": result.get("message", "PR approved")
    }


@tool("Analyze code quality, detect issues like long functions, deep nesting, magic numbers")
def analyze_code_quality(code: str, file_path: str) -> Dict[str, Any]:
    """
    Analyze code quality and detect common issues.
    
    This is a basic implementation. In production, you'd use more sophisticated tools.
    
    Args:
        code: Code content to analyze
        file_path: File path for context
        
    Returns:
        Dictionary with code quality analysis
    """
    issues = []
    suggestions = []
    
    lines = code.split("\n")
    
    # Check for long functions/methods
    function_line_counts = []
    current_function_start = None
    brace_count = 0
    
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        
        # Detect function definitions (Python, JavaScript, etc.)
        if re.search(r'^\s*def\s+\w+|^\s*function\s+\w+|^\s*const\s+\w+\s*=\s*\(|^\s*public\s+\w+\s+\w+\s*\(',
                     stripped):
            if current_function_start:
                length = i - current_function_start
                if length > 50:
                    issues.append({
                        "type": "long_function",
                        "file": file_path,
                        "line": current_function_start,
                        "severity": "warning",
                        "message": f"Function is {length} lines long (recommended: <50 lines)",
                        "suggestion": "Consider breaking into smaller functions"
                    })
            current_function_start = i
            brace_count = 0
        
        # Check nesting depth
        open_braces = stripped.count("{") + stripped.count("(") + (1 if stripped.endswith(":") else 0)
        close_braces = stripped.count("}") + stripped.count(")")
        brace_count += open_braces - close_braces
        
        if brace_count > 4:
            issues.append({
                "type": "deep_nesting",
                "file": file_path,
                "line": i,
                "severity": "warning",
                "message": f"Nesting depth is {brace_count} levels (recommended: <4 levels)",
                "suggestion": "Consider using guard clauses or extracting functions"
            })
        
        # Check for magic numbers
        numbers = re.findall(r'\b\d{3,}\b', stripped)  # Numbers with 3+ digits
        if numbers and not any(keyword in stripped.lower() for keyword in ['id', 'code', 'http', 'port', 'timeout']):
            for num in numbers:
                issues.append({
                    "type": "magic_number",
                    "file": file_path,
                    "line": i,
                    "severity": "suggestion",
                    "message": f"Magic number detected: {num}",
                    "suggestion": "Consider extracting to a named constant"
                })
        
        # Check for TODO/FIXME comments
        if re.search(r'TODO|FIXME|XXX|HACK', stripped, re.IGNORECASE):
            suggestions.append({
                "type": "todo_comment",
                "file": file_path,
                "line": i,
                "severity": "info",
                "message": "TODO/FIXME comment found",
                "suggestion": "Consider resolving before merging"
            })
    
    # Finalize any open function
    if current_function_start and len(lines) - current_function_start > 50:
        issues.append({
            "type": "long_function",
            "file": file_path,
            "line": current_function_start,
            "severity": "warning",
            "message": f"Function is {len(lines) - current_function_start} lines long",
            "suggestion": "Consider breaking into smaller functions"
        })
    
    return {
        "success": True,
        "file_path": file_path,
        "total_lines": len(lines),
        "issues": issues,
        "suggestions": suggestions,
        "issue_count": len(issues),
        "suggestion_count": len(suggestions)
    }


@tool("Check for common antipatterns like duplicate code, god objects, too many parameters")
def check_antipatterns(code: str, file_path: str) -> Dict[str, Any]:
    """
    Check for common antipatterns in code.
    
    Args:
        code: Code content to check
        file_path: File path for context
        
    Returns:
        Dictionary with antipatterns detected
    """
    antipatterns = []
    lines = code.split("\n")
    
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        
        # Check for god object indicators (very long class definitions)
        if re.search(r'^\s*class\s+\w+', stripped):
            # This is simplified - in production, you'd track class size
            pass
        
        # Check for copy-paste code patterns (repeated blocks)
        if i > 5:
            # Check if current block matches previous blocks (simplified)
            current_block = "\n".join(lines[max(0, i-3):i])
            for j in range(max(0, i-20), max(0, i-5)):
                previous_block = "\n".join(lines[j:j+3])
                if current_block == previous_block and len(current_block.strip()) > 20:
                    antipatterns.append({
                        "type": "duplicate_code",
                        "file": file_path,
                        "line": i,
                        "severity": "warning",
                        "message": "Potential duplicate code detected",
                        "suggestion": "Consider extracting to a function"
                    })
                    break
        
        # Check for feature envy (accessing too many methods of another class)
        # This is simplified - full implementation would track method calls
        
        # Check for primitive obsession (too many primitive parameters)
        if re.search(r'def\s+\w+\s*\([^)]*,\s*[^)]*,\s*[^)]*,\s*[^)]*,\s*[^)]*\)', stripped):
            antipatterns.append({
                "type": "too_many_parameters",
                "file": file_path,
                "line": i,
                "severity": "warning",
                "message": "Function has many parameters (>4)",
                "suggestion": "Consider using an object/struct to group related parameters"
            })
    
    return {
        "success": True,
        "file_path": file_path,
        "antipatterns": antipatterns,
        "count": len(antipatterns)
    }


@tool("Validate that a solution addresses the root cause based on error logs")
def validate_solution(solution_code: str, error_logs: str, root_cause: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate that a solution actually addresses the root cause based on logs.
    
    This is a simplified implementation. In production, you'd use more sophisticated analysis.
    
    Args:
        solution_code: The proposed solution code
        error_logs: Error logs or context
        root_cause: Optional root cause analysis
        
    Returns:
        Dictionary with validation results
    """
    validation = {
        "success": True,
        "matches_logs": False,
        "matches_root_cause": False,
        "issues": []
    }
    
    # Extract error keywords from logs
    error_keywords = []
    if error_logs:
        # Look for common error patterns
        error_patterns = [
            r'Error:\s*(\w+)',
            r'Exception:\s*(\w+)',
            r'TypeError|ValueError|AttributeError|KeyError|IndexError',
            r'null pointer|undefined|null|NoneType'
        ]
        for pattern in error_patterns:
            matches = re.findall(pattern, error_logs, re.IGNORECASE)
            error_keywords.extend(matches)
    
    # Check if solution addresses error keywords
    if error_keywords:
        found_keywords = []
        for keyword in error_keywords:
            if keyword.lower() in solution_code.lower():
                found_keywords.append(keyword)
        
        if found_keywords:
            validation["matches_logs"] = True
        else:
            validation["issues"].append({
                "type": "log_mismatch",
                "severity": "warning",
                "message": f"Solution doesn't appear to address error keywords: {error_keywords[:3]}",
                "suggestion": "Review solution against error logs"
            })
    
    # Check if solution addresses root cause
    if root_cause:
        # Simple keyword matching (production would use better NLP)
        root_cause_lower = root_cause.lower()
        solution_lower = solution_code.lower()
        
        # Extract key terms from root cause
        key_terms = re.findall(r'\b\w{5,}\b', root_cause_lower)  # Words with 5+ chars
        
        matching_terms = [term for term in key_terms[:5] if term in solution_lower]
        
        if len(matching_terms) >= 2:  # At least 2 key terms should match
            validation["matches_root_cause"] = True
        else:
            validation["issues"].append({
                "type": "root_cause_mismatch",
                "severity": "warning",
                "message": "Solution may not fully address root cause",
                "suggestion": "Verify solution matches root cause analysis"
            })
    
    return validation
