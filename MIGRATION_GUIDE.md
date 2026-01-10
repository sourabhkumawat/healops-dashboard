# Folder Structure Migration Guide

## Quick Wins (Low Risk, High Impact)

These can be done immediately without breaking changes:

### 1. Move Documentation Files
```bash
mkdir -p apps/engine/docs
mv apps/engine/SLACK_*.md apps/engine/docs/
```

### 2. Move Scripts to Dedicated Directory
```bash
mkdir -p apps/engine/scripts
mv apps/engine/onboard_agent_employee.py apps/engine/scripts/
mv apps/engine/create_bot_user.py apps/engine/scripts/
mv apps/engine/seed_user.py apps/engine/scripts/
mv apps/engine/update_agent_token.py apps/engine/scripts/
mv apps/engine/migrate*.py apps/engine/scripts/
mv apps/engine/repair_data.py apps/engine/scripts/
mv apps/engine/quick_test.py apps/engine/scripts/
```

### 3. Remove Duplicate/Backup Files
```bash
rm apps/engine/middleware.py.backup  # Remove backup file
# Review middleware_SECURE.py and consolidate with middleware.py if possible
```

### 4. Move Test Files
```bash
mkdir -p apps/engine/tests
mv apps/engine/test_agent.py apps/engine/tests/
mv apps/engine/debug_slack_agent.py apps/engine/tests/
```

### 5. Organize Dashboard Components
```bash
# In apps/dashboard/src/components/
mkdir -p apps/dashboard/src/components/features/incidents
mkdir -p apps/dashboard/src/components/features/integrations
mkdir -p apps/dashboard/src/components/features/logs

# Move feature-specific components
mv apps/dashboard/src/components/incident-table.tsx apps/dashboard/src/components/features/incidents/
mv apps/dashboard/src/components/AgentThinkingView.tsx apps/dashboard/src/components/features/incidents/
mv apps/dashboard/src/components/CodeDiffViewer.tsx apps/dashboard/src/components/features/incidents/
mv apps/dashboard/src/components/FileDiffCard.tsx apps/dashboard/src/components/features/incidents/
mv apps/dashboard/src/components/live-logs.tsx apps/dashboard/src/components/features/logs/
mv apps/dashboard/src/components/launchdarkly-provider.tsx apps/dashboard/src/components/features/integrations/
```

## Phase 1: Create Core Structure (Medium Risk)

Create the `src/` directory structure and move core files:

### Step 1: Create Directory Structure
```bash
cd apps/engine
mkdir -p src/{api/routes,agents,core,services/{slack,email,cleanup},integrations/github,tools,memory,database,middleware,auth,utils}
```

### Step 2: Move Core Agent Files
```bash
# Agent files
mv agent_orchestrator.py src/agents/orchestrator.py
mv agent_definitions.py src/agents/definitions.py
mv agent_prompts.py src/agents/prompts.py
mv agent_workspace.py src/agents/workspace.py
mv agent_scratchpad.py src/agents/scratchpad.py
mv execution_loop.py src/agents/execution_loop.py
mv context_manager.py src/agents/context_manager.py
mv agents.py src/agents/legacy.py  # Keep for backward compat
mv crew.py src/agents/crew.py
```

### Step 3: Move Core Business Logic
```bash
mv task_planner.py src/core/task_planner.py
mv event_stream.py src/core/event_stream.py
mv system_prompt.py src/core/system_prompt.py
mv confidence_scoring.py src/core/confidence_scoring.py
mv ai_analysis.py src/core/ai_analysis.py
```

### Step 4: Move Services
```bash
# Services
mv slack_service.py src/services/slack/service.py
mv email_service.py src/services/email/service.py
mv cleanup_service.py src/services/cleanup/service.py
```

### Step 5: Move Database Files
```bash
mv models.py src/database/models.py
mv database.py src/database/database.py
mv memory_models.py src/memory/models.py
mv partition_manager.py src/memory/partition_manager.py
```

### Step 6: Move Tools
```bash
mv code_execution_tools.py src/tools/code_execution.py
mv coding_tools.py src/tools/coding.py
mv sourcemap_resolver.py src/tools/sourcemap.py
```

### Step 7: Move Integrations
```bash
# Move existing integrations directory
mv integrations/github_integration.py src/integrations/github/integration.py
mv integrations/github_app_auth.py src/integrations/github/app_auth.py
mv integrations/__init__.py src/integrations/github/__init__.py
```

### Step 8: Move Middleware and Auth
```bash
mv middleware.py src/middleware/api_key.py
mv middleware_SECURE.py src/middleware/security.py
mv rate_limiter.py src/middleware/rate_limiter.py
mv auth.py src/auth/auth.py
mv crypto_utils.py src/auth/crypto_utils.py
```

### Step 9: Move Memory
```bash
mv memory.py src/memory/memory.py
mv knowledge_retriever.py src/memory/knowledge_retriever.py
```

### Step 10: Move Utils
```bash
mv actions.py src/utils/actions.py
mv prompts.py src/config/prompts.py  # General prompts config
```

### Step 11: Update __init__.py Files
Create `__init__.py` files in each directory for proper Python packages.

## Phase 2: Update Imports (High Risk)

This requires careful testing. Update all import statements:

### Update Pattern for agent_orchestrator.py
**Before:**
```python
from agent_definitions import create_all_enhanced_agents
from event_stream import EventStream, EventType
from task_planner import TaskPlanner
```

**After:**
```python
from src.agents.definitions import create_all_enhanced_agents
from src.core.event_stream import EventStream, EventType
from src.core.task_planner import TaskPlanner
```

### Update Pattern for main.py
**Before:**
```python
from database import engine, Base, get_db, SessionLocal
from models import Incident, LogEntry
from slack_service import SlackService
```

**After:**
```python
from src.database.database import engine, Base, get_db, SessionLocal
from src.database.models import Incident, LogEntry
from src.services.slack.service import SlackService
```

## Phase 3: Add __init__.py Files for Backward Compatibility

Create `__init__.py` files that re-export commonly used items:

### src/agents/__init__.py
```python
"""Agent system for HealOps."""
from .orchestrator import run_robust_crew
from .definitions import create_all_enhanced_agents
from .workspace import Workspace
from .scratchpad import Scratchpad

__all__ = [
    'run_robust_crew',
    'create_all_enhanced_agents',
    'Workspace',
    'Scratchpad',
]
```

### src/database/__init__.py
```python
"""Database models and connection."""
from .database import engine, Base, get_db, SessionLocal
from .models import (
    Incident, LogEntry, User, Integration, ApiKey,
    IntegrationStatus, SourceMap, IncidentStatus,
    IncidentSeverity, AgentEmployee
)

__all__ = [
    'engine', 'Base', 'get_db', 'SessionLocal',
    'Incident', 'LogEntry', 'User', 'Integration',
    'ApiKey', 'IntegrationStatus', 'SourceMap',
    'IncidentStatus', 'IncidentSeverity', 'AgentEmployee',
]
```

### src/services/__init__.py
```python
"""Services for external integrations."""
from .slack.service import SlackService
from .email.service import EmailService

__all__ = ['SlackService', 'EmailService']
```

## Potential Issues and Solutions

### Issue 1: Circular Imports
**Solution:** Use `TYPE_CHECKING` for type hints:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.workspace import Workspace

def some_function(workspace: 'Workspace'):
    pass
```

### Issue 2: Relative vs Absolute Imports
**Solution:** Use absolute imports with package structure:
```python
# Good
from src.agents.orchestrator import run_robust_crew

# Avoid relative imports in deep hierarchies
# from ..agents.orchestrator import run_robust_crew
```

### Issue 3: PYTHONPATH Configuration
**Solution:** Ensure `apps/engine` is in PYTHONPATH, or use proper package installation:
```bash
# In docker-compose.yml or deployment
export PYTHONPATH="${PYTHONPATH}:/path/to/Healops/apps/engine"
```

### Issue 4: Script Execution
**Solution:** Update script imports or run with module syntax:
```bash
# Instead of: python onboard_agent_employee.py
# Use: python -m scripts.onboard_agent_employee

# Or update scripts to handle new import paths
```

## Testing Strategy

### 1. Create Test Script
```python
# test_imports.py
"""Test that all imports work after migration."""
import sys
sys.path.insert(0, 'apps/engine')

try:
    from src.agents.orchestrator import run_robust_crew
    from src.database.models import Incident
    from src.services.slack.service import SlackService
    print("✅ All imports successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
```

### 2. Run Existing Tests
```bash
cd apps/engine
python -m pytest tests/ -v
```

### 3. Manual Testing
- Start the FastAPI server
- Test API endpoints
- Test agent execution
- Test Slack integration

## Rollback Plan

If issues arise, you can:
1. Keep old files temporarily with `.old` extension
2. Use symlinks for critical files during transition
3. Create wrapper modules that import from both old and new locations

## Recommended Order of Migration

1. **Week 1: Low-Risk Moves**
   - Move documentation
   - Move scripts
   - Remove backup files
   - Move test files

2. **Week 2: Core Structure**
   - Create `src/` directory
   - Move agent files
   - Move core business logic
   - Update basic imports

3. **Week 3: Services & Integrations**
   - Move services
   - Move integrations
   - Update service imports

4. **Week 4: Database & Memory**
   - Move database files
   - Move memory files
   - Update database imports

5. **Week 5: Middleware & Auth**
   - Move middleware
   - Move auth utilities
   - Update middleware imports

6. **Week 6: Final Cleanup**
   - Remove old files
   - Update documentation
   - Final testing

## Alternative: Gradual Migration

If a full restructure is too risky, consider a gradual approach:

### Option 1: Use Namespaces Without Moving Files
Create `__init__.py` files that re-export, keeping files in place:
```python
# Keep files in root, but organize via __init__.py
# agents/__init__.py
from agent_orchestrator import run_robust_crew  # File still in root
```

### Option 2: Symlink During Transition
```bash
# Create new structure with symlinks
mkdir -p src/agents
ln -s ../../agent_orchestrator.py src/agents/orchestrator.py
# Gradually move files one by one
```

## Checklist

- [ ] Create new directory structure
- [ ] Move documentation files
- [ ] Move scripts
- [ ] Move test files
- [ ] Move agent files
- [ ] Move core business logic
- [ ] Move services
- [ ] Move database files
- [ ] Move tools
- [ ] Move integrations
- [ ] Move middleware
- [ ] Move auth utilities
- [ ] Create `__init__.py` files
- [ ] Update all imports
- [ ] Update scripts
- [ ] Update tests
- [ ] Update documentation
- [ ] Test all functionality
- [ ] Remove old files
- [ ] Update CI/CD pipelines
- [ ] Update Docker files
