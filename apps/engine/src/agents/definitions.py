"""
Enhanced specialized agents for Cursor-like autonomous code fixing.
Organized by phase: Exploration, Fix Generation, Validation & Decision.
"""
from crewai import Agent, LLM
import os
from typing import List, Optional
from src.agents.prompts import (
    CODEBASE_EXPLORER_PROMPT,
    DEPENDENCY_ANALYZER_PROMPT,
    PATTERN_MATCHER_PROMPT,
    FIX_STRATEGIST_PROMPT,
    CODE_FIXER_PROMPT,
    CONFIDENCE_SCORER_PROMPT,
    SYNTAX_VALIDATOR_PROMPT,
    IMPACT_ANALYZER_PROMPT,
    PATTERN_CONSISTENCY_VALIDATOR_PROMPT,
    DECISION_MAKER_PROMPT,
    QA_REVIEWER_PROMPT
)
from src.config.prompts import CODING_AGENT_PROMPT, RCA_AGENT_PROMPT
from src.tools.coding import (
    read_file,
    find_symbol_definition,
    analyze_file_dependencies,
    find_file_dependents,
    search_code_pattern,
    apply_incremental_edit,
    validate_code,
    get_repo_structure,
    retrieve_memory_context
)
from src.tools.qa_review import (
    review_pr,
    get_pr_file_contents,
    comment_on_pr,
    request_pr_changes,
    approve_pr,
    analyze_code_quality,
    check_antipatterns,
    validate_solution
)

# LLM Configuration
from src.core.openrouter_client import get_api_key
api_key = get_api_key()
base_url = "https://openrouter.ai/api/v1"

# Cost-optimized LLMs
# Initialize with error handling to avoid import-time failures
try:
    if not api_key:
        print("⚠️  Warning: OPENCOUNCIL_API not set. LLMs will not be initialized.")
        flash_llm = None
        coding_llm = None
    else:
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
        print("✅ LLMs initialized successfully in agent_definitions")
except ImportError as import_err:
    if "LiteLLM" in str(import_err) or "litellm" in str(import_err).lower():
        print(f"❌ Error: LiteLLM is required but not available. Please install it: pip install litellm")
        print(f"   Error details: {import_err}")
        flash_llm = None
        coding_llm = None
    else:
        raise
except Exception as e:
    print(f"⚠️  Warning: Failed to initialize LLMs in agent_definitions: {e}")
    import traceback
    traceback.print_exc()
    flash_llm = None
    coding_llm = None

# ============================================================================
# Exploration Phase Agents
# ============================================================================

def create_exploration_agents() -> tuple:
    """Create agents for the exploration phase."""
    
    codebase_explorer = Agent(
        role='Codebase Explorer',
        goal='Explore and map the codebase structure, identify relevant files',
        backstory=CODEBASE_EXPLORER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast, free model for exploration
        tools=[read_file, get_repo_structure, search_code_pattern]
    )
    
    dependency_analyzer = Agent(
        role='Dependency Analyzer',
        goal='Understand imports, dependencies, and code relationships',
        backstory=DEPENDENCY_ANALYZER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast model for dependency analysis
        tools=[analyze_file_dependencies, find_file_dependents, find_symbol_definition]
    )
    
    pattern_matcher = Agent(
        role='Pattern Matcher',
        goal='Find similar code patterns and past fixes from memory',
        backstory=PATTERN_MATCHER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast model for pattern matching
        tools=[search_code_pattern, retrieve_memory_context, read_file]
    )
    
    return codebase_explorer, dependency_analyzer, pattern_matcher

# ============================================================================
# Fix Generation Phase Agents
# ============================================================================

def create_fix_generation_agents() -> tuple:
    """Create agents for the fix generation phase."""
    
    fix_strategist = Agent(
        role='Fix Strategist',
        goal='Plan fix approaches and generate multiple strategies',
        backstory=FIX_STRATEGIST_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm,  # Better reasoning for strategy
        tools=[read_file, analyze_file_dependencies, retrieve_memory_context]
    )
    
    code_fixer_primary = Agent(
        role='Code Fixer (Primary)',
        goal='Generate the primary code fix using incremental edits',
        backstory=CODE_FIXER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm,  # Best model for code generation
        memory=True,
        tools=[
            read_file,
            find_symbol_definition,
            analyze_file_dependencies,
            apply_incremental_edit,
            validate_code
        ]
    )
    
    code_fixer_alternative = Agent(
        role='Code Fixer (Alternative)',
        goal='Generate alternative fixes for comparison',
        backstory=CODE_FIXER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm,  # Best model for code generation
        memory=True,
        tools=[
            read_file,
            find_symbol_definition,
            analyze_file_dependencies,
            apply_incremental_edit,
            validate_code
        ]
    )
    
    confidence_scorer = Agent(
        role='Confidence Scorer',
        goal='Score fixes for confidence, risk, and quality',
        backstory=CONFIDENCE_SCORER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast model for scoring
        tools=[validate_code, retrieve_memory_context]
    )
    
    return fix_strategist, code_fixer_primary, code_fixer_alternative, confidence_scorer

# ============================================================================
# Validation & Decision Phase Agents
# ============================================================================

def create_validation_agents() -> tuple:
    """Create agents for the validation and decision phase."""
    
    syntax_validator = Agent(
        role='Syntax Validator',
        goal='Validate code syntax and basic correctness',
        backstory=SYNTAX_VALIDATOR_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast model for validation
        tools=[validate_code, read_file]
    )
    
    impact_analyzer = Agent(
        role='Impact Analyzer',
        goal='Analyze breaking changes and dependencies',
        backstory=IMPACT_ANALYZER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast model for impact analysis
        tools=[analyze_file_dependencies, find_file_dependents, read_file]
    )
    
    pattern_consistency_validator = Agent(
        role='Pattern Consistency Validator',
        goal='Ensure fixes match codebase patterns',
        backstory=PATTERN_CONSISTENCY_VALIDATOR_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,  # Fast model for pattern checking
        tools=[search_code_pattern, retrieve_memory_context, read_file]
    )
    
    decision_maker = Agent(
        role='Decision Maker',
        goal='Make autonomous decisions about PR creation based on confidence',
        backstory=DECISION_MAKER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm,  # Better reasoning for decisions
        tools=[]  # No tools - uses results from other agents
    )
    
    return syntax_validator, impact_analyzer, pattern_consistency_validator, decision_maker

# ============================================================================
# QA Review Phase Agents
# ============================================================================

def create_qa_agents() -> tuple:
    """Create agents for QA review phase."""
    
    qa_reviewer = Agent(
        role='QA Reviewer',
        goal='Review pull requests, identify code issues, antipatterns, and ensure best practices',
        backstory=QA_REVIEWER_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm,  # Best model for code review and analysis
        tools=[
            review_pr,
            get_pr_file_contents,
            comment_on_pr,
            request_pr_changes,
            approve_pr,
            analyze_code_quality,
            check_antipatterns,
            validate_solution,
            read_file
        ]
    )
    
    return (qa_reviewer,)

# ============================================================================
# Simple four-agent crew (log parser, RCA, coder, safety)
# ============================================================================

def create_simple_crew_agents() -> tuple:
    """
    Create the simple four-agent CrewAI crew: Log Parsing Specialist, Root Cause Analyst,
    Senior Coding Agent, and Safety & Compliance Officer.
    Used for reference or alternative flows; the main flow uses create_all_enhanced_agents().
    """
    if not flash_llm or not coding_llm:
        raise ValueError("LLMs not initialized. Check OPENCOUNCIL_API.")
    log_parser = Agent(
        role='Log Parsing Specialist',
        goal='Extract structured signals from raw logs and identify anomalies.',
        backstory='You are an expert in parsing logs from various systems (Kubernetes, Cloud Run, Postgres). You can spot stack traces and error codes instantly.',
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,
    )
    rca_analyst = Agent(
        role='Root Cause Analyst',
        goal='Determine the underlying cause of the incident based on parsed logs and system state.',
        backstory=RCA_AGENT_PROMPT,
        verbose=True,
        allow_delegation=True,
        llm=flash_llm,
    )
    coding_agent = Agent(
        role='Senior Coding Agent',
        goal='Implement code fixes and improvements based on RCA and Safety analysis, utilizing code memory.',
        backstory=CODING_AGENT_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm,
        memory=True,
    )
    safety_officer = Agent(
        role='Safety & Compliance Officer',
        goal='Ensure that proposed healing actions are safe and reversible.',
        backstory='You are responsible for system stability. You reject any action that could cause data loss or downtime without approval.',
        verbose=True,
        allow_delegation=False,
        llm=flash_llm,
    )
    return log_parser, rca_analyst, coding_agent, safety_officer


# Alias for backward compatibility (e.g. scripts/update_imports.py mapping "from agents import" -> definitions)
create_agents = create_simple_crew_agents

# ============================================================================
# Convenience Functions
# ============================================================================

def create_all_enhanced_agents() -> dict:
    """Create all enhanced agents organized by phase."""
    exploration = create_exploration_agents()
    fix_generation = create_fix_generation_agents()
    validation = create_validation_agents()
    qa_review = create_qa_agents()
    
    return {
        "exploration": {
            "codebase_explorer": exploration[0],
            "dependency_analyzer": exploration[1],
            "pattern_matcher": exploration[2]
        },
        "fix_generation": {
            "fix_strategist": fix_generation[0],
            "code_fixer_primary": fix_generation[1],
            "code_fixer_alternative": fix_generation[2],
            "confidence_scorer": fix_generation[3]
        },
        "validation": {
            "syntax_validator": validation[0],
            "impact_analyzer": validation[1],
            "pattern_consistency_validator": validation[2],
            "decision_maker": validation[3]
        },
        "qa_review": {
            "qa_reviewer": qa_review[0]
        }
    }

