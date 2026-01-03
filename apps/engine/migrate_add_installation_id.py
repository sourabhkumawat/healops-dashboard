"""
Migration script to add installation_id column to integrations table.
Run this after updating the models.py file.
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import engine
from sqlalchemy import text

def add_installation_id_column():
    """Add installation_id column to integrations table."""
    print("🔧 Adding installation_id column to integrations table...")
    
    try:
        with engine.connect() as conn:
            # Check if column already exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='integrations' AND column_name='installation_id'
            """))
            
            if result.fetchone():
                print("✅ Column installation_id already exists")
                return True
            
            # Add the column
            conn.execute(text("""
                ALTER TABLE integrations 
                ADD COLUMN installation_id INTEGER
            """))
            
            # Create index on installation_id
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_integrations_installation_id 
                ON integrations(installation_id)
            """))
            
            conn.commit()
            print("✅ Successfully added installation_id column and index")
            return True
            
    except Exception as e:
        print(f"❌ Error adding installation_id column: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("HealOps Database Migration: Add installation_id")
    print("=" * 60)
    
    success = add_installation_id_column()
    
    if success:
        print("\n✨ Migration completed successfully!")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)

