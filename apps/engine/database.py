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
    # PostgreSQL configuration
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # Verify connections before using them
        pool_size=10,
        max_overflow=20
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
