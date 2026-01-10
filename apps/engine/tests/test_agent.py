"""
Test script to run the agent directly with debugging support.
Set breakpoints in this file or in the agent_orchestrator.py to debug.
"""
import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sys
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.database import SessionLocal
from src.database.models import Incident, LogEntry
from src.agents.orchestrator import run_robust_crew
from src.integrations.github.integration import GithubIntegration

def test_agent(incident_id: int = None):
    """
    Test the agent with a specific incident.
    
    Args:
        incident_id: ID of the incident to test. If None, uses the first available incident.
    """
    db = SessionLocal()
    try:
        # Get an incident
        if incident_id:
            incident = db.query(Incident).filter(Incident.id == incident_id).first()
        else:
            incident = db.query(Incident).order_by(Incident.id.desc()).first()
        
        if not incident:
            print("❌ No incident found. Create one first via the API or database.")
            print("\nTo create a test incident:")
            print("1. Send logs to /ingest/logs endpoint")
            print("2. Or create one directly in the database")
            return
        
        print(f"✅ Found incident {incident.id}: {incident.title}")
        print(f"   Status: {incident.status}")
        print(f"   Root cause: {incident.root_cause[:100] if incident.root_cause else 'Not set'}")
        
        # Get logs
        logs = []
        if incident.log_ids:
            log_id_list = incident.log_ids if isinstance(incident.log_ids, list) else []
            if log_id_list:
                logs = db.query(LogEntry).filter(LogEntry.id.in_(log_id_list)).all()
                print(f"   Found {len(logs)} log entries")
        
        if not logs:
            print("⚠️  Warning: No logs found for this incident")
        
        # Get GitHub integration
        github_integration = None
        if incident.integration_id:
            try:
                github_integration = GithubIntegration(integration_id=incident.integration_id)
                print(f"✅ GitHub integration loaded (ID: {incident.integration_id})")
            except Exception as e:
                print(f"⚠️  Warning: Failed to load GitHub integration: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("⚠️  Warning: No integration_id set for this incident")
        
        repo_name = incident.repo_name or "owner/repo"
        print(f"   Repository: {repo_name}")
        
        # Set root cause if not set
        root_cause = incident.root_cause or "Test root cause - debugging agent execution"
        if not incident.root_cause:
            print(f"⚠️  Root cause not set, using placeholder: {root_cause}")
        
        print("\n" + "="*60)
        print("🚀 Starting agent execution...")
        print("="*60 + "\n")
        print("💡 Set breakpoints in:")
        print("   - agent_orchestrator.py:run_robust_crew() (line 73)")
        print("   - agent_orchestrator.py:_execute_agent_action() (line 396)")
        print("   - execution_loop.py:AgentLoop.run() (line 61)")
        print("\n")
        
        # Run agent (set breakpoint here!)
        result = run_robust_crew(
            incident=incident,
            logs=logs,
            root_cause=root_cause,
            github_integration=github_integration,
            repo_name=repo_name,
            db=db
        )
        
        print("\n" + "="*60)
        print("✅ Agent execution completed!")
        print("="*60)
        print(f"\nResult status: {result.get('status', 'unknown')}")
        
        if result.get('events'):
            print(f"Total events: {len(result['events'])}")
        
        if result.get('error'):
            print(f"Error: {result['error']}")
        
        return result
        
    except Exception as e:
        import traceback
        print(f"\n❌ Error running agent: {e}")
        print("\nFull traceback:")
        print(traceback.format_exc())
        raise
    finally:
        db.close()

if __name__ == "__main__":
    # You can pass an incident_id as command line argument
    incident_id = None
    if len(sys.argv) > 1:
        try:
            incident_id = int(sys.argv[1])
        except ValueError:
            print(f"Invalid incident_id: {sys.argv[1]}. Using first available incident.")
    
    test_agent(incident_id)

