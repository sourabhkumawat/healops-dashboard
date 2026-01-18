"""
Knowledge Retriever using CocoIndex for RAG-based codebase pattern retrieval.
Uses CocoIndex with Tree-sitter AST-aware chunking and PostgreSQL storage.
"""
from typing import List, Dict, Any, Optional
import os

# Lazy import CocoIndex dependencies to avoid import errors when module not installed
try:
    from src.memory.cocoindex_flow import code_to_embedding, github_code_embedding_flow
    COCOINDEX_AVAILABLE = True
except ImportError:
    COCOINDEX_AVAILABLE = False
    code_to_embedding = None
    github_code_embedding_flow = None

from src.database.database import DATABASE_URL, engine


class KnowledgeRetriever:
    """
    CocoIndex-based knowledge retriever with PostgreSQL vector storage.
    
    Indexes codebase patterns, past fixes, and best practices using
    CocoIndex with AST-aware chunking via Tree-sitter.
    """
    
    def __init__(self, github_integration, repo_name: str):
        """
        Initialize knowledge retriever.
        
        Args:
            github_integration: GitHub integration instance
            repo_name: Repository name in format "owner/repo"
        """
        self.gh = github_integration
        self.repo_name = repo_name
        self.indexed = False
        
        # Get integration_id from github_integration if available
        self.integration_id = getattr(github_integration, 'installation_id', None)
        if not self.integration_id and hasattr(github_integration, '_integration_id'):
            self.integration_id = github_integration._integration_id
        
        # Use SQLAlchemy engine for database connections
        # For pgvector queries, we'll get raw connections from the engine
        self.engine = engine
        
        # Table name for code embeddings (CocoIndex uses flow name + export name)
        self.table_name = "code_embeddings"
    
    def _get_table_name(self) -> str:
        """Get the CocoIndex table name for this repository."""
        # CocoIndex creates tables with flow-specific naming
        # For now, use a simple table name - adjust based on actual CocoIndex behavior
        return self.table_name
    
    def index_codebase_patterns(self, file_paths: List[str]):
        """
        Trigger CocoIndex flow update for specific files.
        
        Note: CocoIndex handles incremental updates automatically.
        This method can trigger updates if needed, but full indexing
        should happen at repository connection time.
        
        Args:
            file_paths: List of file paths (for backward compatibility)
                      CocoIndex will handle which files need updating
        """
        # CocoIndex handles incremental updates automatically
        # If files are provided, we could trigger specific updates,
        # but typically full repo indexing happens at connection time
        # This method is kept for interface compatibility but may be a no-op
        # or trigger incremental updates if CocoIndex supports it
        
        # For now, mark as indexed if we have a table (assumes indexing happened)
        try:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                result = conn.execute(text(f"""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = :table_name
                    )
                """), {"table_name": self._get_table_name()})
                exists = result.scalar()
                if exists:
                    self.indexed = True
        except Exception as e:
            print(f"Warning: Could not check index status: {e}")
    
    def index_past_fixes(self, fixes: List[Dict[str, Any]]):
        """
        Index past successful fixes from CodeMemory.
        
        Args:
            fixes: List of fix dictionaries with description, patch, error_signature
        """
        if not fixes:
            return
        
        # For past fixes, we could:
        # 1. Add them to the same CocoIndex table with type="fix" metadata
        # 2. Or maintain a separate table for fixes
        
        # For now, we'll add fixes to the same table with metadata
        # This requires embedding each fix and inserting into PostgreSQL
        try:
            # Get embedding for each fix
            for fix in fixes[:100]:  # Limit to 100 fixes
                if not COCOINDEX_AVAILABLE or code_to_embedding is None:
                    print("Warning: CocoIndex not available, skipping fix indexing")
                    return
                
                fix_text = f"""
Fix Description: {fix.get('description', '')}
Code Patch: {fix.get('patch', '')[:1000]}
Error Signature: {fix.get('error_signature', '')}
"""
                # Embed the fix text
                embedding = code_to_embedding.eval(fix_text)
                
                # Insert into database using SQLAlchemy with raw connection for pgvector
                from sqlalchemy import text
                # Get raw psycopg2 connection from SQLAlchemy engine
                with self.engine.raw_connection() as raw_conn:
                    try:
                        from pgvector.psycopg2 import register_vector
                        register_vector(raw_conn)
                        with raw_conn.cursor() as cur:
                            # Insert with type="fix" metadata
                            # Note: Adjust table schema based on actual CocoIndex export structure
                            cur.execute(f"""
                                INSERT INTO {self._get_table_name()} 
                                (filename, location, code, embedding, type)
                                VALUES (%s, %s, %s, %s::vector, %s)
                                ON CONFLICT (filename, location) DO NOTHING
                            """, (
                                f"fix:{fix.get('error_signature', 'unknown')}",
                                "fix",
                                fix_text,
                                embedding,
                                "fix"
                            ))
                        raw_conn.commit()
                    finally:
                        raw_conn.rollback()
            
            self.indexed = True
        except Exception as e:
            print(f"Warning: Failed to index past fixes: {e}")
            import traceback
            traceback.print_exc()
    
    def retrieve_relevant_knowledge(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant knowledge using CocoIndex PostgreSQL vector search.
        
        Args:
            query: Search query
            k: Number of results to return
            min_score: Minimum relevance score (0-1)
            
        Returns:
            List of knowledge items with relevance scores
            Format: [
                {
                    "content": "...",
                    "metadata": {"file_path": "...", "location": "...", "type": "code|fix"},
                    "relevance_score": 0.92,
                    "source": "code" or "fix"
                },
                ...
            ]
        """
        if not self.indexed:
            # Check if table exists
            try:
                from sqlalchemy import text
                with self.engine.connect() as conn:
                    result = conn.execute(text(f"""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_name = :table_name
                        )
                    """), {"table_name": self._get_table_name()})
                    exists = result.scalar()
                    if not exists:
                        return []
                    self.indexed = True
            except Exception:
                return []
        
        if not COCOINDEX_AVAILABLE or code_to_embedding is None:
            print("Warning: CocoIndex not available, cannot retrieve knowledge")
            return []
        
        try:
            # Get embedding for query
            query_vector = code_to_embedding.eval(query)
            
            # Query PostgreSQL vector store using raw connection for pgvector
            # Get raw psycopg2 connection from SQLAlchemy engine
            with self.engine.raw_connection() as raw_conn:
                try:
                    from pgvector.psycopg2 import register_vector
                    from psycopg2.extras import RealDictCursor
                    register_vector(raw_conn)
                    
                    with raw_conn.cursor(cursor_factory=RealDictCursor) as cur:
                        # Use cosine similarity (<=> operator in pgvector)
                        # Distance: 0 = identical, higher = less similar
                        # Convert to similarity score: 1 - distance
                        cur.execute(f"""
                            SELECT 
                                filename,
                                location,
                                code,
                                type,
                                (embedding <=> %s::vector) as distance
                            FROM {self._get_table_name()}
                            ORDER BY distance
                            LIMIT %s
                        """, (query_vector, k * 2))  # Get more results to filter by min_score
                        
                        results = []
                        for row in cur.fetchall():
                            distance = float(row['distance'])
                            # Convert distance to similarity score (cosine distance -> similarity)
                            # Cosine distance: 0 = identical, 2 = opposite
                            # Similarity: 1 - (distance / 2) for normalized vectors
                            similarity = max(0.0, 1.0 - (distance / 2.0))
                            
                            if similarity >= min_score:
                                file_path = row['filename']
                                # Remove "fix:" prefix if present
                                if file_path.startswith("fix:"):
                                    file_path = file_path[4:]
                                
                                results.append({
                                    "content": row['code'],
                                    "metadata": {
                                        "file_path": file_path,
                                        "location": row['location'] or "unknown",
                                        "type": row.get('type', 'code')
                                    },
                                    "relevance_score": similarity,
                                    "source": row.get('type', 'code')
                                })
                        
                        # Sort by relevance and return top k
                        results.sort(key=lambda x: x["relevance_score"], reverse=True)
                        return results[:k]
                finally:
                    raw_conn.rollback()
                    
        except Exception as e:
            print(f"Warning: Vector search failed: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def retrieve_for_planning(
        self,
        root_cause: str,
        affected_files: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Retrieve knowledge relevant for planning phase.
        
        Args:
            root_cause: Root cause description
            affected_files: List of affected file paths
            
        Returns:
            List of relevant knowledge items
        """
        query = f"""
Root cause: {root_cause}
Affected files: {', '.join(affected_files) if affected_files else 'None'}

Find similar error patterns, fixes, and codebase conventions.
"""
        return self.retrieve_relevant_knowledge(query, k=5)
    
    def retrieve_for_fix_generation(
        self,
        error_type: str,
        file_path: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieve knowledge relevant for fix generation.
        
        Args:
            error_type: Type of error
            file_path: Path to file being fixed
            
        Returns:
            List of relevant knowledge items
        """
        query = f"""
Error type: {error_type}
File: {file_path}

Find similar fixes, error handling patterns, and code examples.
"""
        return self.retrieve_relevant_knowledge(query, k=3)
