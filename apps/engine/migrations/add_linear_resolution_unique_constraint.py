"""
Database migration: Add unique constraint to prevent race conditions

This migration adds a unique constraint to ensure only one active resolution
attempt can exist per integration-issue pair, preventing race conditions.
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
    """Run the migration to add unique constraint for active resolution attempts."""

    # Get database URL
    engine = create_engine(DATABASE_URL)

    # SQL to add unique constraint - prevent multiple active attempts for same issue
    add_unique_constraint_sql = """
    ALTER TABLE linear_resolution_attempts
    ADD CONSTRAINT unique_active_resolution_per_issue
    EXCLUDE USING btree (integration_id WITH =, issue_id WITH =)
    WHERE (status IN ('CLAIMED', 'ANALYZING', 'IMPLEMENTING', 'TESTING'));
    """

    # Alternative approach if EXCLUDE constraint is not supported:
    fallback_unique_index_sql = """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_resolution_per_issue
    ON linear_resolution_attempts (integration_id, issue_id)
    WHERE status IN ('CLAIMED', 'ANALYZING', 'IMPLEMENTING', 'TESTING');
    """

    try:
        with engine.connect() as conn:
            try:
                # Try to use EXCLUDE constraint first (PostgreSQL)
                print("Adding unique constraint to prevent race conditions...")
                conn.execute(text(add_unique_constraint_sql))
                conn.commit()
                print("✅ Successfully added EXCLUDE constraint for active resolution attempts")

            except Exception as e:
                print(f"⚠️  EXCLUDE constraint not supported, falling back to unique index: {e}")

                # Rollback the failed transaction
                conn.rollback()

                # Use unique partial index as fallback
                print("Creating unique partial index instead...")
                conn.execute(text(fallback_unique_index_sql))
                conn.commit()
                print("✅ Successfully added unique partial index for active resolution attempts")

    except Exception as e:
        print(f"❌ Error running migration: {e}")
        raise

def rollback_migration():
    """Rollback the migration by dropping the constraint/index."""

    engine = create_engine(DATABASE_URL)

    try:
        with engine.connect() as conn:
            # Try to drop constraint first
            try:
                print("Dropping unique constraint...")
                conn.execute(text("ALTER TABLE linear_resolution_attempts DROP CONSTRAINT IF EXISTS unique_active_resolution_per_issue;"))
                print("✅ Successfully dropped unique constraint")
            except Exception as e:
                print(f"⚠️  Constraint not found, trying index: {e}")
                conn.rollback()

            # Drop the unique index
            print("Dropping unique index...")
            conn.execute(text("DROP INDEX IF EXISTS idx_unique_active_resolution_per_issue;"))
            conn.commit()
            print("✅ Successfully dropped unique index")

    except Exception as e:
        print(f"❌ Error rolling back migration: {e}")
        raise

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Linear Resolution Unique Constraint Migration")
    parser.add_argument("--rollback", action="store_true", help="Rollback the migration")

    args = parser.parse_args()

    if args.rollback:
        rollback_migration()
    else:
        run_migration()