from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel

class LogIngestRequest(BaseModel):
    service_name: str
    severity: str
    message: str
    source: str = "github"
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    integration_id: Optional[int] = None
    release: Optional[str] = None
    environment: Optional[str] = None

class LogBatchRequest(BaseModel):
    logs: List[LogIngestRequest]

class LogResponse(BaseModel):
    id: int
    service_name: str
    severity: Optional[str] = None
    level: Optional[str] = None
    message: str
    source: Optional[str] = None
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    integration_id: Optional[int] = None

class LogListResponse(BaseModel):
    logs: List[LogResponse]

class OTelSpanEvent(BaseModel):
    name: str
    time: float
    attributes: Optional[Dict[str, Any]] = None

class OTelSpanStatus(BaseModel):
    code: int
    message: Optional[str] = None

class OTelSpan(BaseModel):
    traceId: str
    spanId: str
    parentSpanId: Optional[str] = None
    name: str
    timestamp: float
    startTime: float
    endTime: float
    attributes: Dict[str, Any]
    events: List[OTelSpanEvent]
    status: OTelSpanStatus
    resource: Dict[str, Any]

class OTelErrorPayload(BaseModel):
    apiKey: str
    serviceName: str
    spans: List[OTelSpan]
