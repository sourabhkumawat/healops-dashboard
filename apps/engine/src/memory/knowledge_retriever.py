"""
Knowledge Retriever for RAG-based codebase pattern retrieval.
Manus-style Knowledge module with vector store for semantic search.
"""
from typing import List, Dict, Any, Optional
import os

# Try to import FAISS, fallback to simple in-memory if not available
try:
    from langchain.vectorstores import FAISS
    from langchain.embeddings import HuggingFaceEmbeddings
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain.schema import Document
    HAS_FAISS = True
except ImportError:
    try:
        # Try alternative imports
        from langchain_community.vectorstores import FAISS
        from langchain_community.embeddings import HuggingFaceEmbeddings
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        from langchain.schema import Document
        HAS_FAISS = True
    except ImportError:
        # Fallback to simple implementation
        HAS_FAISS = False
        print("Warning: FAISS not available. Knowledge retrieval will use simple text matching.")

class KnowledgeRetriever:
    """
    Manus-style knowledge retriever with vector store for semantic search.
    
    Indexes codebase patterns, past fixes, and best practices.
    Provides semantic search for relevant knowledge.
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
        self.vectorstore = None
        self.indexed = False
        self.has_faiss = HAS_FAISS  # Store module-level flag as instance variable
        
        if self.has_faiss:
            try:
                self.embeddings = HuggingFaceEmbeddings(
                    model_name="sentence-transformers/all-MiniLM-L6-v2"
                )
                self.text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=1000,
                    chunk_overlap=200
                )
            except Exception as e:
                print(f"Warning: Failed to initialize embeddings: {e}")
                self.has_faiss = False
        
        # Fallback storage
        if not self.has_faiss:
            self.documents: List[Dict[str, Any]] = []
    
    def index_codebase_patterns(self, file_paths: List[str]):
        """
        Index codebase files for pattern retrieval.
        
        Args:
            file_paths: List of file paths to index
        """
        if not file_paths:
            return
        
        documents = []
        
        for file_path in file_paths[:50]:  # Limit to 50 files
            try:
                content = self.gh.get_file_contents(self.repo_name, file_path)
                if not content:
                    continue
                
                if self.has_faiss:
                    # Split into chunks
                    chunks = self.text_splitter.create_documents(
                        [content],
                        metadatas=[{"file_path": file_path, "type": "code"}]
                    )
                    documents.extend(chunks)
                else:
                    # Simple storage
                    self.documents.append({
                        "content": content[:2000],  # Limit content size
                        "metadata": {"file_path": file_path, "type": "code"}
                    })
            except Exception as e:
                print(f"Warning: Failed to index file {file_path}: {e}")
                continue
        
        if self.has_faiss and documents:
            try:
                if self.vectorstore:
                    # Add to existing store
                    self.vectorstore.add_documents(documents)
                else:
                    # Create new store
                    self.vectorstore = FAISS.from_documents(documents, self.embeddings)
                self.indexed = True
            except Exception as e:
                print(f"Warning: Failed to create vector store: {e}")
                self.has_faiss = False
    
    def index_past_fixes(self, fixes: List[Dict[str, Any]]):
        """
        Index past successful fixes from CodeMemory.
        
        Args:
            fixes: List of fix dictionaries with description, patch, error_signature
        """
        if not fixes:
            return
        
        documents = []
        
        for fix in fixes[:100]:  # Limit to 100 fixes
            try:
                # Create document from fix
                fix_text = f"""
Fix Description: {fix.get('description', '')}
Code Patch: {fix.get('patch', '')[:1000]}
Error Signature: {fix.get('error_signature', '')}
"""
                
                if self.has_faiss:
                    doc = Document(
                        page_content=fix_text,
                        metadata={
                            "type": "fix",
                            "error_signature": fix.get('error_signature', ''),
                            "description": fix.get('description', '')
                        }
                    )
                    documents.append(doc)
                else:
                    self.documents.append({
                        "content": fix_text,
                        "metadata": {
                            "type": "fix",
                            "error_signature": fix.get('error_signature', ''),
                            "description": fix.get('description', '')
                        }
                    })
            except Exception as e:
                print(f"Warning: Failed to index fix: {e}")
                continue
        
        if self.has_faiss and documents:
            try:
                if self.vectorstore:
                    self.vectorstore.add_documents(documents)
                else:
                    self.vectorstore = FAISS.from_documents(documents, self.embeddings)
                self.indexed = True
            except Exception as e:
                print(f"Warning: Failed to add fixes to vector store: {e}")
    
    def retrieve_relevant_knowledge(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant knowledge with relevance scoring.
        
        Args:
            query: Search query
            k: Number of results to return
            min_score: Minimum relevance score (0-1)
            
        Returns:
            List of knowledge items with relevance scores
        """
        if not self.indexed and not self.documents:
            return []
        
        if self.has_faiss and self.vectorstore:
            try:
                # Semantic search
                docs_with_scores = self.vectorstore.similarity_search_with_score(query, k=k)
                
                results = []
                for doc, score in docs_with_scores:
                    # Convert distance to relevance (lower distance = higher relevance)
                    # FAISS returns distance, we need to convert to similarity
                    relevance = 1.0 / (1.0 + score) if score > 0 else 1.0
                    
                    if relevance >= min_score:
                        results.append({
                            "content": doc.page_content,
                            "metadata": doc.metadata,
                            "relevance_score": relevance,
                            "source": doc.metadata.get("type", "unknown")
                        })
                
                # Sort by relevance
                results.sort(key=lambda x: x["relevance_score"], reverse=True)
                return results
            except Exception as e:
                print(f"Warning: Vector search failed: {e}")
                # Fallback to simple search
                return self._simple_search(query, k, min_score)
        else:
            # Simple text matching
            return self._simple_search(query, k, min_score)
    
    def _simple_search(
        self,
        query: str,
        k: int,
        min_score: float
    ) -> List[Dict[str, Any]]:
        """
        Simple text-based search fallback.
        
        Args:
            query: Search query
            k: Number of results
            min_score: Minimum score (ignored in simple search)
            
        Returns:
            List of matching documents
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())
        
        results = []
        for doc in self.documents:
            content_lower = doc["content"].lower()
            
            # Simple word matching
            content_words = set(content_lower.split())
            common_words = query_words.intersection(content_words)
            
            if common_words:
                # Calculate simple relevance
                relevance = len(common_words) / max(len(query_words), 1)
                
                if relevance >= min_score:
                    results.append({
                        "content": doc["content"][:500],
                        "metadata": doc["metadata"],
                        "relevance_score": relevance,
                        "source": doc["metadata"].get("type", "unknown")
                    })
        
        # Sort by relevance
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results[:k]
    
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

