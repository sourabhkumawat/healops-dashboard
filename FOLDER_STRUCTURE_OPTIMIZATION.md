# Folder Structure Optimization Plan

## Current Issues

### 1. **apps/engine/** - Too Many Files in Root (58 files)
   - Agent-related files scattered: `agent_*.py`, `agents.py`, `agent_orchestrator.py`
   - Services mixed: `slack_service.py`, `email_service.py`, `cleanup_service.py`
   - Scripts mixed with application code: `onboard_agent_employee.py`, `create_bot_user.py`, `seed_user.py`
   - Documentation in root: `SLACK_*.md` files
   - Duplicate/backup files: `middleware.py.backup`, `middleware_SECURE.py`
   - Configuration scattered: `main.py`, `main_function.py`

### 2. **apps/dashboard/** - Reasonable but can be improved
   - Good separation with `src/` folder
   - Could benefit from better component organization

### 3. **Root Level**
   - `assets/` folder could be moved to a shared location

## Proposed Optimized Structure

```
Healops/
├── apps/
│   ├── engine/
│   │   ├── src/                          # Main application code
│   │   │   ├── __init__.py
│   │   │   ├── main.py                   # FastAPI application entry point
│   │   │   │
│   │   │   ├── api/                      # API endpoints and routes
│   │   │   │   ├── __init__.py
│   │   │   │   ├── routes/
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── incidents.py
│   │   │   │   │   ├── auth.py
│   │   │   │   │   └── integrations.py
│   │   │   │   └── dependencies.py       # FastAPI dependencies
│   │   │   │
│   │   │   ├── agents/                   # Agent system
│   │   │   │   ├── __init__.py
│   │   │   │   ├── orchestrator.py       # agent_orchestrator.py
│   │   │   │   ├── definitions.py        # agent_definitions.py
│   │   │   │   ├── prompts.py            # agent_prompts.py
│   │   │   │   ├── workspace.py          # agent_workspace.py
│   │   │   │   ├── scratchpad.py         # agent_scratchpad.py
│   │   │   │   ├── execution_loop.py
│   │   │   │   ├── context_manager.py
│   │   │   │   ├── legacy.py             # agents.py (for backward compat)
│   │   │   │   └── crew.py
│   │   │   │
│   │   │   ├── core/                     # Core business logic
│   │   │   │   ├── __init__.py
│   │   │   │   ├── task_planner.py
│   │   │   │   ├── event_stream.py
│   │   │   │   ├── system_prompt.py
│   │   │   │   ├── confidence_scoring.py
│   │   │   │   └── ai_analysis.py
│   │   │   │
│   │   │   ├── services/                 # External service integrations
│   │   │   │   ├── __init__.py
│   │   │   │   ├── slack/
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── service.py        # slack_service.py
│   │   │   │   │   └── handlers.py       # Slack event handlers
│   │   │   │   ├── email/
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── service.py        # email_service.py
│   │   │   │   └── cleanup/
│   │   │   │       ├── __init__.py
│   │   │   │       └── service.py        # cleanup_service.py
│   │   │   │
│   │   │   ├── integrations/             # Integration providers
│   │   │   │   ├── __init__.py
│   │   │   │   ├── github/
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── integration.py    # github_integration.py
│   │   │   │   │   └── app_auth.py       # github_app_auth.py
│   │   │   │   └── base.py               # Base integration class
│   │   │   │
│   │   │   ├── tools/                    # Agent tools
│   │   │   │   ├── __init__.py
│   │   │   │   ├── code_execution.py     # code_execution_tools.py
│   │   │   │   ├── coding.py             # coding_tools.py
│   │   │   │   └── sourcemap.py          # sourcemap_resolver.py
│   │   │   │
│   │   │   ├── memory/                   # Memory and learning
│   │   │   │   ├── __init__.py
│   │   │   │   ├── memory.py             # CodeMemory
│   │   │   │   ├── models.py             # memory_models.py
│   │   │   │   ├── knowledge_retriever.py
│   │   │   │   └── partition_manager.py
│   │   │   │
│   │   │   ├── database/                 # Database layer
│   │   │   │   ├── __init__.py
│   │   │   │   ├── database.py           # Database connection
│   │   │   │   ├── models.py             # SQLAlchemy models
│   │   │   │   └── migrations/           # Alembic migrations
│   │   │   │       ├── versions/
│   │   │   │       └── env.py
│   │   │   │
│   │   │   ├── middleware/               # Middleware and security
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py               # Authentication middleware
│   │   │   │   ├── api_key.py            # APIKeyMiddleware
│   │   │   │   ├── rate_limiter.py
│   │   │   │   └── security.py           # middleware_SECURE.py (consolidated)
│   │   │   │
│   │   │   ├── auth/                     # Authentication utilities
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py               # Password/auth functions
│   │   │   │   └── crypto_utils.py       # Encryption utilities
│   │   │   │
│   │   │   └── utils/                    # Utility functions
│   │   │       ├── __init__.py
│   │   │       └── actions.py            # actions.py
│   │   │
│   │   ├── scripts/                      # Administrative scripts
│   │   │   ├── __init__.py
│   │   │   ├── onboard_agent_employee.py
│   │   │   ├── create_bot_user.py
│   │   │   ├── seed_user.py
│   │   │   ├── update_agent_token.py
│   │   │   ├── migrate.py
│   │   │   ├── migrate_add_installation_id.py
│   │   │   ├── repair_data.py
│   │   │   └── quick_test.py
│   │   │
│   │   ├── tests/                        # Test files
│   │   │   ├── __init__.py
│   │   │   ├── test_agent.py
│   │   │   ├── debug_slack_agent.py
│   │   │   └── conftest.py
│   │   │
│   │   ├── docs/                         # Documentation
│   │   │   ├── SLACK_ONBOARDING_GUIDE.md
│   │   │   ├── SLACK_IMPLEMENTATION_SUMMARY.md
│   │   │   └── README.md
│   │   │
│   │   ├── config/                       # Configuration files
│   │   │   ├── prompts.py                # prompts.py (general prompts)
│   │   │   └── settings.py               # Environment/config management
│   │   │
│   │   ├── templates/                    # Template files (keep as is)
│   │   │
│   │   ├── celery_app.py                 # Celery app configuration
│   │   ├── tasks.py                      # Celery tasks
│   │   ├── requirements.txt
│   │   ├── requirements_cf.txt
│   │   ├── Dockerfile
│   │   ├── Procfile
│   │   ├── run_local.sh
│   │   └── .env.example
│   │
│   └── dashboard/                        # Next.js dashboard (mostly OK)
│       ├── src/
│       │   ├── app/                      # Next.js app router
│       │   ├── components/               # React components
│       │   │   ├── features/             # Feature-specific components
│       │   │   │   ├── incidents/
│       │   │   │   ├── integrations/
│       │   │   │   └── logs/
│       │   │   └── ui/                   # Reusable UI components (shadcn)
│       │   ├── lib/                      # Utilities and client code
│       │   └── actions/                  # Server actions
│       └── ...
│
├── packages/                             # Shared packages (keep as is)
│   ├── healops_opentelemetry_python/
│   └── healops-opentelemetry_node/
│
├── shared/                               # Shared resources
│   ├── assets/                           # Moved from root
│   │   └── logo.png
│   └── types/                            # Shared TypeScript types (if needed)
│
├── docker-compose.yml
├── README.md
└── .gitignore

```

## Benefits of This Structure

### 1. **Clear Separation of Concerns**
   - API routes separated from business logic
   - Services grouped by functionality
   - Scripts separated from application code

### 2. **Improved Discoverability**
   - Easy to find related files
   - Logical grouping by feature/domain
   - Clear entry points

### 3. **Better Maintainability**
   - Easier to add new features
   - Clear module boundaries
   - Reduced coupling

### 4. **Scalability**
   - Easy to add new services
   - Simple to extend integrations
   - Clear patterns for new code

### 5. **Better Testing**
   - Tests co-located or in dedicated directory
   - Easier to mock dependencies
   - Clear test organization

## Migration Steps

### Phase 1: Create New Structure (Non-breaking)
1. Create new directories
2. Move files gradually
3. Update imports incrementally
4. Keep old files for reference initially

### Phase 2: Update Imports
1. Update all relative imports
2. Update `__init__.py` files for proper exports
3. Update `PYTHONPATH` or use proper package structure

### Phase 3: Cleanup
1. Remove duplicate/backup files
2. Remove old file locations
3. Update documentation

## Import Pattern Changes

### Before:
```python
from agent_orchestrator import run_robust_crew
from slack_service import SlackService
from models import Incident
```

### After:
```python
from src.agents.orchestrator import run_robust_crew
from src.services.slack.service import SlackService
from src.database.models import Incident
```

## Specific File Mappings

| Current Location | New Location | Reason |
|-----------------|--------------|--------|
| `agent_orchestrator.py` | `src/agents/orchestrator.py` | Agent system core |
| `agent_definitions.py` | `src/agents/definitions.py` | Agent definitions |
| `agent_prompts.py` | `src/agents/prompts.py` | Agent prompts |
| `agent_workspace.py` | `src/agents/workspace.py` | Agent workspace |
| `slack_service.py` | `src/services/slack/service.py` | Slack service |
| `email_service.py` | `src/services/email/service.py` | Email service |
| `github_integration.py` | `src/integrations/github/integration.py` | GitHub integration |
| `models.py` | `src/database/models.py` | Database models |
| `database.py` | `src/database/database.py` | Database connection |
| `memory.py` | `src/memory/memory.py` | Memory system |
| `onboard_agent_employee.py` | `scripts/onboard_agent_employee.py` | Administrative script |
| `middleware.py` | `src/middleware/auth.py` | Auth middleware |
| `middleware_SECURE.py` | `src/middleware/security.py` | Security middleware |
| `SLACK_*.md` | `docs/` | Documentation |
| `test_agent.py` | `tests/test_agent.py` | Test file |

## Implementation Priority

### High Priority (Core Structure)
1. Create `src/` directory structure
2. Move agents/ to `src/agents/`
3. Move services/ to `src/services/`
4. Move database/ to `src/database/`
5. Move scripts/ to separate directory

### Medium Priority (Refinement)
1. Organize API routes
2. Consolidate middleware
3. Group integrations
4. Organize documentation

### Low Priority (Polish)
1. Optimize dashboard structure
2. Add shared types
3. Consolidate configs

## Notes

- Use `__init__.py` files to maintain backward compatibility where possible
- Keep import paths simple with proper `__init__.py` exports
- Consider using a `setup.py` or `pyproject.toml` for proper package structure
- Add type stubs in `typings/` if needed for better IDE support
