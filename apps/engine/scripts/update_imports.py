#!/usr/bin/env python3
"""
Helper script to update imports after folder restructuring.

This script updates import statements to use the new src/ structure.
"""
import os
import re
import sys
from pathlib import Path

# Mapping of old imports to new imports
IMPORT_MAPPINGS = [
    # Database imports
    (r'^from database import', 'from src.database.database import'),
    (r'^from models import', 'from src.database.models import'),
    (r'^import memory_models', 'from src.memory.models import'),
    (r'^from memory_models import', 'from src.memory.models import'),
    
    # Agent imports
    (r'^from agent_orchestrator import', 'from src.agents.orchestrator import'),
    (r'^from agent_definitions import', 'from src.agents.definitions import'),
    (r'^from agent_prompts import', 'from src.agents.prompts import'),
    (r'^from agent_workspace import', 'from src.agents.workspace import'),
    (r'^from agent_scratchpad import', 'from src.agents.scratchpad import'),
    (r'^from execution_loop import', 'from src.agents.execution_loop import'),
    (r'^from context_manager import', 'from src.agents.context_manager import'),
    (r'^from agents import', 'from src.agents.legacy import'),
    (r'^from crew import', 'from src.agents.crew import'),
    
    # Core imports
    (r'^from event_stream import', 'from src.core.event_stream import'),
    (r'^from task_planner import', 'from src.core.task_planner import'),
    (r'^from system_prompt import', 'from src.core.system_prompt import'),
    (r'^from confidence_scoring import', 'from src.core.confidence_scoring import'),
    (r'^from ai_analysis import', 'from src.core.ai_analysis import'),
    
    # Service imports
    (r'^from slack_service import', 'from src.services.slack.service import'),
    (r'^from email_service import', 'from src.services.email.service import'),
    (r'^from cleanup_service import', 'from src.services.cleanup.service import'),
    
    # Integration imports
    (r'^from integrations\.github_integration import', 'from src.integrations.github.integration import'),
    (r'^from integrations\.github_app_auth import', 'from src.integrations.github.app_auth import'),
    (r'^from integrations import generate_api_key', 'from src.integrations.utils import generate_api_key'),
    (r'^from integrations import', 'from src.integrations import'),
    
    # Tool imports
    (r'^from code_execution_tools import', 'from src.tools.code_execution import'),
    (r'^from coding_tools import', 'from src.tools.coding import'),
    (r'^from sourcemap_resolver import', 'from src.tools.sourcemap import'),
    
    # Memory imports
    (r'^from memory import CodeMemory', 'from src.memory.memory import CodeMemory'),
    (r'^from memory import', 'from src.memory import'),
    (r'^from knowledge_retriever import', 'from src.memory.knowledge_retriever import'),
    (r'^from partition_manager import', 'from src.memory.partition_manager import'),
    
    # Middleware imports
    (r'^from middleware import', 'from src.middleware import'),
    (r'^from rate_limiter import', 'from src.middleware.rate_limiter import'),
    
    # Auth imports
    (r'^from auth import', 'from src.auth.auth import'),
    (r'^from crypto_utils import', 'from src.auth.crypto_utils import'),
    
    # Config imports
    (r'^from prompts import', 'from src.config.prompts import'),
    
    # Utils imports
    (r'^from actions import', 'from src.utils.actions import'),
]

def update_file_imports(filepath: Path) -> bool:
    """Update imports in a single file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # Apply all import mappings
        for pattern, replacement in IMPORT_MAPPINGS:
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        
        # Only write if content changed
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
    except Exception as e:
        print(f"Error updating {filepath}: {e}")
        return False

def update_imports_in_directory(directory: Path, recursive: bool = True):
    """Update imports in all Python files in a directory."""
    updated_count = 0
    
    if recursive:
        pattern = "**/*.py"
    else:
        pattern = "*.py"
    
    for filepath in directory.glob(pattern):
        # Skip __pycache__ and venv directories
        if '__pycache__' in str(filepath) or 'venv' in str(filepath):
            continue
        
        if update_file_imports(filepath):
            print(f"✅ Updated: {filepath.relative_to(directory)}")
            updated_count += 1
    
    return updated_count

if __name__ == "__main__":
    # Update imports in src/ directory
    src_dir = Path(__file__).parent.parent / "src"
    if src_dir.exists():
        print(f"Updating imports in {src_dir}...")
        count = update_imports_in_directory(src_dir)
        print(f"\n✅ Updated {count} files")
    else:
        print(f"❌ {src_dir} does not exist")
