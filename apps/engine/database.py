import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Use PostgreSQL for production (Supabase)
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://postgres:pPlztsKITYyGzUEUbFGKnDoFzyZTneMB@switchyard.proxy.rlwy.net:10514/railway"
)

# Create engine with appropriate connection args
if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # PostgreSQL configuration optimized for 2 vCPUs and 4GB RAM
    # Reduced pool size to prevent memory exhaustion
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # Verify connections before using them
        pool_size=5,  # Reduced from 10 to 5 for lower memory usage
        max_overflow=10,  # Reduced from 20 to 10
        pool_recycle=3600,  # Recycle connections after 1 hour (prevents stale connections)
        pool_timeout=30,  # Timeout for getting connection from pool
        connect_args={
            "connect_timeout": 10,  # Connection timeout in seconds
            "options": "-c statement_timeout=30000"  # 30 second statement timeout
        }
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
