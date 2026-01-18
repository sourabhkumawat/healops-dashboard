"""
Enable pgvector extension in PostgreSQL database.
Safe to run multiple times (checks if extension already exists).
"""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from src.database.database import engine

def enable_pgvector():
    """Enable pgvector extension in PostgreSQL database."""
    print("🔧 Enabling pgvector extension in PostgreSQL...")
    
    try:
        with engine.begin() as conn:
            # Check if extension already exists
            result = conn.execute(text("""
                SELECT EXISTS(
                    SELECT 1 FROM pg_extension WHERE extname = 'vector'
                )
            """))
            exists = result.scalar()
            
            if exists:
                print("✅ pgvector extension already enabled")
                return True
            
            # Enable the extension
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # begin() context manager auto-commits
            print("✅ pgvector extension enabled successfully")
            return True
            
    except Exception as e:
        print(f"❌ Error enabling pgvector extension: {e}")
        if "does not exist" in str(e).lower():
            print("   💡 Hint: Install pgvector on your PostgreSQL server first")
            print("   See: https://github.com/pgvector/pgvector#installation")
        return False

if __name__ == "__main__":
    success = enable_pgvector()
    sys.exit(0 if success else 1)
