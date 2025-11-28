"""
Database Migration Script for HealOps
Creates all necessary tables in PostgreSQL database
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import engine, Base
from models import (
    Incident, 
    LogEntry, 
    IntegrationStatus, 
    User, 
    Integration, 
    ApiKey
)

def create_tables():
    """Create all tables in the database"""
    print("🔧 Creating database tables...")
    print(f"📍 Database URL: {engine.url}")
    
    try:
        # Drop all tables (use with caution!)
        # Base.metadata.drop_all(bind=engine)
        # print("✅ Dropped existing tables")
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        print("✅ Created all tables successfully!")
        
        # Print created tables
        print("\n📋 Created tables:")
        for table in Base.metadata.sorted_tables:
            print(f"  - {table.name}")
            
        return True
        
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_tables():
    """Verify that all tables were created"""
    from sqlalchemy import inspect
    
    print("\n🔍 Verifying tables...")
    inspector = inspect(engine)
    
    expected_tables = ['incidents', 'logs', 'integration_status', 'users', 'integrations', 'api_keys']
    existing_tables = inspector.get_table_names()
    
    print(f"\n📊 Existing tables: {existing_tables}")
    
    for table in expected_tables:
        if table in existing_tables:
            print(f"  ✅ {table}")
            
            # Show columns
            columns = inspector.get_columns(table)
            print(f"     Columns: {', '.join([col['name'] for col in columns])}")
        else:
            print(f"  ❌ {table} - MISSING!")
    
    return all(table in existing_tables for table in expected_tables)

if __name__ == "__main__":
    print("=" * 60)
    print("HealOps Database Migration")
    print("=" * 60)
    
    success = create_tables()
    
    if success:
        verify_tables()
        print("\n✨ Migration completed successfully!")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)
