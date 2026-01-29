"""
Indexing Manager - Handles repository indexing with debouncing to prevent
duplicate indexing operations from multiple push events.
"""
import asyncio
from typing import Dict, Optional
from threading import Lock
from datetime import datetime
import os


class IndexingManager:
    """
    Manages indexing operations with debouncing to handle multiple push events.
    
    When multiple push events arrive in quick succession, this manager:
    1. Debounces requests (waits for a quiet period before indexing)
    2. Prevents concurrent indexing of the same repository
    3. Cancels pending tasks if new pushes arrive
    """
    
    def __init__(self, debounce_seconds: int = 60):
        """
        Initialize the indexing manager.
        
        Args:
            debounce_seconds: Wait time in seconds after last push before indexing
        """
        self.pending_tasks: Dict[str, asyncio.Task] = {}
        self.in_progress: Dict[str, bool] = {}
        self.lock = Lock()
        self.debounce_seconds = debounce_seconds
    
    async def schedule_reindex(
        self, 
        repo_name: str, 
        integration_id: int, 
        ref: str = "main"
    ):
        """
        Schedule reindexing with debouncing.
        
        If a push event comes within the debounce window, the previous task
        is cancelled and a new one is scheduled.
        
        Args:
            repo_name: Repository name in format "owner/repo"
            integration_id: GitHub integration ID
            ref: Branch or commit SHA (default: "main")
            db_session: Optional database session to update last_indexed_at
        """
        key = f"{integration_id}:{repo_name}"
        
        with self.lock:
            # If already indexing, skip (will be reindexed after completion if needed)
            if self.in_progress.get(key, False):
                print(f"⏳ Indexing already in progress for {repo_name}, skipping duplicate request")
                return
            
            # Cancel existing pending task for this repo if any
            if key in self.pending_tasks:
                task = self.pending_tasks[key]
                if not task.done():
                    task.cancel()
                    print(f"🔄 Cancelled previous indexing task for {repo_name} (debouncing)")
            
            # Create new delayed task
            async def delayed_index():
                try:
                    # Wait for debounce period
                    await asyncio.sleep(self.debounce_seconds)
                    
                    # Double-check we're not already indexing
                    with self.lock:
                        if self.in_progress.get(key, False):
                            print(f"⏭️  Skipping indexing for {repo_name} (already in progress)")
                            return
                        self.in_progress[key] = True
                    
                    try:
                        print(f"🔄 Starting indexing for {repo_name} (ref: {ref})")
                        from src.memory.cocoindex_flow import execute_flow_update_async
                        
                        # Execute the indexing (async to avoid event-loop RuntimeWarnings)
                        success = await execute_flow_update_async(repo_name, integration_id, ref)
                        
                        if success:
                            print(f"✅ Successfully indexed {repo_name}")
                            
                            # Update last_indexed_at (create new session to avoid closure issues)
                            try:
                                from src.database.database import SessionLocal
                                from src.database.models import Integration
                                db = SessionLocal()
                                try:
                                    integration = db.query(Integration).filter(
                                        Integration.id == integration_id
                                    ).first()
                                    if integration:
                                        integration.updated_at = datetime.utcnow()
                                        db.commit()
                                        print(f"📝 Updated last_indexed_at for integration {integration_id}")
                                except Exception as e:
                                    print(f"⚠️  Failed to update last_indexed_at: {e}")
                                    db.rollback()
                                finally:
                                    db.close()
                            except Exception as e:
                                print(f"⚠️  Error updating last_indexed_at: {e}")
                        else:
                            print(f"❌ Failed to index {repo_name}")
                    except Exception as e:
                        print(f"❌ Error indexing {repo_name}: {e}")
                        import traceback
                        traceback.print_exc()
                    finally:
                        with self.lock:
                            self.in_progress[key] = False
                            if key in self.pending_tasks:
                                del self.pending_tasks[key]
                except asyncio.CancelledError:
                    print(f"⏭️  Indexing cancelled for {repo_name} (new push received)")
                    with self.lock:
                        if key in self.pending_tasks:
                            del self.pending_tasks[key]
            
            # Schedule the task
            task = asyncio.create_task(delayed_index())
            self.pending_tasks[key] = task
            print(f"📅 Scheduled indexing for {repo_name} in {self.debounce_seconds}s (debounced)")


# Global instance - can be configured via environment variable
DEBOUNCE_SECONDS = int(os.getenv("INDEXING_DEBOUNCE_SECONDS", "60"))
indexing_manager = IndexingManager(debounce_seconds=DEBOUNCE_SECONDS)
