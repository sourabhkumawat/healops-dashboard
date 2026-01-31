"""
Knowledge Retriever using CocoIndex for RAG-based codebase pattern retrieval.
Uses CocoIndex with Tree-sitter AST-aware chunking and PostgreSQL storage.
"""
from typing import List, Dict, Any, Optional
import os
import time

from src.utils.observability import log_phase

# Lazy import CocoIndex dependencies to avoid import errors when module not installed
try:
    from src.memory.cocoindex_flow import code_to_embedding, github_code_embedding_flow
    COCOINDEX_AVAILABLE = True
except ImportError:
    COCOINDEX_AVAILABLE = False
    code_to_embedding = None
    github_code_embedding_flow = None

from src.database.database import DATABASE_URL, engine

# Embedding dimension for sentence-transformers/all-MiniLM-L6-v2 (must match CocoIndex flow)
EMBEDDING_DIM = 384


class KnowledgeRetriever:
    """
    CocoIndex-based knowledge retriever with PostgreSQL vector storage.
    
    Indexes codebase patterns, past fixes, and best practices using
    CocoIndex with AST-aware chunking via Tree-sitter.
    """
    
    def __init__(self, github_integration, repo_name: str, integration_id: Optional[int] = None):
        """
        Initialize knowledge retriever.
        
        Args:
            github_integration: GitHub integration instance
            repo_name: Repository name in format "owner/repo"
            integration_id: Database integration ID (required for table name resolution)
        """
        self.gh = github_integration
        self.repo_name = repo_name
        self.indexed = False
        
        # Get integration_id - prefer explicit parameter, then try to extract
        if integration_id:
            self.integration_id = integration_id
        else:
            # Try to get from github_integration (may not be available)
            self.integration_id = getattr(github_integration, 'integration_id', None)
            if not self.integration_id and hasattr(github_integration, '_integration_id'):
                self.integration_id = github_integration._integration_id
        
        # Use SQLAlchemy engine for database connections
        # For pgvector queries, we'll get raw connections from the engine
        self.engine = engine
    
    def _get_table_name(self) -> str:
        """
        Get the CocoIndex table name for this repository.
        
        CocoIndex creates tables with naming pattern:
        githubcodeembedding_{integration_id}_{sanitized_repo}__code_embeddings
        
        PostgreSQL has a 63-character limit for identifiers, and CocoIndex truncates automatically.
        """
        if not self.integration_id:
            # Fallback if integration_id not available (shouldn't happen in production)
            print("Warning: integration_id not available, using fallback table name")
            return "code_embeddings"
        
        # Match CocoIndex flow naming convention from execute_flow_update
        import re
        sanitized_repo = re.sub(r'[^a-zA-Z0-9_]', '_', self.repo_name)
        
        # CocoIndex naming: GitHubCodeEmbedding_{integration_id}_{sanitized_repo}
        # Table name: {flow_name_lowercase}__code_embeddings
        # PostgreSQL truncates to 63 chars automatically
        table_name = f"githubcodeembedding_{self.integration_id}_{sanitized_repo}__code_embeddings"
        
        # Convert to lowercase (PostgreSQL identifiers are case-insensitive unless quoted)
        table_name = table_name.lower()
        
        return table_name
    
    def _get_table_name_for_db(self) -> str:
        """
        Table name for use in SQL, truncated to PostgreSQL's 63-char identifier limit.
        Use this for existence checks, CREATE, INSERT, and SELECT so we match the
        actual stored name when CocoIndex or we create the table.
        """
        name = self._get_table_name()
        return name[:63] if len(name) > 63 else name
    
    def _ensure_table_exists(self) -> bool:
        """
        Create the CocoIndex table if it does not exist, so retrieval and fix indexing can proceed.
        Schema matches CocoIndex export: filename, location, code, embedding vector(384), type.
        Returns True if the table exists (after possibly creating it), False on error.
        """
        from sqlalchemy import text
        table_name_safe = self._get_table_name_for_db()
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = :table_name
                    )
                """), {"table_name": table_name_safe})
                if result.scalar():
                    return True
                # Create pgvector extension if needed
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
            # Create table and index in separate connection so DDL commits
            with self.engine.connect() as conn:
                # Schema matches CocoIndex: primary_key_fields=["filename", "location"], vector on embedding
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {table_name_safe} (
                        filename TEXT NOT NULL,
                        location TEXT NOT NULL,
                        code TEXT,
                        embedding vector({EMBEDDING_DIM}),
                        type TEXT,
                        PRIMARY KEY (filename, location)
                    )
                """))
                conn.commit()
            with self.engine.connect() as conn:
                # HNSW works on empty tables and builds incrementally (ivfflat requires data first)
                try:
                    conn.execute(text(f"""
                        CREATE INDEX IF NOT EXISTS ix_{table_name_safe.replace('.', '_')[:50]}_embedding
                        ON {table_name_safe} USING hnsw (embedding vector_cosine_ops)
                    """))
                    conn.commit()
                except Exception as idx_err:
                    conn.rollback()
                    # Older pgvector may not have HNSW; table is still usable (sequential scan)
                    print(f"Note: Could not create vector index (hnsw): {idx_err}")
            print(f"✅ CocoIndex table created: {table_name_safe}")
            return True
        except Exception as e:
            print(f"Warning: Could not create CocoIndex table {table_name_safe}: {e}")
            import traceback
            traceback.print_exc()
            return False

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
        
        # Check if CocoIndex table exists; create it if missing so we can proceed
        try:
            from sqlalchemy import text
            table_name = self._get_table_name_for_db()
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public'
                        AND table_name = :table_name
                    )
                """), {"table_name": table_name})
                exists = result.scalar()
                if exists:
                    self.indexed = True
                    print(f"✅ CocoIndex table found: {table_name}")
                else:
                    print(f"⚠️  CocoIndex table not found: {table_name}")
                    if self._ensure_table_exists():
                        self.indexed = True
                        print(f"✅ Proceeding with created table: {table_name}")
        except Exception as e:
            print(f"Warning: Could not check index status: {e}")
            import traceback
            traceback.print_exc()
    
    def index_past_fixes(self, fixes: List[Dict[str, Any]]):
        """
        Index past successful fixes from CodeMemory.
        
        Args:
            fixes: List of fix dictionaries with description, patch, error_signature
        """
        if not fixes:
            return
        
        # For past fixes, we'll add them to the same CocoIndex table with type="fix" metadata
        # This requires embedding each fix and inserting into PostgreSQL
        if not COCOINDEX_AVAILABLE:
            print("Warning: CocoIndex not available, skipping fix indexing")
            return
        
        # Ensure table exists so inserts can proceed
        if not self._ensure_table_exists():
            print("Warning: Could not ensure CocoIndex table exists, skipping fix indexing")
            return
        
        try:
            import numpy as np
            _t0 = time.time()
            # Use sentence-transformers directly for embedding generation (same model as CocoIndex)
            # This ensures consistency with CocoIndex embeddings
            # Use sentence-transformers directly for embedding generation
            try:
                from sentence_transformers import SentenceTransformer
                # Use same model as CocoIndex flow
                model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
                log_phase("embed_model_loaded", duration_sec=time.time() - _t0, op="index_past_fixes", cached=False)
            except ImportError:
                print("Warning: sentence-transformers not available, skipping fix indexing")
                return
            
            # Get embedding for each fix
            _index_start = time.time()
            for fix in fixes[:100]:  # Limit to 100 fixes
                fix_text = f"""
Fix Description: {fix.get('description', '')}
Code Patch: {fix.get('patch', '')[:1000]}
Error Signature: {fix.get('error_signature', '')}
"""
                # Generate embedding using sentence-transformers directly
                embedding = model.encode(fix_text, convert_to_numpy=True)
                
                # Convert numpy array to list for pgvector
                if isinstance(embedding, np.ndarray):
                    embedding = embedding.tolist()
                
                # Insert into database using SQLAlchemy with raw connection for pgvector
                from sqlalchemy import text
                # Get raw psycopg2 connection from SQLAlchemy engine
                raw_conn = self.engine.raw_connection()
                try:
                    try:
                        from pgvector.psycopg2 import register_vector
                    except ImportError:
                        print("Error: pgvector package not installed. Install with: pip install pgvector")
                        raise
                    # Get the actual psycopg2 connection from SQLAlchemy's connection proxy
                    actual_conn = getattr(raw_conn, 'driver_connection', None) or getattr(raw_conn, 'connection', None) or raw_conn
                    register_vector(actual_conn, globally=False)
                    with raw_conn.cursor() as cur:
                        table_name = self._get_table_name_for_db()
                        # Insert with type="fix" metadata
                        # Schema matches CocoIndex: (filename, location, code, embedding, type)
                        # Primary key is (filename, location) as defined in CocoIndex export
                        # Use unquoted table name (PostgreSQL lowercases unquoted identifiers)
                        cur.execute(f"""
                            INSERT INTO {table_name} 
                            (filename, location, code, embedding, type)
                            VALUES (%s, %s, %s, %s::vector, %s)
                            ON CONFLICT (filename, location) DO UPDATE
                            SET code = EXCLUDED.code,
                                embedding = EXCLUDED.embedding,
                                type = EXCLUDED.type
                        """, (
                            f"fix:{fix.get('error_signature', 'unknown')}",
                            "fix",
                            fix_text,
                            embedding,
                            "fix"
                        ))
                    raw_conn.commit()
                except Exception as e:
                    raw_conn.rollback()
                    print(f"Warning: Failed to insert fix: {e}")
                    raise
                finally:
                    raw_conn.close()
            
            self.indexed = True
            log_phase(
                "index_past_fixes_done",
                duration_sec=time.time() - _t0,
                fixes_indexed=min(len(fixes), 100),
                integration_id=self.integration_id,
            )
        except Exception as e:
            log_phase(
                "index_past_fixes_failed",
                duration_sec=time.time() - _t0,
                error=str(e)[:200],
                integration_id=self.integration_id,
            )
            print(f"⚠️  Warning: Failed to index past fixes: {e}")
            print(f"   Table: {self._get_table_name()}")
            print(f"   Integration ID: {self.integration_id}")
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
            # Check if table exists; create if missing so we can proceed (empty results until indexed)
            try:
                from sqlalchemy import text
                table_name = self._get_table_name_for_db()
                with self.engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = 'public'
                            AND table_name = :table_name
                        )
                    """), {"table_name": table_name})
                    exists = result.scalar()
                    if not exists:
                        print(f"⚠️  CocoIndex table not found: {table_name}")
                        if not self._ensure_table_exists():
                            return []
                        # Table created; proceed with retrieval (will return [] until data exists)
                    self.indexed = True
            except Exception as e:
                print(f"Warning: Error checking table existence: {e}")
                return []
        
        if not COCOINDEX_AVAILABLE:
            print("Warning: CocoIndex not available, cannot retrieve knowledge")
            return []
        
        try:
            import numpy as np
            _t0 = time.time()
            # Use sentence-transformers directly for query embedding (same model as CocoIndex)
            try:
                from sentence_transformers import SentenceTransformer
                # Use same model as CocoIndex flow
                model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
                log_phase("embed_model_loaded", duration_sec=time.time() - _t0, op="retrieve_knowledge", cached=False)
            except ImportError:
                print("Warning: sentence-transformers not available, cannot retrieve knowledge")
                return []
            
            # Generate query embedding
            query_vector = model.encode(query, convert_to_numpy=True)
            
            # Convert numpy array to list for pgvector
            if isinstance(query_vector, np.ndarray):
                query_vector = query_vector.tolist()
            
            # Query PostgreSQL vector store using raw connection for pgvector
            # Get raw psycopg2 connection from SQLAlchemy engine
            raw_conn = self.engine.raw_connection()
            try:
                try:
                    from pgvector.psycopg2 import register_vector
                    from psycopg2.extras import RealDictCursor
                except ImportError:
                    print("Error: pgvector package not installed. Install with: pip install pgvector")
                    raise
                # Get the actual psycopg2 connection from SQLAlchemy's connection proxy
                # Use driver_connection (new API) or connection (deprecated but works)
                actual_conn = getattr(raw_conn, 'driver_connection', None) or getattr(raw_conn, 'connection', None) or raw_conn
                register_vector(actual_conn, globally=False)
                
                with raw_conn.cursor(cursor_factory=RealDictCursor) as cur:
                        table_name = self._get_table_name_for_db()
                        # Use cosine similarity (<=> operator in pgvector)
                        # Distance: 0 = identical, higher = less similar
                        # Convert to similarity score: 1 - distance
                        # Use proper quoting for table name (PostgreSQL identifiers are case-sensitive when quoted)
                        # Since CocoIndex creates lowercase table names, we can use unquoted or quoted lowercase
                        cur.execute(f"""
                            SELECT 
                                filename,
                                location,
                                code,
                                type,
                                (embedding <=> %s::vector) as distance
                            FROM {table_name}
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
                        out = results[:k]
                        log_phase(
                            "retrieve_knowledge_done",
                            duration_sec=time.time() - _t0,
                            k=k,
                            result_count=len(out),
                            integration_id=self.integration_id,
                        )
                        return out
            finally:
                raw_conn.close()
                    
        except Exception as e:
            log_phase(
                "retrieve_knowledge_failed",
                duration_sec=time.time() - _t0,
                k=k,
                error=str(e)[:200],
                integration_id=self.integration_id,
            )
            print(f"⚠️  Warning: Vector search failed: {e}")
            print(f"   Table: {self._get_table_name()}")
            print(f"   Integration ID: {self.integration_id}")
            print(f"   Repo: {self.repo_name}")
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
