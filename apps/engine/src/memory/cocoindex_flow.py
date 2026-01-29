"""
CocoIndex flow definition for codebase indexing.
Uses Tree-sitter for AST-aware chunking and PostgreSQL for persistent storage.
"""
import os
import re
import cocoindex
from src.memory.cocoindex_github_source import GitHubSource, FileKey, FileValue


# Shared embedding transform for consistency between indexing and querying
@cocoindex.transform_flow()
def code_to_embedding(text: cocoindex.DataSlice[str]) -> cocoindex.DataSlice[list[float]]:
    """
    Transform code text to embedding vector.
    Must use same model for indexing and querying.
    """
    return text.transform(
        cocoindex.functions.SentenceTransformerEmbed(
            model="sentence-transformers/all-MiniLM-L6-v2"
        )
    )


# Shared function for extracting file extensions (defined at module level to avoid registration conflicts)
@cocoindex.op.function()
def extract_extension(path: str) -> str:
    """Extract file extension from path."""
    return os.path.splitext(path)[1]


@cocoindex.flow_def(name="GitHubCodeEmbedding")
def github_code_embedding_flow(
    flow_builder: cocoindex.FlowBuilder,
    data_scope: cocoindex.DataScope,
    repo_name: str,
    integration_id: int,
    ref: str = "main",
):
    """
    CocoIndex flow for indexing GitHub repository code.
    
    Args:
        flow_builder: CocoIndex flow builder
        data_scope: Data scope for the flow
        repo_name: Repository name in format "owner/repo"
        integration_id: GitHub integration ID for authentication
        ref: Branch or commit SHA (default: "main")
    """
    # 1. Add GitHub source
    data_scope["files"] = flow_builder.add_source(
        GitHubSource(
            repo_name=repo_name,
            ref=ref,
            integration_id=integration_id,
        )
    )
    
    # Collector for code embeddings
    code_embeddings = data_scope.add_collector()
    
    # 2. Extract file extension for language detection
    @cocoindex.op.function()
    def extract_extension(path: str) -> str:
        """Extract file extension from path."""
        return os.path.splitext(path)[1]
    
    # 3. Process each file: chunk using Tree-sitter, embed, collect
    with data_scope["files"].row() as file:
        # Extract extension for language detection
        file["extension"] = file["path"].transform(extract_extension)
        
        # Use Tree-sitter for AST-aware chunking
        # SplitRecursively respects code structure (functions, classes, etc.)
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language=file["extension"],  # Tree-sitter uses extension to detect language
            chunk_size=1000,  # Maximum tokens per chunk
            chunk_overlap=300,  # Overlap between chunks for context preservation
        )
        
        # 4. Embed each chunk
        with file["chunks"].row() as chunk:
            # Use shared embedding transform for consistency
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            
            # Collect chunk with metadata
            code_embeddings.collect(
                filename=file["path"],  # Full file path
                location=chunk["location"],  # Chunk location (e.g., "lines:45-78")
                code=chunk["text"],  # Actual code content
                embedding=chunk["embedding"],  # Vector embedding
                type="code",  # Metadata type for filtering
            )
    
    # 5. Export to PostgreSQL with pgvector
    code_embeddings.export(
        "code_embeddings",
        cocoindex.storages.Postgres(),
        primary_key_fields=["filename", "location"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            )
        ],
    )


def get_flow_for_repo(repo_name: str, integration_id: int, ref: str = "main"):
    """
    Get CocoIndex flow instance for a specific repository.
    
    Args:
        repo_name: Repository name "owner/repo"
        integration_id: GitHub integration ID
        ref: Branch or commit SHA
        
    Returns:
        CocoIndex flow instance
    """
    # Create flow with repository-specific parameters
    # Note: CocoIndex flows are typically defined at module level,
    # but we need to pass repo_name and integration_id dynamically
    # We'll handle this by creating a wrapper flow factory
    
    # For now, return the flow definition - actual execution will pass params
    return github_code_embedding_flow


def _make_flow_func(repo_name: str, integration_id: int, ref: str):
    """Build flow_func closure for a given repo/integration/ref (shared by sync and async)."""

    def flow_func(flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope):
        data_scope["files"] = flow_builder.add_source(
            GitHubSource(
                repo_name=repo_name,
                ref=ref,
                integration_id=integration_id,
            )
        )
        code_embeddings = data_scope.add_collector()
        with data_scope["files"].row() as file:
            file["extension"] = file["path"].transform(extract_extension)
            file["chunks"] = file["content"].transform(
                cocoindex.functions.SplitRecursively(),
                language=file["extension"],
                chunk_size=1000,
                chunk_overlap=300,
            )
            with file["chunks"].row() as chunk:
                chunk["embedding"] = chunk["text"].call(code_to_embedding)
                code_embeddings.collect(
                    filename=file["path"],
                    location=chunk["location"],
                    code=chunk["text"],
                    embedding=chunk["embedding"],
                    type="code",
                )
        code_embeddings.export(
            "code_embeddings",
            cocoindex.storages.Postgres(),
            primary_key_fields=["filename", "location"],
            vector_indexes=[
                cocoindex.VectorIndexDef(
                    field_name="embedding",
                    metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
                )
            ],
        )

    return flow_func


def execute_flow_update(repo_name: str, integration_id: int, ref: str = "main"):
    """
    Execute CocoIndex flow update for a repository.
    This triggers indexing (initial or incremental).
    
    Args:
        repo_name: Repository name "owner/repo"
        integration_id: GitHub integration ID
        ref: Branch or commit SHA
    """
    # Set COCOINDEX_DATABASE_URL if not set (defaults to DATABASE_URL)
    if not os.getenv("COCOINDEX_DATABASE_URL"):
        from src.database.database import DATABASE_URL
        os.environ["COCOINDEX_DATABASE_URL"] = DATABASE_URL
    
    # Initialize CocoIndex if not already initialized
    try:
        cocoindex.init()
    except Exception:
        pass  # Already initialized or not needed
    
    # Execute flow update
    # CocoIndex handles incremental updates automatically (only changed files)
    try:
        # Create a flow instance with the parameters
        # The flow function accepts repo_name, integration_id, ref as parameters
        # We need to create a flow using open_flow with the flow definition
        # Sanitize flow name: only letters, digits, and underscores allowed
        # Replace invalid characters with underscores
        sanitized_repo = re.sub(r'[^a-zA-Z0-9_]', '_', repo_name)
        flow_name = f"GitHubCodeEmbedding_{integration_id}_{sanitized_repo}"
        flow_func = _make_flow_func(repo_name, integration_id, ref)
        flow = cocoindex.open_flow(flow_name, flow_func)
        
        # Setup the flow (creates tables, indexes, etc.)
        flow.setup(report_to_stdout=False)
        
        # Execute the flow update
        result = flow.update()
        print(f"✅ CocoIndex flow update completed for {repo_name}")
        if result is not None:
            stats = getattr(result, "stats", result)
            if stats is not None:
                print(f"   Stats: {stats}")
        return True
    except Exception as e:
        print(f"❌ CocoIndex flow update failed for {repo_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def execute_flow_update_async(repo_name: str, integration_id: int, ref: str = "main"):
    """
    Execute CocoIndex flow update for a repository (async).
    Use this when called from async code / inside an event loop to avoid blocking and RuntimeWarnings.

    Args:
        repo_name: Repository name "owner/repo"
        integration_id: GitHub integration ID
        ref: Branch or commit SHA
    """
    if not os.getenv("COCOINDEX_DATABASE_URL"):
        from src.database.database import DATABASE_URL
        os.environ["COCOINDEX_DATABASE_URL"] = DATABASE_URL
    try:
        cocoindex.init()
    except Exception:
        pass
    try:
        sanitized_repo = re.sub(r'[^a-zA-Z0-9_]', '_', repo_name)
        flow_name = f"GitHubCodeEmbedding_{integration_id}_{sanitized_repo}"
        flow_func = _make_flow_func(repo_name, integration_id, ref)
        flow = cocoindex.open_flow(flow_name, flow_func)
        await flow.setup_async(report_to_stdout=False)
        result = await flow.update_async(print_stats=False)
        print(f"✅ CocoIndex flow update completed for {repo_name}")
        if result is not None:
            stats = getattr(result, "stats", result)
            if stats is not None:
                print(f"   Stats: {stats}")
        return True
    except Exception as e:
        print(f"❌ CocoIndex flow update failed for {repo_name}: {e}")
        import traceback
        traceback.print_exc()
        return False
