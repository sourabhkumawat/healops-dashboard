import json
import os
import redis
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from database import SessionLocal
from memory_models import AgentMemoryError, AgentMemoryFix, AgentRepoContext

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

class CodeMemory:
    def __init__(self):
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        self.db: Session = SessionLocal()

    def __del__(self):
        if hasattr(self, 'db'):
            self.db.close()

    def _get_redis_key(self, prefix: str, key: str) -> str:
        return f"agent_memory:{prefix}:{key}"

    def store_error_context(self, error_signature: str, context: str):
        """Stores context related to a specific error signature in DB and Redis."""
        # 1. Store in DB (Persistent)
        try:
            # Check if exists
            existing_error = self.db.query(AgentMemoryError).filter(AgentMemoryError.error_signature == error_signature).first()
            if existing_error:
                existing_error.context = context
                # Update timestamp handled by onupdate if we added it, or manually
            else:
                new_error = AgentMemoryError(error_signature=error_signature, context=context)
                self.db.add(new_error)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            print(f"Error storing error context in DB: {e}")

        # 2. Cache in Redis (Fast Access)
        try:
            key = self._get_redis_key("error", error_signature)
            self.redis_client.set(key, context, ex=3600*24) # Cache for 24 hours
        except Exception as e:
            print(f"Error storing error context in Redis: {e}")

    def store_fix(self, error_signature: str, fix_description: str, code_patch: str):
        """Stores a successful fix for an error in DB."""
        try:
            new_fix = AgentMemoryFix(
                error_signature=error_signature,
                description=fix_description,
                code_patch=code_patch
            )
            self.db.add(new_fix)
            self.db.commit()

            # Invalidate/Update Redis cache for retrieval
            # We might want to cache the LIST of fixes for an error
            self._cache_fixes(error_signature)

        except Exception as e:
            self.db.rollback()
            print(f"Error storing fix in DB: {e}")

    def _cache_fixes(self, error_signature: str):
        """Helper to cache fixes in Redis."""
        try:
            fixes = self.db.query(AgentMemoryFix).filter(AgentMemoryFix.error_signature == error_signature).all()
            fixes_data = [
                {"description": f.description, "patch": f.code_patch}
                for f in fixes
            ]
            key = self._get_redis_key("fixes", error_signature)
            self.redis_client.set(key, json.dumps(fixes_data), ex=3600*24)
        except Exception as e:
            print(f"Error caching fixes in Redis: {e}")

    def retrieve_context(self, error_signature: str) -> Dict[str, Any]:
        """Retrieves past errors and fixes for a given error signature.
           Tries Redis first, falls back to DB.
        """
        result = {
            "past_errors": [],
            "known_fixes": []
        }

        # 1. Try Redis for Fixes
        try:
            fixes_key = self._get_redis_key("fixes", error_signature)
            cached_fixes = self.redis_client.get(fixes_key)
            if cached_fixes:
                result["known_fixes"] = json.loads(cached_fixes)
        except Exception as e:
            print(f"Redis read error (fixes): {e}")

        # 2. Try Redis for Error Context
        try:
            error_key = self._get_redis_key("error", error_signature)
            cached_error = self.redis_client.get(error_key)
            if cached_error:
                result["past_errors"].append({"context": cached_error, "source": "redis"})
        except Exception as e:
            print(f"Redis read error (error): {e}")

        # 3. Fallback to DB if missing
        if not result["known_fixes"]:
            fixes = self.db.query(AgentMemoryFix).filter(AgentMemoryFix.error_signature == error_signature).all()
            if fixes:
                result["known_fixes"] = [
                    {"description": f.description, "patch": f.code_patch}
                    for f in fixes
                ]
                # Populate cache
                self._cache_fixes(error_signature)

        if not result["past_errors"]:
            error = self.db.query(AgentMemoryError).filter(AgentMemoryError.error_signature == error_signature).first()
            if error:
                result["past_errors"].append({"context": error.context, "source": "db"})
                # Populate cache
                try:
                    key = self._get_redis_key("error", error_signature)
                    self.redis_client.set(key, error.context, ex=3600*24)
                except Exception:
                    pass

        return result

    def update_repo_context(self, file_path: str, summary: str):
        """Updates the memory with a summary of a file."""
        try:
            existing = self.db.query(AgentRepoContext).filter(AgentRepoContext.file_path == file_path).first()
            if existing:
                existing.summary = summary
            else:
                new_context = AgentRepoContext(file_path=file_path, summary=summary)
                self.db.add(new_context)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            print(f"Error updating repo context: {e}")
