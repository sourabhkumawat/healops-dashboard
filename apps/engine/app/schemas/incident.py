from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel

class IncidentBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: str
    severity: str
    service_name: str
    source: Optional[str] = None
    repo_name: Optional[str] = None

class IncidentResponse(IncidentBase):
    id: int
    user_id: Optional[int] = None
    created_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    root_cause: Optional[str] = None
    action_taken: Optional[str] = None
    action_result: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True

class IncidentUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    root_cause: Optional[str] = None
    action_taken: Optional[str] = None

class IncidentAnalysisResponse(BaseModel):
    status: str
    message: str
