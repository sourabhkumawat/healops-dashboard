import json
import os
import redis
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from database import SessionLocal
from memory_models import AgentMemoryError, AgentMemoryFix, AgentRepoContext, AgentLearningPattern

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

    def store_fix(self, error_signature: str, fix_description: str, code_patch: str, structured_data: Optional[Dict[str, Any]] = None):
        """Stores a successful fix for an error in DB.
        
        Args:
            error_signature: Error signature/fingerprint
            fix_description: Description of the fix
            code_patch: Code patch (can be edit blocks or full file)
            structured_data: Optional structured fix data (edits, validation, etc.)
        """
        try:
            # If structured_data provided, store as JSON in code_patch
            if structured_data:
                code_patch = json.dumps(structured_data)
            
            new_fix = AgentMemoryFix(
                error_signature=error_signature,
                description=fix_description,
                code_patch=code_patch
            )
            self.db.add(new_fix)
            self.db.commit()

            # Invalidate/Update Redis cache for retrieval
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
    
    def compare_with_memory(self, error_signature: str, current_fix: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare current fix with past fixes in memory.
        
        Args:
            error_signature: Error signature to look up
            current_fix: Current fix data to compare
            
        Returns:
            Comparison results with similarity scores
        """
        memory_data = self.retrieve_context(error_signature)
        known_fixes = memory_data.get("known_fixes", [])
        
        if not known_fixes:
            return {
                "similarity_score": 0,
                "matches": [],
                "message": "No past fixes found in memory"
            }
        
        matches = []
        current_files = set(current_fix.get("files_changed", []))
        
        for i, past_fix in enumerate(known_fixes[:5]):  # Compare with top 5 past fixes
            try:
                # Try to parse structured data if available
                patch_data = past_fix.get("patch", "")
                if patch_data.startswith("{"):
                    past_fix_data = json.loads(patch_data)
                    past_files = set(past_fix_data.get("files_changed", []))
                else:
                    # Legacy format - extract file paths from patch
                    past_files = set()
                    # Simple extraction (would need more sophisticated parsing)
                
                # Calculate similarity
                if current_files and past_files:
                    common_files = current_files.intersection(past_files)
                    similarity = len(common_files) / max(len(current_files), len(past_files))
                else:
                    similarity = 0.5 if past_fix.get("description", "").lower() in str(current_fix).lower() else 0.0
                
                matches.append({
                    "index": i,
                    "description": past_fix.get("description", ""),
                    "similarity": round(similarity * 100, 2),
                    "common_files": list(common_files) if current_files and past_files else []
                })
            except Exception as e:
                print(f"Error comparing with fix {i}: {e}")
                continue
        
        # Get best match
        best_match = max(matches, key=lambda x: x["similarity"]) if matches else None
        
        return {
            "similarity_score": best_match["similarity"] if best_match else 0,
            "matches": matches,
            "best_match": best_match,
            "total_past_fixes": len(known_fixes)
        }
    
    def store_fix_with_workspace(
        self,
        error_signature: str,
        fix_description: str,
        code_patch: str,
        workspace_context: Dict[str, Any],
        incident_id: int,
        structured_data: Optional[Dict[str, Any]] = None
    ):
        """
        Store a successful fix with workspace context for learning.
        
        Args:
            error_signature: Error signature/fingerprint
            fix_description: Description of the fix
            code_patch: Code patch
            workspace_context: Workspace context with files_read, files_modified, etc.
            incident_id: Incident ID
            structured_data: Optional structured fix data
        """
        # Store the fix normally
        self.store_fix(error_signature, fix_description, code_patch, structured_data)
        
        # Learn from workspace context
        try:
            self._learn_from_workspace(error_signature, workspace_context, fix_description)
        except Exception as e:
            print(f"Warning: Failed to learn from workspace: {e}")
    
    def _learn_from_workspace(
        self,
        error_signature: str,
        workspace_context: Dict[str, Any],
        fix_description: str
    ):
        """
        Extract and store learning patterns from workspace context.
        
        Args:
            error_signature: Error signature
            workspace_context: Workspace context dict
            fix_description: Fix description
        """
        # Extract error type
        error_type = self._extract_error_type(error_signature, fix_description)
        
        # Extract patterns
        files_read = workspace_context.get("files_read", [])
        files_modified = workspace_context.get("files_modified", [])
        context_files = workspace_context.get("context_files", [])
        
        # Find or create learning pattern
        pattern = self.db.query(AgentLearningPattern).filter(
            AgentLearningPattern.error_type == error_type
        ).first()
        
        if pattern:
            # Update existing pattern
            # Merge file lists (keep unique)
            existing_read = set(pattern.typical_files_read or [])
            existing_modified = set(pattern.typical_files_modified or [])
            existing_context = set(pattern.context_files or [])
            
            pattern.typical_files_read = list(existing_read.union(set(files_read)))
            pattern.typical_files_modified = list(existing_modified.union(set(files_modified)))
            pattern.context_files = list(existing_context.union(set(context_files)))
            
            pattern.total_attempts += 1
            pattern.success_count += 1  # This is called after successful fix
            pattern.last_used = datetime.utcnow()
        else:
            # Create new pattern
            pattern = AgentLearningPattern(
                error_type=error_type,
                error_signature_pattern=error_signature[:200],  # Store pattern
                typical_files_read=files_read,
                typical_files_modified=files_modified,
                context_files=context_files,
                total_attempts=1,
                success_count=1,
                last_used=datetime.utcnow()
            )
            self.db.add(pattern)
        
        # Update confidence score (success rate as percentage)
        if pattern.total_attempts > 0:
            pattern.confidence_score = int((pattern.success_count / pattern.total_attempts) * 100)
        
        self.db.commit()
    
    def _extract_error_type(self, error_signature: str, fix_description: str) -> str:
        """
        Extract error type from signature and description.
        
        Args:
            error_signature: Error signature
            fix_description: Fix description
            
        Returns:
            Error type string
        """
        # Simple categorization logic
        signature_lower = error_signature.lower()
        description_lower = fix_description.lower()
        
        if any(keyword in signature_lower or keyword in description_lower for keyword in ["import", "module", "cannot import"]):
            return "import_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["syntax", "parse", "invalid syntax"]):
            return "syntax_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["type", "attribute", "has no attribute"]):
            return "type_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["key", "keyerror", "missing key"]):
            return "key_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["index", "indexerror", "out of range"]):
            return "index_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["null", "none", "nullpointer"]):
            return "null_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["timeout", "timed out"]):
            return "timeout_error"
        elif any(keyword in signature_lower or keyword in description_lower for keyword in ["network", "connection", "refused"]):
            return "network_error"
        else:
            return "general_error"
    
    def get_learning_pattern(self, error_type: str) -> Optional[Dict[str, Any]]:
        """
        Get learning pattern for a specific error type.
        
        Args:
            error_type: Error type string
            
        Returns:
            Learning pattern dict or None
        """
        try:
            # Get most confident pattern for this error type
            pattern = self.db.query(AgentLearningPattern).filter(
                AgentLearningPattern.error_type == error_type
            ).order_by(AgentLearningPattern.confidence_score.desc()).first()
            
            if pattern:
                return {
                    "error_type": pattern.error_type,
                    "typical_files_read": pattern.typical_files_read or [],
                    "typical_files_modified": pattern.typical_files_modified or [],
                    "context_files": pattern.context_files or [],
                    "confidence_score": pattern.confidence_score,
                    "success_count": pattern.success_count,
                    "total_attempts": pattern.total_attempts
                }
        except Exception as e:
            print(f"Error retrieving learning pattern: {e}")
        
        return None
    
    def update_fix_success_rate(self, error_signature: str, success: bool):
        """
        Update success rate for a fix.
        
        Args:
            error_signature: Error signature
            success: Whether the fix was successful
        """
        try:
            fixes = self.db.query(AgentMemoryFix).filter(
                AgentMemoryFix.error_signature == error_signature
            ).all()
            
            for fix in fixes:
                # Update success rate (simple increment/decrement)
                if success:
                    fix.success_rate = min(100, fix.success_rate + 1)
                else:
                    fix.success_rate = max(0, fix.success_rate - 1)
            
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            print(f"Error updating fix success rate: {e}")
