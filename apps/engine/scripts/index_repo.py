"""
Index repository with CocoIndex for active GitHub integrations.
Indexes the default repository configured for each active integration.
"""
import sys
import os

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.database import SessionLocal, engine
from src.database.models import Integration

# Lazy import to handle missing cocoindex gracefully
try:
    from src.memory.cocoindex_flow import execute_flow_update
    COCOINDEX_AVAILABLE = True
except ImportError as e:
    COCOINDEX_AVAILABLE = False
    execute_flow_update = None
    print(f"⚠️  Warning: CocoIndex not available: {e}")
    print("   Install with: pip install cocoindex[embeddings]")


def get_active_github_integrations():
    """Get all active GitHub integrations from the database."""
    db = SessionLocal()
    try:
        integrations = db.query(Integration).filter(
            Integration.provider == "GITHUB",
            Integration.status == "ACTIVE"
        ).all()
        return integrations
    finally:
        db.close()


def get_repo_name(integration: Integration) -> str:
    """
    Get repository name from integration config.
    
    Args:
        integration: Integration model instance
        
    Returns:
        Repository name in format "owner/repo" or None
    """
    # Check config first
    if integration.config and isinstance(integration.config, dict):
        repo_name = integration.config.get("repo_name") or integration.config.get("repository")
        if repo_name:
            return repo_name
    
    # Check project_id as fallback
    if integration.project_id:
        return integration.project_id
    
    return None


def index_integrations():
    """Index repositories for all active GitHub integrations."""
    if not COCOINDEX_AVAILABLE or execute_flow_update is None:
        print("❌ CocoIndex is not available")
        print("   Please install it with: pip install 'cocoindex[embeddings]>=0.1.0'")
        print("   Or install all requirements: pip install -r requirements.txt")
        return
    
    print("🔍 Finding active GitHub integrations...")
    
    integrations = get_active_github_integrations()
    
    if not integrations:
        print("❌ No active GitHub integrations found")
        print("   Please set up a GitHub integration first and mark it as ACTIVE")
        return
    
    print(f"✅ Found {len(integrations)} active GitHub integration(s)")
    
    success_count = 0
    failed_count = 0
    
    for integration in integrations:
        repo_name = get_repo_name(integration)
        
        if not repo_name:
            print(f"⚠️  Skipping integration {integration.id} ({integration.name}): No repo_name configured")
            failed_count += 1
            continue
        
        if not integration.id:
            print(f"⚠️  Skipping integration {integration.id}: Missing integration ID")
            failed_count += 1
            continue
        
        print(f"\n📦 Indexing repository: {repo_name} (Integration ID: {integration.id})")
        
        try:
            success = execute_flow_update(
                repo_name=repo_name,
                integration_id=integration.id,
                ref="main"
            )
            
            if success:
                print(f"✅ Successfully indexed: {repo_name}")
                success_count += 1
            else:
                print(f"❌ Failed to index: {repo_name}")
                failed_count += 1
        except Exception as e:
            print(f"❌ Error indexing {repo_name}: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1
    
    print(f"\n{'='*60}")
    print(f"📊 Indexing Summary:")
    print(f"   ✅ Successful: {success_count}")
    print(f"   ❌ Failed: {failed_count}")
    print(f"   📦 Total: {len(integrations)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    print("🚀 Starting CocoIndex repository indexing...")
    print("=" * 60)
    
    try:
        index_integrations()
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n✅ Indexing script completed")
    sys.exit(0)
