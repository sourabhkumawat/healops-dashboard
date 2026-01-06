from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from database import Base

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


class AgentEvent(Base):
    """Store event stream events for debugging and analysis."""
    __tablename__ = "agent_events"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), index=True, nullable=False)
    event_type = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    agent_name = Column(String, nullable=True)
    data = Column(JSONB, nullable=False)  # Event data payload


class AgentPlan(Base):
    """Store plans for analysis and debugging."""
    __tablename__ = "agent_plans"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), index=True, nullable=False)
    plan = Column(JSONB, nullable=False)  # Plan steps
    plan_version = Column(Integer, default=1)  # Version for replanning
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="active")  # active, completed, failed


class AgentWorkspace(Base):
    """Store complete workspace state for learning, debugging, and recovery."""
    __tablename__ = "agent_workspaces"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), index=True, nullable=False)
    files = Column(JSONB, nullable=False)  # All file contents: {file_path: content}
    plan = Column(JSONB, nullable=True)  # Plan/todo state
    notes = Column(JSONB, nullable=True)  # Notes from workspace
    files_read = Column(JSONB, nullable=True)  # List of file paths that were read
    files_modified = Column(JSONB, nullable=True)  # List of file paths that were modified
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="active")  # active, completed, failed


class AgentLearningPattern(Base):
    """Store learned patterns from past incidents for better future fixes."""
    __tablename__ = "agent_learning_patterns"

    id = Column(Integer, primary_key=True, index=True)
    error_type = Column(String, index=True, nullable=False)
    error_signature_pattern = Column(String, index=True, nullable=True)
    typical_files_read = Column(JSONB, nullable=True)  # List of file paths typically read
    typical_files_modified = Column(JSONB, nullable=True)  # List of file paths typically modified
    context_files = Column(JSONB, nullable=True)  # Files read but not modified
    exploration_pattern = Column(Text, nullable=True)  # Description of exploration approach
    fix_pattern = Column(Text, nullable=True)  # Description of fix approach
    success_count = Column(Integer, default=0)
    total_attempts = Column(Integer, default=0)
    confidence_score = Column(Integer, default=0)  # 0-100, based on success rate
    last_used = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
