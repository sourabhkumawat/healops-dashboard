"""
Database migration: Add performance indexes for common query patterns

This migration adds composite indexes to optimize frequently used queries,
especially in the stats controller and incidents controller.
"""

import os
import sys
import time
from pathlib import Path

# Add the engine directory to the Python path
engine_root = Path(__file__).parent.parent
sys.path.insert(0, str(engine_root))

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from src.database.database import DATABASE_URL

def create_engine_with_retry():
    """Create database engine with proper connection settings and retry logic."""
    if "sqlite" in DATABASE_URL:
        return create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    else:
        # Use same connection pool settings as main app
        return create_engine(
            DATABASE_URL,
            pool_pre_ping=True,  # Verify connections before using them
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
            pool_timeout=30,
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000"
            }
        )

def test_connection(engine, max_retries=3, retry_delay=2):
    """Test database connection with retry logic."""
    for attempt in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                print("✅ Database connection successful")
                return True
        except OperationalError as e:
            if attempt < max_retries - 1:
                print(f"⚠️  Connection attempt {attempt + 1} failed, retrying in {retry_delay} seconds...")
                print(f"   Error: {str(e)[:100]}")
                time.sleep(retry_delay)
            else:
                print(f"❌ Failed to connect after {max_retries} attempts")
                raise
    return False

def run_migration():
    """Run the migration to add performance indexes."""

    # Create engine with proper settings
    print("Connecting to database...")
    engine = create_engine_with_retry()
    
    # Test connection first
    try:
        test_connection(engine)
    except Exception as e:
        print(f"\n❌ Cannot connect to database. Please check:")
        print(f"   1. Database server is running")
        print(f"   2. Network connectivity")
        print(f"   3. DATABASE_URL is correct")
        print(f"   4. Database credentials are valid")
        raise

    # Indexes to add for better query performance
    indexes = [
        # Incident indexes - optimize stats and filtering queries
        {
            "name": "idx_incidents_user_status",
            "table": "incidents",
            "columns": ["user_id", "status"],
            "description": "Index for filtering incidents by user and status"
        },
        {
            "name": "idx_incidents_user_severity",
            "table": "incidents",
            "columns": ["user_id", "severity"],
            "description": "Index for filtering incidents by user and severity"
        },
        {
            "name": "idx_incidents_user_created_at",
            "table": "incidents",
            "columns": ["user_id", "created_at"],
            "description": "Index for time-based incident queries"
        },
        {
            "name": "idx_incidents_user_status_created_at",
            "table": "incidents",
            "columns": ["user_id", "status", "created_at"],
            "description": "Composite index for status and time-based queries"
        },
        {
            "name": "idx_incidents_user_service",
            "table": "incidents",
            "columns": ["user_id", "service_name"],
            "description": "Index for filtering incidents by user and service"
        },
        {
            "name": "idx_incidents_user_source",
            "table": "incidents",
            "columns": ["user_id", "source"],
            "description": "Index for filtering incidents by user and source"
        },
        # LogEntry indexes - optimize log queries
        {
            "name": "idx_logs_user_timestamp",
            "table": "logs",
            "columns": ["user_id", "timestamp"],
            "description": "Index for time-based log queries"
        },
        {
            "name": "idx_logs_user_severity",
            "table": "logs",
            "columns": ["user_id", "severity"],
            "description": "Index for filtering logs by user and severity"
        },
        {
            "name": "idx_logs_user_service",
            "table": "logs",
            "columns": ["user_id", "service_name"],
            "description": "Index for filtering logs by user and service"
        },
        {
            "name": "idx_logs_user_severity_timestamp",
            "table": "logs",
            "columns": ["user_id", "severity", "timestamp"],
            "description": "Composite index for severity and time-based log queries"
        },
        # AgentPR indexes - optimize PR statistics queries
        {
            "name": "idx_agent_prs_incident_id",
            "table": "agent_prs",
            "columns": ["incident_id"],
            "description": "Index for joining agent PRs with incidents"
        },
        {
            "name": "idx_agent_prs_qa_status",
            "table": "agent_prs",
            "columns": ["qa_review_status"],
            "description": "Index for filtering PRs by QA status"
        },
        # LinearResolutionAttempt indexes
        {
            "name": "idx_linear_attempts_user_status",
            "table": "linear_resolution_attempts",
            "columns": ["user_id", "status"],
            "description": "Index for filtering linear attempts by user and status"
        },
    ]

    try:
        # Use begin() for transaction management
        with engine.begin() as conn:
            for index_def in indexes:
                index_name = index_def["name"]
                table = index_def["table"]
                columns = ", ".join(index_def["columns"])
                description = index_def["description"]

                # Check if index already exists
                check_index_sql = f"""
                SELECT COUNT(*) 
                FROM pg_indexes 
                WHERE indexname = '{index_name}';
                """

                create_index_sql = f"""
                CREATE INDEX IF NOT EXISTS {index_name}
                ON {table} ({columns});
                """

                try:
                    result = conn.execute(text(check_index_sql))
                    exists = result.scalar() > 0

                    if not exists:
                        print(f"Creating index {index_name} on {table} ({columns})...")
                        print(f"  Purpose: {description}")
                        conn.execute(text(create_index_sql))
                        print(f"✅ Successfully created index {index_name}")
                    else:
                        print(f"⏭️  Index {index_name} already exists, skipping...")

                except Exception as e:
                    print(f"⚠️  Error creating index {index_name}: {e}")
                    # Continue with other indexes even if one fails
                    # Note: Using begin() auto-commits, so we continue

            print("\n✅ Migration completed successfully!")

    except OperationalError as e:
        print(f"\n❌ Database connection error: {e}")
        print("   This might be a temporary network issue. Please try again.")
        raise
    except Exception as e:
        print(f"❌ Error running migration: {e}")
        raise

def rollback_migration():
    """Rollback the migration by dropping the indexes."""

    print("Connecting to database...")
    engine = create_engine_with_retry()
    
    # Test connection first
    try:
        test_connection(engine)
    except Exception as e:
        print(f"\n❌ Cannot connect to database: {e}")
        raise

    indexes_to_drop = [
        "idx_incidents_user_status",
        "idx_incidents_user_severity",
        "idx_incidents_user_created_at",
        "idx_incidents_user_status_created_at",
        "idx_incidents_user_service",
        "idx_incidents_user_source",
        "idx_logs_user_timestamp",
        "idx_logs_user_severity",
        "idx_logs_user_service",
        "idx_logs_user_severity_timestamp",
        "idx_agent_prs_incident_id",
        "idx_agent_prs_qa_status",
        "idx_linear_attempts_user_status",
    ]

    try:
        with engine.begin() as conn:
            for index_name in indexes_to_drop:
                try:
                    print(f"Dropping index {index_name}...")
                    conn.execute(text(f"DROP INDEX IF EXISTS {index_name};"))
                    print(f"✅ Successfully dropped index {index_name}")
                except Exception as e:
                    print(f"⚠️  Error dropping index {index_name}: {e}")
                    # Continue with other indexes

            print("\n✅ Rollback completed!")

    except OperationalError as e:
        print(f"\n❌ Database connection error: {e}")
        raise
    except Exception as e:
        print(f"❌ Error rolling back migration: {e}")
        raise

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Performance Indexes Migration")
    parser.add_argument("--rollback", action="store_true", help="Rollback the migration")

    args = parser.parse_args()

    if args.rollback:
        rollback_migration()
    else:
        run_migration()
