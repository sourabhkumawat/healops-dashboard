"""
Enhanced crew orchestration for three-phase autonomous code fixing.
Coordinates Exploration → Fix Generation → Validation & Decision phases.

Features:
- Interactive code exploration (Cursor-like)
- Multi-agent collaboration
- Feedback loop: Validation errors are sent back to code fixer for correction
- Automatic retry (up to 3 attempts per fix)
- Confidence-based decision making
"""
from crewai import Crew, Task
from typing import Dict, Any, List, Optional, Tuple
from enhanced_agents import create_all_enhanced_agents
from coding_tools import set_coding_tools_context, CodingToolsContext
from integrations.github_integration import GithubIntegration
from confidence_scoring import ConfidenceScorer, compare_fixes
from memory import CodeMemory
from ai_analysis import get_incident_fingerprint
from models import Incident, LogEntry
from sqlalchemy.orm import Session

def run_enhanced_crew(
    incident: Incident,
    logs: List[LogEntry],
    root_cause: str,
    github_integration: GithubIntegration,
    repo_name: str,
    db: Session
) -> Dict[str, Any]:
    """
    Run the enhanced three-phase crew for autonomous code fixing.
    
    Args:
        incident: The incident to fix
        logs: Related log entries
        root_cause: Root cause analysis from RCA agent
        github_integration: GitHub integration instance
        repo_name: Repository name in format "owner/repo"
        db: Database session
        
    Returns:
        Result dictionary with fixes, confidence scores, and decision
    """
    # Set up coding tools context
    tools_context = CodingToolsContext(github_integration, repo_name)
    set_coding_tools_context(tools_context)
    
    # Get error signature for memory
    error_signature = get_incident_fingerprint(incident, logs)
    code_memory = CodeMemory()
    
    # Create all agents
    agents = create_all_enhanced_agents()
    
    # Extract file paths from logs (simplified - would use actual extraction)
    file_paths = _extract_file_paths_from_logs(logs)
    
    # ========================================================================
    # Phase 1: Exploration Crew
    # ========================================================================
    print("🔍 Phase 1: Exploration")
    
    explore_task = Task(
        description=f"""
        Explore the codebase for incident #{incident.id}:
        - Service: {incident.service_name}
        - Root Cause: {root_cause}
        - File paths from logs: {', '.join(file_paths[:5]) if file_paths else 'None'}
        
        Your tasks:
        1. Read the affected files
        2. Map the codebase structure
        3. Analyze dependencies
        4. Find similar patterns
        """,
        agent=agents["exploration"]["codebase_explorer"],
        expected_output="List of relevant files, codebase structure, and initial context"
    )
    
    dependency_task = Task(
        description=f"""
        Analyze dependencies for the affected files.
        Build a dependency graph showing what imports what.
        """,
        agent=agents["exploration"]["dependency_analyzer"],
        context=[explore_task],
        expected_output="Dependency graph and dependent files list"
    )
    
    pattern_task = Task(
        description=f"""
        Find similar patterns and check memory for past fixes.
        Error signature: {error_signature}
        """,
        agent=agents["exploration"]["pattern_matcher"],
        context=[explore_task],
        expected_output="Similar patterns found and past fixes from memory"
    )
    
    exploration_crew = Crew(
        agents=[
            agents["exploration"]["codebase_explorer"],
            agents["exploration"]["dependency_analyzer"],
            agents["exploration"]["pattern_matcher"]
        ],
        tasks=[explore_task, dependency_task, pattern_task],
        verbose=2
    )
    
    exploration_result = exploration_crew.kickoff()
    print(f"✅ Exploration complete: {str(exploration_result)[:200]}...")
    
    # ========================================================================
    # Phase 2: Fix Generation Crew
    # ========================================================================
    print("🔧 Phase 2: Fix Generation")
    
    strategy_task = Task(
        description=f"""
        Plan fix strategies for the root cause: {root_cause}
        
        Generate 2-3 different fix approaches:
        1. Conservative: Minimal change, low risk
        2. Comprehensive: Addresses root cause, medium risk
        3. Pattern-based: Matches codebase style, low risk
        """,
        agent=agents["fix_generation"]["fix_strategist"],
        context=[explore_task, dependency_task, pattern_task],
        expected_output="2-3 fix strategies with approach descriptions and risk levels"
    )
    
    primary_fix_task = Task(
        description=f"""
        Generate the PRIMARY fix using the best strategy.
        Root cause: {root_cause}
        
        IMPORTANT:
        - Use incremental edits (edit blocks), not full file regeneration
        - Read files before editing
        - Trace symbols to definitions
        - Validate after editing
        """,
        agent=agents["fix_generation"]["code_fixer_primary"],
        context=[strategy_task],
        expected_output="Primary fix with edit blocks and file paths"
    )
    
    alternative_fix_task = Task(
        description=f"""
        Generate an ALTERNATIVE fix using a different approach.
        Root cause: {root_cause}
        
        Use a different strategy than the primary fix.
        """,
        agent=agents["fix_generation"]["code_fixer_alternative"],
        context=[strategy_task],
        expected_output="Alternative fix with edit blocks and file paths"
    )
    
    scoring_task = Task(
        description=f"""
        Score both fixes for confidence, risk, and quality.
        Compare the primary and alternative fixes.
        """,
        agent=agents["fix_generation"]["confidence_scorer"],
        context=[primary_fix_task, alternative_fix_task],
        expected_output="Confidence scores for both fixes with ranking"
    )
    
    fix_generation_crew = Crew(
        agents=[
            agents["fix_generation"]["fix_strategist"],
            agents["fix_generation"]["code_fixer_primary"],
            agents["fix_generation"]["code_fixer_alternative"],
            agents["fix_generation"]["confidence_scorer"]
        ],
        tasks=[strategy_task, primary_fix_task, alternative_fix_task, scoring_task],
        verbose=2
    )
    
    fix_generation_result = fix_generation_crew.kickoff()
    print(f"✅ Fix generation complete: {str(fix_generation_result)[:200]}...")
    
    # Extract fixes from results (would need parsing in real implementation)
    # For now, we'll use the tools context cache
    fixes = _extract_fixes_from_context(tools_context)
    
    # ========================================================================
    # Phase 3: Validation & Decision Crew with Feedback Loop
    # ========================================================================
    print("✅ Phase 3: Validation & Decision (with Feedback Loop)")
    
    MAX_RETRIES = 3
    validation_results = {}
    impact_results = {}
    pattern_results = {}
    total_retries_used = 0
    
    # Validate each fix with retry loop
    for fix_name, fix_data in fixes.items():
        retry_count = 0
        validation_passed = False
        current_fix = fix_data
        
        while retry_count < MAX_RETRIES and not validation_passed:
            iteration_label = f" (Attempt {retry_count + 1}/{MAX_RETRIES})" if retry_count > 0 else ""
            print(f"🔍 Validating fix: {fix_name}{iteration_label}")
            
            # Validate syntax
            syntax_task = Task(
                description=f"""
                Validate syntax for fix: {fix_name}
                Check all modified files for syntax errors.
                {f"Previous attempt had errors - please verify they are fixed." if retry_count > 0 else ""}
                """,
                agent=agents["validation"]["syntax_validator"],
                expected_output="Syntax validation results with specific error messages if any"
            )
            
            # Validate impact
            impact_task = Task(
                description=f"""
                Analyze impact for fix: {fix_name}
                Check for breaking changes and dependent files.
                """,
                agent=agents["validation"]["impact_analyzer"],
                context=[syntax_task],
                expected_output="Impact analysis with breaking changes and dependent files"
            )
            
            # Validate pattern consistency
            pattern_task = Task(
                description=f"""
                Validate pattern consistency for fix: {fix_name}
                Compare with codebase patterns and past fixes.
                """,
                agent=agents["validation"]["pattern_consistency_validator"],
                context=[syntax_task],
                expected_output="Pattern consistency score and recommendations"
            )
            
            validation_crew = Crew(
                agents=[
                    agents["validation"]["syntax_validator"],
                    agents["validation"]["impact_analyzer"],
                    agents["validation"]["pattern_consistency_validator"]
                ],
                tasks=[syntax_task, impact_task, pattern_task],
                verbose=2
            )
            
            validation_result = validation_crew.kickoff()
            validation_results[fix_name] = validation_result
            
            # Check if validation passed
            validation_passed, validation_errors = _check_validation_passed(validation_result, fix_name, tools_context)
            
            if validation_passed:
                print(f"✅ Validation passed for {fix_name}")
                total_retries_used += retry_count
                break
            
            # If validation failed and we have retries left, send errors back to fixer
            if retry_count < MAX_RETRIES - 1:
                retry_count += 1
                total_retries_used += 1
                print(f"🔄 Retry {retry_count}/{MAX_RETRIES}: Validation failed, sending errors back to code fixer...")
                print(f"   Errors found: {validation_errors[:200]}...")
                
                # Extract validation errors for feedback
                error_summary = _extract_validation_errors(validation_result, validation_errors)
                
                # Create retry task for code fixer
                retry_task = Task(
                    description=f"""
                    Previous fix failed validation. Please correct the following errors:
                    
                    {error_summary}
                    
                    File: {fix_name}
                    Current fix content: {str(current_fix.get('content', ''))[:1000]}...
                    
                    IMPORTANT:
                    1. Read the file again to see current state
                    2. Fix the specific validation errors mentioned above
                    3. Use apply_incremental_edit to make corrections
                    4. Validate again after fixing
                    
                    Focus on fixing the validation errors while preserving the original fix intent.
                    """,
                    agent=agents["fix_generation"]["code_fixer_primary"],
                    expected_output="Corrected fix addressing all validation errors"
                )
                
                # Regenerate fix with error feedback
                retry_crew = Crew(
                    agents=[agents["fix_generation"]["code_fixer_primary"]],
                    tasks=[retry_task],
                    verbose=2
                )
                
                retry_result = retry_crew.kickoff()
                print(f"🔄 Regenerated fix for {fix_name} based on validation feedback")
                
                # Update fix with corrected version
                # The fix should be in the tools context cache after apply_incremental_edit
                updated_content = tools_context.get_file_contents(fix_name)
                if updated_content:
                    current_fix["content"] = updated_content
                    fixes[fix_name] = current_fix
            else:
                print(f"⚠️  Max retries reached for {fix_name}. Validation still failing.")
                break
    
    # Decision making
    decision_task = Task(
        description=f"""
        Make final decision based on all validation results.
        
        Validation results: {str(validation_results)[:500]}
        
        Decision thresholds:
        - High confidence (90%+): Create PR automatically
        - Medium confidence (70-90%): Create PR with warnings
        - Low confidence (<70%): Create DRAFT PR - changes available on incident page for review
        """,
        agent=agents["validation"]["decision_maker"],
        context=list(validation_results.values()),
        expected_output="Final decision with action, confidence score, and reasoning"
    )
    
    decision_crew = Crew(
        agents=[agents["validation"]["decision_maker"]],
        tasks=[decision_task],
        verbose=2
    )
    
    decision_result = decision_crew.kickoff()
    print(f"✅ Decision complete: {str(decision_result)[:200]}...")
    
    # ========================================================================
    # Process Results
    # ========================================================================
    
    # Use confidence scorer to calculate final scores
    scorer = ConfidenceScorer()
    
    # Build fix data for scoring
    scored_fixes = []
    for fix_name, fix_data in fixes.items():
        # This would be populated from actual validation results
        # For now, using placeholder structure
        scored_fixes.append({
            "fix_data": fix_data,
            "validation": validation_results.get(fix_name, {}),
            "impact": {},
            "pattern": {},
            "error_signature": error_signature
        })
    
    # Compare and rank fixes
    ranked_fixes = compare_fixes(scored_fixes, scorer)
    
    # Get best fix
    best_fix = ranked_fixes[0] if ranked_fixes else None
    
    # Make final decision - check actual validation status
    if best_fix:
        # Check if validation actually passed
        best_fix_name = list(fixes.keys())[0] if fixes else None
        actual_validation_passed = False
        if best_fix_name:
            validation_result_str = str(validation_results.get(best_fix_name, ""))
            # Check if validation result contains success indicators
            actual_validation_passed = (
                "✅" in validation_result_str or 
                "valid" in validation_result_str.lower() or
                "passed" in validation_result_str.lower()
            ) and "❌" not in validation_result_str
        
        decision = scorer.make_decision(
            confidence_score=best_fix["confidence"]["overall_confidence"],
            risk_level=best_fix["confidence"]["risk_level"],
            validation_passed=actual_validation_passed
        )
    else:
        decision = {
            "action": "SKIP_PR",
            "reasoning": "No valid fixes generated - cannot create PR",
            "warnings": ["No fixes could be generated by the agents"]
        }
    
    return {
        "status": "success",
        "exploration": str(exploration_result),
        "fixes": fixes,
        "ranked_fixes": ranked_fixes,
        "best_fix": best_fix,
        "decision": decision,
        "validation_results": validation_results,
        "retry_info": {
            "max_retries": MAX_RETRIES,
            "retries_used": total_retries_used
        }
    }

def _extract_file_paths_from_logs(logs: List[LogEntry]) -> List[str]:
    """Extract file paths from log entries."""
    file_paths = []
    for log in logs:
        if log.metadata_json and isinstance(log.metadata_json, dict):
            # Extract file paths from metadata (stack traces, etc.)
            # This is simplified - would need actual extraction logic
            if "file" in log.metadata_json:
                file_paths.append(log.metadata_json["file"])
            if "filename" in log.metadata_json:
                file_paths.append(log.metadata_json["filename"])
    return list(set(file_paths))  # Remove duplicates

def _extract_fixes_from_context(tools_context: CodingToolsContext) -> Dict[str, Any]:
    """Extract fixes from tools context cache."""
    fixes = {}
    
    # Get all cached files (these are the modified files)
    for cache_key, content in tools_context._file_cache.items():
        if ":" in cache_key:
            _, file_path = cache_key.split(":", 1)
            fixes[file_path] = {
                "file_path": file_path,
                "content": content,
                "type": "full_content"  # Would be "incremental_edit" in real implementation
            }
    
    return fixes

def _check_validation_passed(validation_result: Any, fix_name: str, tools_context: CodingToolsContext) -> Tuple[bool, str]:
    """
    Check if validation passed and extract errors.
    
    Returns:
        (validation_passed: bool, errors: str)
    """
    validation_str = str(validation_result)
    
    # Check for validation failure indicators
    has_errors = (
        "❌" in validation_str or
        "error" in validation_str.lower() or
        "failed" in validation_str.lower() or
        "invalid" in validation_str.lower()
    ) and "✅" not in validation_str
    
    # Also validate using the validate_code tool directly
    try:
        from coding_tools import validate_code
        # Get current fix content
        fix_content = tools_context.get_file_contents(fix_name)
        if fix_content:
            tool_validation = validate_code(fix_name, fix_content)
            if "❌" in tool_validation or "error" in tool_validation.lower():
                has_errors = True
                validation_str += f"\nTool Validation: {tool_validation}"
    except Exception as e:
        print(f"⚠️  Error in direct validation: {e}")
    
    validation_passed = not has_errors
    errors = validation_str if has_errors else ""
    
    return validation_passed, errors

def _extract_validation_errors(validation_result: Any, validation_errors: str) -> str:
    """
    Extract and format validation errors for feedback to code fixer.
    
    Args:
        validation_result: Validation result from crew
        validation_errors: Error string from validation
        
    Returns:
        Formatted error summary
    """
    error_summary = []
    error_summary.append("VALIDATION ERRORS FOUND:")
    error_summary.append("=" * 50)
    
    # Extract specific error messages
    validation_str = str(validation_result) + "\n" + validation_errors
    
    # Look for syntax errors
    if "Syntax error" in validation_str or "syntax" in validation_str.lower():
        error_summary.append("❌ Syntax Errors:")
        # Extract line numbers if available
        import re
        syntax_errors = re.findall(r'Syntax error.*?line \d+', validation_str, re.IGNORECASE)
        for err in syntax_errors[:5]:  # Limit to 5 errors
            error_summary.append(f"  - {err}")
    
    # Look for unmatched brackets
    if "Unmatched" in validation_str or "bracket" in validation_str.lower():
        error_summary.append("❌ Structure Errors:")
        unmatched = re.findall(r'Unmatched.*?\(.*?\)', validation_str)
        for err in unmatched[:3]:
            error_summary.append(f"  - {err}")
    
    # Look for validation failure messages
    if "❌" in validation_str:
        error_lines = [line for line in validation_str.split("\n") if "❌" in line]
        error_summary.append("❌ Validation Failures:")
        for err in error_lines[:5]:
            error_summary.append(f"  - {err.strip()}")
    
    # If no specific errors found, include the full validation result
    if len(error_summary) == 2:  # Only header and separator
        error_summary.append("Full validation result:")
        error_summary.append(validation_str[:1000])  # Limit length
    
    return "\n".join(error_summary)

