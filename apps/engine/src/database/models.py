from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, Enum, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
import enum
from src.database.database import Base

class IncidentStatus(str, enum.Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    HEALING = "HEALING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"

class IncidentSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(String)
    status = Column(String, default=IncidentStatus.OPEN)
    severity = Column(String, default=IncidentSeverity.MEDIUM)
    service_name = Column(String, index=True)
    
    # New fields for Phase 11
    source = Column(String, nullable=True)  # github
    log_ids = Column(JSON, default=[])  # List of related log IDs
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    integration_id = Column(Integer, ForeignKey("integrations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # User who owns this incident
    repo_name = Column(String, nullable=True)  # Repository name in format "owner/repo" for PR creation

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Store the raw log or event that triggered this
    trigger_event = Column(JSON)
    
    # Store metadata_json from the log for easy access
    metadata_json = Column(JSON, nullable=True)
    
    # AI Analysis
    root_cause = Column(String, nullable=True)
    reasoning_trace = Column(JSON, nullable=True)
    
    # Healing
    action_taken = Column(String, nullable=True)
    action_result = Column(JSON, nullable=True)

class LogEntry(Base):
    __tablename__ = "logs"
    
    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, index=True)
    level = Column(String)
    message = Column(String)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    metadata_json = Column(JSONB, nullable=True)  # JSONB for better performance and GIN indexing
    
    # New fields for Phase 11
    source = Column(String, index=True, nullable=True)
    severity = Column(String, index=True, nullable=True)
    integration_id = Column(Integer, ForeignKey("integrations.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # User who owns this log

class IntegrationStatus(Base):
    __tablename__ = "integration_status"
    
    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(Integer, ForeignKey("integrations.id"))
    last_log_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="PENDING")  # ACTIVE, STALE, DISCONNECTED
    details = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="admin")
    name = Column(String, nullable=True)  # User display name
    organization_name = Column(String, nullable=True)  # Organization name
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class IntegrationProvider(str, enum.Enum):
    GITHUB = "GITHUB"

class IntegrationStatusEnum(str, enum.Enum):
    PENDING = "PENDING"
    CONFIGURING = "CONFIGURING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"

class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    provider = Column(String)  # GITHUB
    status = Column(String, default=IntegrationStatusEnum.PENDING)
    name = Column(String)  # User-friendly name
    
    # Provider-specific config (encrypted JSON)
    config = Column(JSON)
    
    # OAuth tokens (encrypted) - for backward compatibility
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expiry = Column(DateTime(timezone=True), nullable=True)
    
    # GitHub App installation ID (for GitHub Apps)
    installation_id = Column(Integer, nullable=True, index=True)
    
    # Metadata
    project_id = Column(String, nullable=True)
    region = Column(String, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_verified = Column(DateTime(timezone=True), nullable=True)

class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    integration_id = Column(Integer, ForeignKey("integrations.id"), nullable=True)
    
    key_hash = Column(String, unique=True, index=True)  # SHA256 hash
    key_prefix = Column(String)  # First 8 chars for display
    name = Column(String)
    
    # Permissions
    scopes = Column(JSON)  # ["logs:write", "metrics:write", etc.]
    
    # Usage tracking
    last_used = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, default=0)
    
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)

class EmailLog(Base):
    __tablename__ = "email_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    email_type = Column(String, index=True)  # "pr_creation", "incident_resolved", "test"
    recipient_email = Column(String, index=True)
    status = Column(String, index=True)  # "success", "failed", "skipped"
    
    # Related entities
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    
    # Email details
    subject = Column(String, nullable=True)
    message_id = Column(String, nullable=True)  # Brevo message ID if successful
    
    # Error information
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)  # Store full error details as JSON
    
    # Metadata
    email_metadata = Column(JSON, nullable=True)  # Additional context (pr_url, pr_number, etc.)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

class SourceMap(Base):
    __tablename__ = "sourcemaps"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    service_name = Column(String, index=True, nullable=False)
    release = Column(String, index=True, nullable=False)
    environment = Column(String, index=True, default="production")
    file_path = Column(String, index=True, nullable=False)
    source_map = Column(Text, nullable=False)  # Base64 encoded or raw JSON
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Composite index for lookups
    __table_args__ = (
        {'schema': None},
    )

class AgentEmployee(Base):
    __tablename__ = "agent_employees"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, nullable=False)  # e.g., "Senior Software Engineer"
    department = Column(String, nullable=False)  # e.g., "Engineering"
    agent_type = Column(String, nullable=False)  # e.g., "coding", "safety"
    crewai_role = Column(String, nullable=False)  # e.g., "code_fixer_primary"
    capabilities = Column(JSON, server_default='[]')  # List of capabilities
    description = Column(Text, nullable=True)
    
    status = Column(String, default="available")  # available, working, idle
    current_task = Column(String, nullable=True)
    completed_tasks = Column(JSON, server_default='[]')  # List of completed task IDs/descriptions
    
    # Slack integration
    slack_bot_token = Column(String, nullable=True)  # Encrypted token
    slack_channel_id = Column(String, nullable=True)  # Channel ID where agent posts
    slack_user_id = Column(String, nullable=True)  # Bot user ID in Slack
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

class AgentPR(Base):
    """Track PRs created by agents (like Alex) for QA review."""
    __tablename__ = "agent_prs"
    
    id = Column(Integer, primary_key=True, index=True)
    pr_number = Column(Integer, nullable=False, index=True)
    repo_name = Column(String, nullable=False, index=True)  # "owner/repo"
    pr_url = Column(String, nullable=True)
    title = Column(String, nullable=True)
    head_branch = Column(String, nullable=True)
    base_branch = Column(String, nullable=True)
    
    # Agent who created the PR
    agent_employee_id = Column(Integer, ForeignKey("agent_employees.id"), nullable=False, index=True)
    agent_name = Column(String, nullable=False)  # e.g., "Alexandra Chen"
    
    # Related incident (if any)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True, index=True)
    
    # QA review status
    qa_review_status = Column(String, default="pending")  # pending, in_review, reviewed, approved, changes_requested
    qa_reviewed_by_id = Column(Integer, ForeignKey("agent_employees.id"), nullable=True)
    qa_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    pr_created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

# Import new memory models so they are registered with Base
from src.memory.models import AgentMemoryError, AgentMemoryFix, AgentRepoContext
