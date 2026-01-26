"""
Database migration: Add LinearResolutionAttempt table

This migration adds the linear_resolution_attempts table to track
automated resolution attempts for Linear tickets.
"""

import os
import sys
from pathlib import Path

# Add the engine directory to the Python path
engine_root = Path(__file__).parent.parent
sys.path.insert(0, str(engine_root))

from sqlalchemy import create_engine, text
from src.database.database import DATABASE_URL

def run_migration():
    """Run the migration to add linear_resolution_attempts table."""

    # Get database URL
    engine = create_engine(DATABASE_URL)

    # SQL to create the table
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS linear_resolution_attempts (
        id SERIAL PRIMARY KEY,

        -- Integration and ticket info
        integration_id INTEGER NOT NULL REFERENCES integrations(id),
        user_id INTEGER NOT NULL REFERENCES users(id),
        issue_id VARCHAR NOT NULL,
        issue_identifier VARCHAR NOT NULL,
        issue_title VARCHAR(255),

        -- Agent info
        agent_name VARCHAR NOT NULL,
        agent_version VARCHAR,

        -- Resolution tracking
        status VARCHAR DEFAULT 'CLAIMED',
        claimed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        started_at TIMESTAMP WITH TIME ZONE,
        completed_at TIMESTAMP WITH TIME ZONE,

        -- Analysis results
        confidence_score VARCHAR,
        ticket_type VARCHAR,
        complexity VARCHAR,
        estimated_effort VARCHAR,

        -- Resolution details
        resolution_summary TEXT,
        failure_reason TEXT,

        -- Metadata (store PR URLs, commit hashes, etc.)
        resolution_metadata JSONB,

        -- Audit trail
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """

    # SQL to create indexes
    create_indexes_sql = [
        "CREATE INDEX IF NOT EXISTS idx_linear_resolution_attempts_integration_id ON linear_resolution_attempts(integration_id);",
        "CREATE INDEX IF NOT EXISTS idx_linear_resolution_attempts_user_id ON linear_resolution_attempts(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_linear_resolution_attempts_issue_id ON linear_resolution_attempts(issue_id);",
        "CREATE INDEX IF NOT EXISTS idx_linear_resolution_attempts_issue_identifier ON linear_resolution_attempts(issue_identifier);",
        "CREATE INDEX IF NOT EXISTS idx_linear_resolution_attempts_status ON linear_resolution_attempts(status);",
        "CREATE INDEX IF NOT EXISTS idx_linear_resolution_attempts_claimed_at ON linear_resolution_attempts(claimed_at);"
    ]

    try:
        with engine.connect() as conn:
            # Create the table
            print("Creating linear_resolution_attempts table...")
            conn.execute(text(create_table_sql))

            # Create indexes
            print("Creating indexes...")
            for index_sql in create_indexes_sql:
                conn.execute(text(index_sql))

            # Commit the transaction
            conn.commit()

            print("✅ Successfully created linear_resolution_attempts table and indexes")

    except Exception as e:
        print(f"❌ Error running migration: {e}")
        raise

def rollback_migration():
    """Rollback the migration by dropping the table."""

    engine = create_engine(DATABASE_URL)

    try:
        with engine.connect() as conn:
            print("Dropping linear_resolution_attempts table...")
            conn.execute(text("DROP TABLE IF EXISTS linear_resolution_attempts;"))
            conn.commit()
            print("✅ Successfully dropped linear_resolution_attempts table")

    except Exception as e:
        print(f"❌ Error rolling back migration: {e}")
        raise

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Linear Resolution Attempts Migration")
    parser.add_argument("--rollback", action="store_true", help="Rollback the migration")

    args = parser.parse_args()

    if args.rollback:
        rollback_migration()
    else:
        run_migration()