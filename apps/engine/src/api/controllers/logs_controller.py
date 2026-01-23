"""
Logs Controller - Handles log ingestion and retrieval.
"""
from fastapi import Request, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime

from src.database.models import LogEntry, Integration
from src.memory import ensure_partition_exists_for_timestamp
from src.utils.redpanda_websocket_manager import connection_manager as manager
from src.api.controllers.base import get_user_id_from_request


class LogIngestRequest(BaseModel):
    service_name: str
    severity: str  # Changed from level to match PRD
    message: str
    source: str = "github"  # agent
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    integration_id: Optional[int] = None
    release: Optional[str] = None  # Release identifier for source map resolution
    environment: Optional[str] = None  # Environment name for source map resolution


class LogBatchRequest(BaseModel):
    logs: List[LogIngestRequest]


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


class LogsController:
    """Controller for log ingestion and retrieval."""
    
    @staticmethod
    async def ingest_log(log: LogIngestRequest, request: Request, background_tasks: BackgroundTasks, db: Session):
        """
        Ingest logs from clients. All logs are broadcast to WebSockets.
        Only ERROR and CRITICAL logs are persisted to the database.
        """
        try:
            # API Key is already validated by middleware
            api_key = request.state.api_key
            
            # Determine integration_id
            integration_id = api_key.integration_id
            if not integration_id and log.integration_id:
                integration = db.query(Integration).filter(
                    Integration.id == log.integration_id,
                    Integration.user_id == api_key.user_id
                ).first()
                if integration:
                    integration_id = integration.id
            
            # Prepare log data
            log_data = {
                "service_name": log.service_name,
                "severity": log.severity,
                "message": log.message,
                "source": log.source,
                "timestamp": log.timestamp or datetime.utcnow().isoformat(),
                "metadata": log.metadata
            }
            
            # 1. Broadcast to WebSockets (ALL LOGS)
            # Don't let WebSocket failures prevent persistence
            try:
                await manager.broadcast(log_data)
            except Exception as ws_error:
                print(f"Warning: Failed to broadcast log to WebSockets: {ws_error}")
                # Continue execution even if broadcast fails
            
            # 2. Persistence & Incident Logic (ERRORS ONLY)
            severity_upper = log.severity.upper() if log.severity else ""
            should_persist = severity_upper in ["ERROR", "CRITICAL"]
            
            if should_persist:
                try:
                    # Resolve source maps in metadata before saving
                    resolved_metadata = log.metadata
                    if log.metadata and isinstance(log.metadata, dict):
                        try:
                            from sourcemap_resolver import resolve_metadata_with_sourcemaps
                            # Use release/environment from top-level request, fallback to metadata
                            release = log.release or log.metadata.get('release') or log.metadata.get('releaseId') or None
                            environment = log.environment or log.metadata.get('environment') or log.metadata.get('env') or "production"
                            resolved_metadata = resolve_metadata_with_sourcemaps(
                                db=db,
                                user_id=api_key.user_id,
                                service_name=log.service_name,
                                metadata=log.metadata,
                                release=release,
                                environment=environment
                            )
                        except Exception as sm_error:
                            # Don't fail log ingestion if source map resolution fails
                            print(f"Warning: Source map resolution failed: {sm_error}")
                            resolved_metadata = log.metadata
                    
                    # Parse timestamp and ensure partition exists
                    log_timestamp = datetime.utcnow()
                    if log.timestamp:
                        try:
                            # Try parsing ISO format timestamp
                            if isinstance(log.timestamp, str):
                                log_timestamp = datetime.fromisoformat(log.timestamp.replace('Z', '+00:00'))
                            elif isinstance(log.timestamp, datetime):
                                log_timestamp = log.timestamp
                        except (ValueError, AttributeError) as e:
                            print(f"Warning: Could not parse timestamp {log.timestamp}, using current time: {e}")
                            log_timestamp = datetime.utcnow()
                    
                    # Ensure partition exists before inserting
                    ensure_partition_exists_for_timestamp(log_timestamp)
                    
                    db_log = LogEntry(
                        service_name=log.service_name,
                        level=log.severity,
                        severity=log.severity,
                        message=log.message,
                        source=log.source,
                        integration_id=integration_id,
                        user_id=api_key.user_id,  # Store user_id from API key
                        metadata_json=resolved_metadata,  # Use resolved metadata with source maps
                        timestamp=log_timestamp
                    )
                    db.add(db_log)
                    db.commit()
                    db.refresh(db_log)
                    
                    # Log successful persistence for debugging
                    print(f"✓ Persisted {severity_upper} log: id={db_log.id}, service={log.service_name}, message={log.message[:50]}")
                    
                    # Trigger incident check via Redpanda
                    try:
                        from src.services.redpanda_task_processor import publish_log_processing_task
                        publish_log_processing_task(db_log.id)
                    except Exception as task_error:
                        print(f"Warning: Failed to queue incident check task via Redpanda: {task_error}")
                        # Don't fail the request if task queuing fails
                    
                    return {"status": "ingested", "id": db_log.id, "persisted": True, "severity": log.severity}
                except Exception as db_error:
                    # Rollback on error
                    db.rollback()
                    print(f"✗ Failed to persist log to database: {db_error}")
                    print(f"  Log details: service={log.service_name}, severity={log.severity}, message={log.message[:50]}")
                    # Return error response but don't raise exception (log was received)
                    return {
                        "status": "broadcasted",
                        "persisted": False,
                        "error": "Failed to persist log to database",
                        "severity": log.severity
                    }
            else:
                # Log received but not persisted (INFO/WARNING)
                print(f"Received {severity_upper} log (not persisted): service={log.service_name}, message={log.message[:50]}")
                return {"status": "broadcasted", "persisted": False, "severity": log.severity}
                
        except Exception as e:
            # Catch any unexpected errors
            print(f"✗ Unexpected error in ingest_log: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    @staticmethod
    async def ingest_logs_batch(batch: LogBatchRequest, request: Request, background_tasks: BackgroundTasks, db: Session):
        """
        Ingest multiple logs in a batch. All logs are broadcast to WebSockets.
        Only ERROR and CRITICAL logs are persisted to the database.
        """
        try:
            # API Key is already validated by middleware
            api_key = request.state.api_key
            
            # Determine integration_id (same for all logs in batch)
            integration_id = api_key.integration_id
            
            results = []
            persisted_count = 0
            broadcasted_count = 0
            
            for log in batch.logs:
                try:
                    # Override integration_id if provided in log
                    log_integration_id = integration_id
                    if not log_integration_id and log.integration_id:
                        integration = db.query(Integration).filter(
                            Integration.id == log.integration_id,
                            Integration.user_id == api_key.user_id
                        ).first()
                        if integration:
                            log_integration_id = integration.id
                    
                    # Prepare log data
                    log_data = {
                        "service_name": log.service_name,
                        "severity": log.severity,
                        "message": log.message,
                        "source": log.source,
                        "timestamp": log.timestamp or datetime.utcnow().isoformat(),
                        "metadata": log.metadata
                    }
                    
                    # 1. Broadcast to WebSockets (ALL LOGS)
                    try:
                        await manager.broadcast(log_data)
                        broadcasted_count += 1
                    except Exception as ws_error:
                        print(f"Warning: Failed to broadcast log to WebSockets: {ws_error}")
                        # Continue execution even if broadcast fails
                    
                    # 2. Persistence & Incident Logic (ERRORS ONLY)
                    severity_upper = log.severity.upper() if log.severity else ""
                    should_persist = severity_upper in ["ERROR", "CRITICAL"]
                    
                    if should_persist:
                        try:
                            # Use a savepoint for each log so failures don't affect others
                            savepoint = db.begin_nested()
                            try:
                                # Parse timestamp and ensure partition exists
                                log_timestamp = datetime.utcnow()
                                if log.timestamp:
                                    try:
                                        # Try parsing ISO format timestamp
                                        if isinstance(log.timestamp, str):
                                            log_timestamp = datetime.fromisoformat(log.timestamp.replace('Z', '+00:00'))
                                        elif isinstance(log.timestamp, datetime):
                                            log_timestamp = log.timestamp
                                    except (ValueError, AttributeError) as e:
                                        print(f"Warning: Could not parse timestamp {log.timestamp}, using current time: {e}")
                                        log_timestamp = datetime.utcnow()
                                
                                # Resolve source maps in metadata before saving
                                resolved_metadata = log.metadata
                                if log.metadata and isinstance(log.metadata, dict):
                                    try:
                                        from sourcemap_resolver import resolve_metadata_with_sourcemaps
                                        # Use release/environment from top-level request, fallback to metadata
                                        release = log.release or log.metadata.get('release') or log.metadata.get('releaseId') or None
                                        environment = log.environment or log.metadata.get('environment') or log.metadata.get('env') or "production"
                                        resolved_metadata = resolve_metadata_with_sourcemaps(
                                            db=db,
                                            user_id=api_key.user_id,
                                            service_name=log.service_name,
                                            metadata=log.metadata,
                                            release=release,
                                            environment=environment
                                        )
                                    except Exception as sm_error:
                                        # Don't fail log ingestion if source map resolution fails
                                        print(f"Warning: Source map resolution failed: {sm_error}")
                                        resolved_metadata = log.metadata
                                
                                # Ensure partition exists before inserting
                                ensure_partition_exists_for_timestamp(log_timestamp)
                                
                                db_log = LogEntry(
                                    service_name=log.service_name,
                                    level=log.severity,
                                    severity=log.severity,
                                    message=log.message,
                                    source=log.source,
                                    integration_id=log_integration_id,
                                    user_id=api_key.user_id,  # Store user_id from API key
                                    metadata_json=resolved_metadata,  # Use resolved metadata with source maps
                                    timestamp=log_timestamp
                                )
                                db.add(db_log)
                                db.flush()  # Flush to get the ID without committing
                                savepoint.commit()
                                persisted_count += 1
                                
                                # Log successful persistence for debugging
                                print(f"✓ Persisted {severity_upper} log: id={db_log.id}, service={log.service_name}, message={log.message[:50]}")
                                
                                # Trigger incident check via Redpanda
                                try:
                                    from src.services.redpanda_task_processor import publish_log_processing_task
                                    publish_log_processing_task(db_log.id)
                                except Exception as task_error:
                                    print(f"Warning: Failed to queue incident check task via Redpanda: {task_error}")
                                    # Don't fail the request if task queuing fails
                                
                                results.append({"status": "ingested", "id": db_log.id, "persisted": True, "severity": log.severity})
                            except Exception as db_error:
                                # Rollback only this savepoint, not the entire transaction
                                savepoint.rollback()
                                print(f"✗ Failed to persist log to database: {db_error}")
                                print(f"  Log details: service={log.service_name}, severity={log.severity}, message={log.message[:50]}")
                                # Return error response but don't raise exception (log was received)
                                results.append({
                                    "status": "broadcasted",
                                    "persisted": False,
                                    "error": "Failed to persist log to database",
                                    "severity": log.severity
                                })
                        except Exception as savepoint_error:
                            # Handle savepoint creation errors
                            print(f"✗ Failed to create savepoint: {savepoint_error}")
                            results.append({
                                "status": "broadcasted",
                                "persisted": False,
                                "error": "Failed to persist log to database",
                                "severity": log.severity
                            })
                    else:
                        # Log received but not persisted (INFO/WARNING)
                        print(f"Received {severity_upper} log (not persisted): service={log.service_name}, message={log.message[:50]}")
                        results.append({"status": "broadcasted", "persisted": False, "severity": log.severity})
                        
                except Exception as log_error:
                    # Handle individual log errors without failing the entire batch
                    print(f"✗ Error processing log in batch: {log_error}")
                    import traceback
                    traceback.print_exc()
                    results.append({
                        "status": "error",
                        "error": str(log_error),
                        "severity": log.severity if log else "UNKNOWN"
                    })
            
            # Commit all persisted logs at once
            if persisted_count > 0:
                try:
                    db.commit()
                except Exception as commit_error:
                    db.rollback()
                    print(f"✗ Failed to commit batch: {commit_error}")
                    raise HTTPException(status_code=500, detail=f"Failed to commit logs: {str(commit_error)}")
            
            return {
                "status": "success",
                "total": len(batch.logs),
                "broadcasted": broadcasted_count,
                "persisted": persisted_count,
                "results": results
            }
                
        except Exception as e:
            # Catch any unexpected errors
            print(f"✗ Unexpected error in ingest_logs_batch: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    @staticmethod
    async def ingest_otel_errors(payload: OTelErrorPayload, background_tasks: BackgroundTasks, request: Request, db: Session):
        """
        Ingest OpenTelemetry spans from HealOps SDK.
        Now receives ALL spans (success & error).
        - Broadcasts ALL spans to WebSocket (Live Logs)
        - Persists ONLY ERROR/CRITICAL spans to Database
        """
        # API key is already validated by APIKeyMiddleware and set in request.state
        # Use it to update last_used timestamp
        if not hasattr(request.state, 'api_key') or not request.state.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        valid_key = request.state.api_key
        
        # Update last_used timestamp
        valid_key.last_used = datetime.utcnow()
        
        # Ensure partition exists for current date (all logs will use current timestamp)
        ensure_partition_exists_for_timestamp(datetime.utcnow())
        
        # Process each span
        persisted_count = 0
        total_received = len(payload.spans)
        
        for span in payload.spans:
            # Extract error information
            error_message = span.status.message or span.name
            
            # Check for exception in events
            exception_details = None
            for event in span.events:
                if event.name == 'exception' and event.attributes:
                    exception_type = event.attributes.get('exception.type', 'Unknown')
                    exception_message = event.attributes.get('exception.message', '')
                    exception_stacktrace = event.attributes.get('exception.stacktrace', '')
                    exception_details = f"{exception_type}: {exception_message}\n{exception_stacktrace}"
                    break
            
            # Check for exception in attributes
            if not exception_details:
                if 'exception.type' in span.attributes or 'exception.message' in span.attributes:
                    exception_type = span.attributes.get('exception.type', 'Unknown')
                    exception_message = span.attributes.get('exception.message', '')
                    exception_stacktrace = span.attributes.get('exception.stacktrace', '')
                    exception_details = f"{exception_type}: {exception_message}\n{exception_stacktrace}"
            
            if exception_details:
                error_message = exception_details
            
            # Determine severity based on status code
            # SpanStatusCode: UNSET=0, OK=1, ERROR=2
            is_error = span.status.code == 2
            severity = "ERROR" if is_error else "INFO"
            
            # If it's not an error, check if it has exception details (could be a handled exception)
            if not is_error and exception_details:
                severity = "WARNING"
                is_error = True  # Treat warning as something to persist? Requirement says "error logs", usually implies ERROR/CRITICAL. Let's stick to strict ERROR code for persistence unless it has exception.
            
            metadata = {
                "traceId": span.traceId,
                "spanId": span.spanId,
                "parentSpanId": span.parentSpanId,
                "spanName": span.name,
                "startTime": span.startTime,
                "endTime": span.endTime,
                "duration": span.endTime - span.startTime,
                "attributes": span.attributes,
                "events": [
                    {
                        "name": event.name,
                        "time": event.time,
                        "attributes": event.attributes
                    }
                    for event in span.events
                ],
                "resource": span.resource,
                "statusCode": span.status.code,
                "statusMessage": span.status.message
            }
            
            # Prepare log data for broadcast
            log_data = {
                "service_name": payload.serviceName,
                "severity": severity,
                "message": error_message,
                "source": "otel",
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": metadata
            }
            
            # 1. Broadcast (ALL SPANS)
            await manager.broadcast(log_data)
            
            # 2. Persist (ONLY ERRORS)
            if is_error or severity.upper() in ["ERROR", "CRITICAL"]:
                db_log = LogEntry(
                    service_name=payload.serviceName,
                    level=severity,
                    severity=severity,
                    message=error_message,
                    source="otel",
                    integration_id=valid_key.integration_id,
                    user_id=valid_key.user_id,  # Store user_id from API key
                    metadata_json=metadata
                )
                db.add(db_log)
                persisted_count += 1
        
        if persisted_count > 0:
            db.commit()
            
            # Trigger async analysis for persisted logs via Redpanda
            try:
                from src.services.redpanda_task_processor import publish_log_processing_task
                # Fetch IDs of newly inserted logs
                recent_logs = db.query(LogEntry).filter(
                    LogEntry.service_name == payload.serviceName,
                    LogEntry.source == "otel"
                ).order_by(LogEntry.id.desc()).limit(persisted_count).all()

                for log in recent_logs:
                    publish_log_processing_task(log.id)

            except Exception as e:
                print(f"Failed to trigger tasks via Redpanda: {e}")
        
        return {
            "status": "success",
            "received": total_received,
            "persisted": persisted_count,
            "message": f"Received {total_received} spans, persisted {persisted_count} errors"
        }
    
    @staticmethod
    def list_logs(limit: int, request: Request, db: Session):
        """List recent log entries for the authenticated user only."""
        # Get authenticated user (middleware ensures this is set)
        user_id = get_user_id_from_request(request, db=db)

        # ALWAYS filter by user_id - no exceptions
        query = db.query(LogEntry).filter(LogEntry.user_id == user_id)
        logs = query.order_by(LogEntry.timestamp.desc()).limit(limit).all()

        return {
            "logs": [
                {
                    "id": log.id,
                    "service_name": log.service_name,
                    "severity": log.severity or log.level,
                    "level": log.level,
                    "message": log.message,
                    "source": log.source,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                    "metadata": log.metadata_json,
                    "integration_id": log.integration_id
                }
                for log in logs
            ]
        }
