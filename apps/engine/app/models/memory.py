from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from sqlalchemy.sql import func
from app.db.session import Base

class AgentMemoryError(Base):
    __tablename__ = "agent_memory_errors"

    id = Column(Integer, primary_key=True, index=True)
    error_signature = Column(String, unique=True, index=True, nullable=False)
    context = Column(Text, nullable=False)  # Detailed error context/stacktrace
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Metadata for better querying
    language = Column(String, nullable=True)
    framework = Column(String, nullable=True)

class AgentMemoryFix(Base):
    __tablename__ = "agent_memory_fixes"

    id = Column(Integer, primary_key=True, index=True)
    error_signature = Column(String, ForeignKey("agent_memory_errors.error_signature"), index=True, nullable=False)
    description = Column(Text, nullable=False)
    code_patch = Column(Text, nullable=False)
    success_rate = Column(Integer, default=0) # Track how often this fix works
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

class AgentRepoContext(Base):
    __tablename__ = "agent_repo_context"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String, unique=True, index=True, nullable=False)
    summary = Column(Text, nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
