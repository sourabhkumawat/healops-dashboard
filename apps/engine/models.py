from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, Enum, Text
from sqlalchemy.sql import func
import enum
from database import Base

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
    metadata_json = Column(JSON, nullable=True)
    
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
    
    # OAuth tokens (encrypted)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expiry = Column(DateTime(timezone=True), nullable=True)
    
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

