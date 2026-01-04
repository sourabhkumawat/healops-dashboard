from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel

class IntegrationBase(BaseModel):
    provider: str
    name: str
    status: str
    project_id: Optional[str] = None

class IntegrationResponse(IntegrationBase):
    id: int
    created_at: Optional[str] = None
    last_verified: Optional[str] = None
    default_repo: Optional[str] = None
    service_mappings: Optional[Dict[str, str]] = None

    class Config:
        from_attributes = True

class IntegrationUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    default_repo: Optional[str] = None
    service_mappings: Optional[Dict[str, str]] = None
    repository: Optional[str] = None

class ServiceMappingRequest(BaseModel):
    service_name: str
    repo_name: str

class ServiceMappingsUpdateRequest(BaseModel):
    service_mappings: Dict[str, str]
    default_repo: Optional[str] = None

class GithubConfig(BaseModel):
    access_token: str
