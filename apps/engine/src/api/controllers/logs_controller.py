"""
Logs Controller - Handles log ingestion and retrieval.
"""
from fastapi import Request, HTTPException, BackgroundTasks, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import asyncio
import json

from src.database.models import LogEntry, Integration, ApiKey
from src.database.database import SessionLocal
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


def _query_integration_sync(db: Session, integration_id: int, user_id: int) -> Optional[Integration]:
    """Synchronous helper to query integration."""
    return db.query(Integration).filter(
        Integration.id == integration_id,
        Integration.user_id == user_id
    ).first()


def _persist_log_sync(db: Session, log_entry: LogEntry) -> LogEntry:
    """Synchronous helper to persist log entry."""
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


def _resolve_sourcemaps_sync(db: Session, user_id: int, service_name: str, metadata: Dict[str, Any], release: Optional[str], environment: str) -> Dict[str, Any]:
    """Synchronous helper to resolve source maps."""
    from src.tools.sourcemap import resolve_metadata_with_sourcemaps
    return resolve_metadata_with_sourcemaps(
        db=db,
        user_id=user_id,
        service_name=service_name,
        metadata=metadata,
        release=release,
        environment=environment
    )


def _persist_log_batch_sync(db: Session, db_log: LogEntry, savepoint) -> LogEntry:
    """Synchronous helper to persist log entry in batch with savepoint."""
    db.add(db_log)
    db.flush()  # Flush to get the ID without committing
    savepoint.commit()
    return db_log


def _commit_batch_sync(db: Session):
    """Synchronous helper to commit batch transaction."""
    db.commit()


# Background processing functions for immediate response pattern
async def process_log_background(log_data: dict, api_key_id: int, user_id: int, integration_id: Optional[int], db: Session = None):
    """Background task to process a single log entry."""
    if db is None:
        db = SessionLocal()
        close_db = True
    else:
        close_db = False
    
    try:
        log = LogIngestRequest(**log_data)
        severity_upper = log.severity.upper() if log.severity else ""
        should_persist = severity_upper in ["ERROR", "CRITICAL"]
        
        if should_persist:
            try:
                # Resolve source maps with timeout
                resolved_metadata = log.metadata
                if log.metadata and isinstance(log.metadata, dict):
                    release = log.release or log.metadata.get('release') or log.metadata.get('releaseId') or None
                    environment = log.environment or log.metadata.get('environment') or log.metadata.get('env') or "production"
                    
                    try:
                        resolved_metadata = await asyncio.wait_for(
                            asyncio.to_thread(
                                _resolve_sourcemaps_sync,
                                db,
                                user_id,
                                log.service_name,
                                log.metadata,
                                release,
                                environment
                            ),
                            timeout=2.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        resolved_metadata = log.metadata
                
                # Parse timestamp
                log_timestamp = datetime.utcnow()
                if log.timestamp:
                    try:
                        if isinstance(log.timestamp, str):
                            log_timestamp = datetime.fromisoformat(log.timestamp.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        log_timestamp = datetime.utcnow()
                
                # Ensure partition exists
                await asyncio.to_thread(ensure_partition_exists_for_timestamp, log_timestamp)
                
                # Create and persist log
                db_log = LogEntry(
                    service_name=log.service_name,
                    level=log.severity,
                    severity=log.severity,
                    message=log.message,
                    source=log.source,
                    integration_id=integration_id,
                    user_id=user_id,
                    metadata_json=resolved_metadata,
                    timestamp=log_timestamp
                )
                await asyncio.to_thread(_persist_log_sync, db, db_log)
                
                # Trigger incident check
                try:
                    from src.services.redpanda_task_processor import publish_log_processing_task
                    publish_log_processing_task(db_log.id)
                except Exception:
                    pass
            except Exception as e:
                print(f"Error in background log processing: {e}")
    finally:
        if close_db:
            db.close()


async def process_otel_spans_background(payload_dict: dict, api_key_id: int, user_id: int, integration_id: Optional[int], db: Session = None):
    """Background task to process OTel spans in batch."""
    if db is None:
        db = SessionLocal()
        close_db = True
    else:
        close_db = False
    
    try:
        # Reconstruct payload from dict
        payload = OTelErrorPayload(**payload_dict)
        service_name = payload.serviceName
        
        # Update last_used timestamp
        def _update_last_used_sync(db: Session, api_key_id: int):
            api_key = db.query(ApiKey).filter(ApiKey.id == api_key_id).first()
            if api_key:
                api_key.last_used = datetime.utcnow()
                db.commit()
        
        await asyncio.to_thread(_update_last_used_sync, db, api_key_id)
        
        # Ensure partition exists
        await asyncio.to_thread(ensure_partition_exists_for_timestamp, datetime.utcnow())
        
        # Batch process spans
        logs_to_add = []
        for span in payload.spans:
            error_message = span.status.message or span.name
            
            # Check for exception
            exception_details = None
            for event in span.events:
                if event.name == 'exception' and event.attributes:
                    exception_type = event.attributes.get('exception.type', 'Unknown')
                    exception_message = event.attributes.get('exception.message', '')
                    exception_stacktrace = event.attributes.get('exception.stacktrace', '')
                    exception_details = f"{exception_type}: {exception_message}\n{exception_stacktrace}"
                    break
            
            if not exception_details:
                if 'exception.type' in span.attributes:
                    exception_type = span.attributes.get('exception.type', 'Unknown')
                    exception_message = span.attributes.get('exception.message', '')
                    exception_stacktrace = span.attributes.get('exception.stacktrace', '')
                    exception_details = f"{exception_type}: {exception_message}\n{exception_stacktrace}"
            
            if exception_details:
                error_message = exception_details
            
            is_error = span.status.code == 2
            severity = "ERROR" if is_error else "INFO"
            
            if not is_error and exception_details:
                severity = "WARNING"
                is_error = True
            
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
            
            # Prepare log data for broadcast (non-blocking with timeout)
            log_data = {
                "service_name": service_name,
                "severity": severity,
                "message": error_message,
                "source": "otel",
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": metadata
            }
            
            # Broadcast with timeout (non-blocking)
            try:
                await asyncio.wait_for(manager.broadcast(log_data), timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass  # Broadcast is non-critical
            
            # Collect errors for batch insert
            if is_error or severity.upper() in ["ERROR", "CRITICAL"]:
                logs_to_add.append({
                    "service_name": service_name,
                    "level": severity,
                    "severity": severity,
                    "message": error_message,
                    "source": "otel",
                    "integration_id": integration_id,
                    "user_id": user_id,
                    "metadata_json": metadata,
                    "timestamp": datetime.utcnow()
                })
        
        # Batch insert all error logs
        if logs_to_add:
            def _bulk_insert_logs_sync(db: Session, logs: List[dict]):
                db.bulk_insert_mappings(LogEntry, logs)
                db.commit()
            
            await asyncio.to_thread(_bulk_insert_logs_sync, db, logs_to_add)
            
            # Trigger incident checks
            try:
                from src.services.redpanda_task_processor import publish_log_processing_task
                # Fetch recent logs to get IDs
                def _fetch_recent_logs_sync(db: Session, service_name: str, source: str, limit: int):
                    return db.query(LogEntry).filter(
                        LogEntry.service_name == service_name,
                        LogEntry.source == source
                    ).order_by(LogEntry.id.desc()).limit(limit).all()
                
                recent_logs = await asyncio.to_thread(
                    _fetch_recent_logs_sync,
                    db,
                    service_name,
                    "otel",
                    len(logs_to_add)
                )
                
                for log in recent_logs:
                    publish_log_processing_task(log.id)
            except Exception:
                pass
    finally:
        if close_db:
            db.close()


class LogsController:
    """Controller for log ingestion and retrieval."""
    
    @staticmethod
    async def ingest_log(log: LogIngestRequest, request: Request, background_tasks: BackgroundTasks, db: Session):
        """
        Ingest logs from clients. Returns 202 Accepted immediately and processes in background.
        All logs are broadcast to WebSockets. Only ERROR and CRITICAL logs are persisted to the database.
        """
        try:
            # API Key is already validated by middleware
            api_key = request.state.api_key
            
            # Determine integration_id quickly
            integration_id = api_key.integration_id
            if not integration_id and log.integration_id:
                try:
                    integration = await asyncio.wait_for(
                        asyncio.to_thread(_query_integration_sync, db, log.integration_id, api_key.user_id),
                        timeout=0.5
                    )
                    if integration:
                        integration_id = integration.id
                except (asyncio.TimeoutError, Exception):
                    pass  # Use default integration_id
            
            # Prepare log data for background processing
            log_data = {
                "service_name": log.service_name,
                "severity": log.severity,
                "message": log.message,
                "source": log.source,
                "timestamp": log.timestamp or datetime.utcnow().isoformat(),
                "metadata": log.metadata,
                "release": log.release,
                "environment": log.environment,
                "integration_id": log.integration_id
            }
            
            # Quick broadcast attempt (non-blocking with timeout)
            try:
                await asyncio.wait_for(manager.broadcast(log_data), timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                pass  # Broadcast is non-critical, continue
            
            # Process in background and return immediately
            background_tasks.add_task(
                process_log_background,
                log_data,
                api_key.id,
                api_key.user_id,
                integration_id
            )
            
            # Return 202 Accepted immediately
            return Response(
                status_code=202,
                content=json.dumps({
                    "status": "accepted",
                    "message": "Log is being processed in background"
                }),
                media_type="application/json"
            )
                
        except Exception as e:
            # Catch any unexpected errors
            print(f"✗ Unexpected error in ingest_log: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    
    @staticmethod
    async def ingest_logs_batch(batch: LogBatchRequest, request: Request, background_tasks: BackgroundTasks, db: Session):
        """
        Ingest multiple logs in a batch. Returns 202 Accepted immediately and processes in background.
        All logs are broadcast to WebSockets. Only ERROR and CRITICAL logs are persisted to the database.
        """
        try:
            # API Key is already validated by middleware
            api_key = request.state.api_key
            integration_id = api_key.integration_id
            
            # Prepare batch data for background processing
            batch_data = {
                "logs": [log.dict() for log in batch.logs],
                "api_key_id": api_key.id,
                "user_id": api_key.user_id,
                "integration_id": integration_id
            }
            
            # Quick broadcast attempts (non-blocking with timeout)
            for log in batch.logs:
                log_data = {
                    "service_name": log.service_name,
                    "severity": log.severity,
                    "message": log.message,
                    "source": log.source,
                    "timestamp": log.timestamp or datetime.utcnow().isoformat(),
                    "metadata": log.metadata
                }
                try:
                    await asyncio.wait_for(manager.broadcast(log_data), timeout=0.3)
                except (asyncio.TimeoutError, Exception):
                    pass  # Continue with next log
            
            # Process in background and return immediately
            async def process_batch_background(batch_data: dict):
                """Background task to process batch of logs."""
                db = SessionLocal()
                try:
                    api_key_id = batch_data["api_key_id"]
                    user_id = batch_data["user_id"]
                    integration_id = batch_data["integration_id"]
                    
                    for log_dict in batch_data["logs"]:
                        await process_log_background(log_dict, api_key_id, user_id, integration_id, db)
                finally:
                    db.close()
            
            background_tasks.add_task(process_batch_background, batch_data)
            
            # Return 202 Accepted immediately
            return Response(
                status_code=202,
                content=json.dumps({
                    "status": "accepted",
                    "message": f"Batch of {len(batch.logs)} logs is being processed in background"
                }),
                media_type="application/json"
            )
            
            # OLD CODE BELOW - keeping for reference but not executed
            if False:
                results = []
                persisted_count = 0
                broadcasted_count = 0
                
                for log in batch.logs:
                    try:
                        # Override integration_id if provided in log
                        log_integration_id = integration_id
                        if not log_integration_id and log.integration_id:
                            # Run DB query in thread pool to avoid blocking
                            integration = await asyncio.to_thread(
                                _query_integration_sync,
                                db,
                                log.integration_id,
                                api_key.user_id
                            )
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
                                    
                                    # Resolve source maps in metadata before saving (with timeout)
                                    resolved_metadata = log.metadata
                                    if log.metadata and isinstance(log.metadata, dict):
                                        # Use release/environment from top-level request, fallback to metadata
                                        release = log.release or log.metadata.get('release') or log.metadata.get('releaseId') or None
                                        environment = log.environment or log.metadata.get('environment') or log.metadata.get('env') or "production"
                                        
                                        # Try source map resolution with timeout to avoid blocking
                                        try:
                                            resolved_metadata = await asyncio.wait_for(
                                                asyncio.to_thread(
                                                    _resolve_sourcemaps_sync,
                                                    db,
                                                    api_key.user_id,
                                                    log.service_name,
                                                    log.metadata,
                                                    release,
                                                    environment
                                                ),
                                                timeout=2.0  # 2 second timeout
                                            )
                                        except ImportError:
                                            # Source map resolution not available - this is optional
                                            resolved_metadata = log.metadata
                                        except (asyncio.TimeoutError, Exception) as sm_error:
                                            # Don't fail log ingestion if source map resolution fails or times out
                                            import logging
                                            logger = logging.getLogger(__name__)
                                            logger.debug(f"Source map resolution failed (non-critical): {sm_error}")
                                            resolved_metadata = log.metadata
                                    
                                    # Ensure partition exists before inserting (non-blocking)
                                    await asyncio.to_thread(ensure_partition_exists_for_timestamp, log_timestamp)
                                    
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
            
            # Commit all persisted logs at once (non-blocking)
            if persisted_count > 0:
                try:
                    await asyncio.to_thread(_commit_batch_sync, db)
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
        Returns 202 Accepted immediately and processes spans in background with batch operations.
        - Broadcasts ALL spans to WebSocket (Live Logs)
        - Persists ONLY ERROR/CRITICAL spans to Database
        """
        # API key is already validated by APIKeyMiddleware and set in request.state
        if not hasattr(request.state, 'api_key') or not request.state.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        valid_key = request.state.api_key
        
        # Prepare payload data for background processing
        payload_dict = {
            "apiKey": payload.apiKey,
            "serviceName": payload.serviceName,
            "spans": [span.dict() for span in payload.spans]
        }
        
        # Process in background and return immediately
        background_tasks.add_task(
            process_otel_spans_background,
            payload_dict,
            valid_key.id,
            valid_key.user_id,
            valid_key.integration_id
        )
        
        # Return 202 Accepted immediately
        return Response(
            status_code=202,
            content=json.dumps({
                "status": "accepted",
                "message": f"Processing {len(payload.spans)} spans in background"
            }),
            media_type="application/json"
        )
    
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
