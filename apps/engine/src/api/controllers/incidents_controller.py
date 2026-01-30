"""
Incidents Controller - Handles incident management and analysis.
"""
import traceback
import time
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import Request, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from src.api.controllers.base import get_user_id_from_request
from src.core.ai_analysis import analyze_incident_with_openrouter
from src.database.database import SessionLocal
from src.database.models import Incident, LogEntry, User, IncidentStatus, IncidentSeverity, Integration
from src.middleware import check_rate_limit
from src.services.email.service import send_incident_resolved_email
from src.services.incident_resolution_requests import ensure_incident_resolution_requested, run_incident_resolution_job
from src.utils.integrations import sync_linear_ticket_status, sync_linear_ticket_resolution


class IncidentsController:
    """Controller for incident management."""
    
    @staticmethod
    def list_incidents(
        status: Optional[str],
        severity: Optional[str],
        source: Optional[str],
        service: Optional[str],
        page: Optional[int],
        page_size: Optional[int],
        request: Request,
        db: Session
    ):
        """List incidents for the authenticated user only with optional pagination."""
        try:
            # Get authenticated user (middleware ensures this is set)
            user_id = get_user_id_from_request(request, db=db)

            # ALWAYS filter by user_id - no exceptions
            query = db.query(Incident).filter(Incident.user_id == user_id)

            # Validate and apply status filter
            if status:
                valid_statuses = [s.value for s in IncidentStatus]
                if status not in valid_statuses:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
                    )
                query = query.filter(Incident.status == status)

            # Validate and apply severity filter
            if severity:
                valid_severities = [s.value for s in IncidentSeverity]
                if severity not in valid_severities:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid severity. Must be one of: {', '.join(valid_severities)}"
                    )
                query = query.filter(Incident.severity == severity)

            # Apply source filter (no validation needed - can be any string)
            if source:
                query = query.filter(Incident.source == source)

            # Apply service filter (no validation needed - can be any string)
            if service:
                query = query.filter(Incident.service_name == service)

            # Handle pagination - if page_size is provided, default page to 1
            # This prevents the bug where page_size alone would return all incidents
            use_pagination = page_size is not None
            if use_pagination:
                # Default page to 1 if not provided
                if page is None:
                    page = 1
                
                # Validate pagination parameters
                if page < 1:
                    raise HTTPException(status_code=400, detail="Page must be >= 1")
                if page_size < 1 or page_size > 100:
                    raise HTTPException(status_code=400, detail="Page size must be between 1 and 100")
                
                # Get total count for pagination metadata (only when pagination is used)
                total_count = query.count()
                
                offset = (page - 1) * page_size
                incidents = query.order_by(Incident.last_seen_at.desc()).offset(offset).limit(page_size).all()
                
                # Return paginated response with metadata
                return {
                    "data": incidents,
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total": total_count,
                        "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
                    }
                }
            else:
                # Return all incidents if pagination not specified (backward compatibility)
                # WARNING: This can be slow for large datasets - consider requiring pagination
                incidents = query.order_by(Incident.last_seen_at.desc()).all()
                return incidents
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch incidents: {str(e)}"
            )
    
    @staticmethod
    async def get_incident(incident_id: int, background_tasks: BackgroundTasks, request: Request, db: Session):
        """Get incident details including related logs. Triggers AI analysis if not already done."""
        # Get authenticated user (middleware ensures this is set)
        user_id = get_user_id_from_request(request, db=db)

        # Helper functions for async database queries
        def _fetch_incident_sync(db: Session, incident_id: int, user_id: int):
            return db.query(Incident).filter(
                Incident.id == incident_id,
                Incident.user_id == user_id
            ).first()
        
        def _fetch_logs_sync(db: Session, log_ids: List[int], user_id: int):
            if not log_ids:
                return []
            return db.query(LogEntry).filter(
                LogEntry.id.in_(log_ids),
                LogEntry.user_id == user_id
            ).order_by(LogEntry.timestamp.desc()).all()

        # Fetch incident in thread pool to avoid blocking
        incident = await asyncio.to_thread(_fetch_incident_sync, db, incident_id, user_id)

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        # Fetch related logs in thread pool (non-blocking)
        logs = []
        if incident.log_ids:
            logs = await asyncio.to_thread(_fetch_logs_sync, db, incident.log_ids, user_id)

        # Ensure incident resolution is requested (idempotent; non-blocking to API thread).
        # IMPORTANT: do not use the request-scoped SQLAlchemy Session across threads.
        if incident.status != IncidentStatus.RESOLVED.value:
            def _ensure_requested_sync():
                local_db = SessionLocal()
                try:
                    ensure_incident_resolution_requested(
                        db=local_db,
                        incident_id=incident_id,
                        requested_by_user_id=user_id,
                        requested_by_trigger="incident_view",
                    )
                finally:
                    local_db.close()

            await asyncio.to_thread(_ensure_requested_sync)

        return {
            "incident": incident,
            "logs": logs
        }
    
    @staticmethod
    async def analyze_incident(incident_id: int, background_tasks: BackgroundTasks, request: Request, db: Session):
        """Manually trigger AI analysis for an incident."""
        # Get authenticated user (middleware ensures this is set)
        user_id = get_user_id_from_request(request, db=db)

        # Rate limiting: 5 analyses per user per hour
        rate_limit_key = f"analyze_incident:user:{user_id}"
        is_allowed, remaining = check_rate_limit(rate_limit_key, max_requests=5, window_seconds=3600)
        
        if not is_allowed:
            # Calculate human-readable time
            if remaining >= 3600:
                time_str = f"{remaining // 3600} hour(s)"
            elif remaining >= 60:
                time_str = f"{remaining // 60} minute(s)"
            else:
                time_str = f"{remaining} second(s)"
            
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Maximum 5 analyses per hour. Try again in {time_str}."
            )

        # ALWAYS filter by user_id to prevent cross-user access
        incident = db.query(Incident).filter(
            Incident.id == incident_id,
            Incident.user_id == user_id
        ).first()

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        # Track analytics
        try:
            # Log analysis request
            print(f"📊 Analytics: Analysis requested for incident {incident_id} by user {user_id}")
        except Exception as e:
            print(f"⚠️  Failed to log analytics: {e}")

        background_tasks.add_task(IncidentsController.analyze_incident_async, incident_id, user_id)

        return {"status": "analysis_triggered", "message": "AI analysis started in background"}
    
    @staticmethod
    async def analyze_incident_async(incident_id: int, user_id: Optional[int] = None):
        """Background task to analyze an incident."""
        db = SessionLocal()
        incident = None
        analysis_start_time = time.time()
        analysis_success = False
        analysis_error = None
        
        try:
            incident = db.query(Incident).filter(Incident.id == incident_id).first()
            if not incident:
                print(f"❌ Incident {incident_id} not found for analysis")
                return
            
            # Fetch related logs
            logs = []
            if incident.log_ids:
                # Ensure log_ids is a list and not empty
                log_id_list = incident.log_ids if isinstance(incident.log_ids, list) else []
                if log_id_list:
                    logs = db.query(LogEntry).filter(LogEntry.id.in_(log_id_list)).order_by(LogEntry.timestamp.desc()).all()
            
            # Perform analysis
            analysis = analyze_incident_with_openrouter(incident, logs, db)
            
            # Update incident with analysis results
            # Always update root_cause (even if it's an error message) to stop infinite loading
            if analysis.get("root_cause"):
                incident.root_cause = analysis["root_cause"]
            if analysis.get("action_taken"):
                incident.action_taken = analysis["action_taken"]
            
            # Store PR information in action_result if PR was created
            if analysis.get("pr_url"):
                changes = analysis.get("changes", {})
                original_contents = analysis.get("original_contents", {})
                
                # Ensure original_contents has entries for all changed files
                # If missing, set to empty string (new file) to prevent UI errors
                for file_path in changes.keys():
                    if file_path not in original_contents:
                        original_contents[file_path] = ""
                
                incident.action_result = {
                    "pr_url": analysis.get("pr_url"),
                    "pr_number": analysis.get("pr_number"),
                    "pr_files_changed": analysis.get("pr_files_changed", []),
                    "changes": changes,
                    "original_contents": original_contents,
                    "is_draft": analysis.get("is_draft", False),
                    "confidence_score": analysis.get("confidence_score"),
                    "decision": analysis.get("decision", {}),
                    "status": "pr_created_draft" if analysis.get("is_draft") else "pr_created"
                }
                pr_type = "DRAFT PR" if analysis.get("is_draft") else "PR"
                print(f"✅ {pr_type} created for incident {incident_id}: {analysis.get('pr_url')}")
            elif analysis.get("pr_error"):
                # Store PR error if creation failed
                incident.action_result = {
                    "status": "pr_failed",
                    "error": analysis.get("pr_error"),
                    "code_fix_explanation": analysis.get("code_fix_explanation", f"Failed to create pull request: {analysis.get('pr_error')}")
                }
            elif analysis.get("changes"):
                # Store changes even if PR wasn't created (for UI display)
                changes = analysis.get("changes", {})
                original_contents = analysis.get("original_contents", {})
                
                # Ensure original_contents has entries for all changed files
                # If missing, set to empty string (new file) to prevent UI errors
                for file_path in changes.keys():
                    if file_path not in original_contents:
                        original_contents[file_path] = ""
                
                incident.action_result = {
                    "changes": changes,
                    "original_contents": original_contents,
                    "pr_files_changed": list(changes.keys()),  # Set file list for UI display
                    "confidence_score": analysis.get("confidence_score"),
                    "decision": analysis.get("decision", {}),
                    "status": "changes_generated",
                    "code_fix_explanation": analysis.get("code_fix_explanation")
                }
                print(f"📝 Changes generated for incident {incident_id} (no PR created)")
            
            # Store explanation if no code fixes were attempted
            elif analysis.get("code_fix_explanation"):
                incident.action_result = {
                    "status": "no_code_fix",
                    "code_fix_explanation": analysis.get("code_fix_explanation")
                }
            
            # Ensure we always set something to stop infinite loading
            if not incident.root_cause:
                incident.root_cause = "Analysis failed - no results returned. Please check logs."
            
            # Commit with error handling
            try:
                db.commit()
                analysis_success = True
            except Exception as commit_error:
                db.rollback()
                print(f"❌ Failed to commit analysis results for incident {incident_id}: {commit_error}")
                # Try to set error message and commit again
                try:
                    incident.root_cause = f"Analysis completed but failed to save: {str(commit_error)[:200]}"
                    db.commit()
                except Exception as retry_error:
                    db.rollback()
                    print(f"❌ Failed to save error message: {retry_error}")
            
            analysis_duration = time.time() - analysis_start_time
            if analysis_success:
                print(f"✅ AI analysis completed for incident {incident_id}: {incident.root_cause[:100]}")
            
            # Track successful analysis analytics
            try:
                print(f"📊 Analytics: Analysis succeeded for incident {incident_id}, duration: {analysis_duration:.2f}s, user: {user_id}")
            except Exception as analytics_error:
                print(f"⚠️  Failed to track analytics: {analytics_error}")
            
        except Exception as e:
            error_trace = traceback.format_exc()
            analysis_error = str(e)
            analysis_duration = time.time() - analysis_start_time
            print(f"❌ Error analyzing incident {incident_id}: {e}")
            print(f"Full traceback: {error_trace}")
            
            # Track failed analysis analytics
            try:
                print(f"📊 Analytics: Analysis failed for incident {incident_id}, duration: {analysis_duration:.2f}s, error: {str(e)[:100]}, user: {user_id}")
            except Exception as analytics_error:
                print(f"⚠️  Failed to track analytics: {analytics_error}")
            
            # Set error message to stop infinite loading in UI
            try:
                if incident:
                    incident.root_cause = f"Analysis error: {str(e)[:200]}. Please check server logs."
                    try:
                        db.commit()
                        print(f"✅ Set error message for incident {incident_id}")
                    except Exception as commit_error:
                        db.rollback()
                        print(f"❌ Failed to commit error message: {commit_error}")
                else:
                    # Try to get incident again if we lost the reference
                    try:
                        incident = db.query(Incident).filter(Incident.id == incident_id).first()
                        if incident:
                            incident.root_cause = f"Analysis error: {str(e)[:200]}. Please check server logs."
                            try:
                                db.commit()
                                print(f"✅ Set error message for incident {incident_id}")
                            except Exception as commit_error:
                                db.rollback()
                                print(f"❌ Failed to commit error message: {commit_error}")
                    except Exception as query_error:
                        print(f"❌ Failed to query incident: {query_error}")
            except Exception as update_error:
                print(f"❌ Failed to update incident with error message: {update_error}")
                try:
                    db.rollback()
                except Exception:
                    pass  # Ignore rollback errors if connection is already closed
        finally:
            db.close()
    
    @staticmethod
    def update_incident(incident_id: int, update_data: dict, request: Request, db: Session):
        """Update incident status or severity."""
        # Get authenticated user (middleware ensures this is set)
        user_id = get_user_id_from_request(request, db=db)

        # ALWAYS filter by user_id to prevent cross-user modification
        incident = db.query(Incident).filter(
            Incident.id == incident_id,
            Incident.user_id == user_id
        ).first()

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
        
        # Store old status to detect changes
        old_status = incident.status
        
        if "status" in update_data:
            incident.status = update_data["status"]
        if "severity" in update_data:
            incident.severity = update_data["severity"]
            
        db.commit()
        db.refresh(incident)
        
        # Sync Linear ticket status if status changed
        # This is optional - Linear integration may not be available for all clients
        if "status" in update_data and old_status != incident.status:
            try:
                # Update Linear ticket state based on incident status
                # These functions handle cases where Linear is not configured gracefully
                sync_linear_ticket_status(
                    incident=incident,
                    old_status=old_status,
                    new_status=incident.status,
                    db=db
                )
                
                # If resolved, also update with resolution details
                if incident.status == "RESOLVED":
                    sync_linear_ticket_resolution(incident=incident, db=db)
            except Exception as e:
                # Log error but don't fail the request
                # This catch is for unexpected errors in the sync functions themselves
                print(f"⚠️  Unexpected error in Linear sync for incident {incident.id}: {e}")
                traceback.print_exc()
        
        # Send email notification if incident was just resolved
        # Check both old_status and new status to ensure we only send email on actual transition to RESOLVED
        status_changed_to_resolved = (
            "status" in update_data 
            and update_data["status"] == "RESOLVED" 
            and old_status != "RESOLVED"
        )
        
        if status_changed_to_resolved:
            try:
                # Get user email from incident
                user_email = None
                if incident.user_id:
                    user = db.query(User).filter(User.id == incident.user_id).first()
                    if user and user.email:
                        user_email = user.email
                
                if user_email:
                    # Prepare incident data for email
                    # Use updated_at as resolved_at, fallback to current time if not set
                    resolved_at = incident.updated_at
                    if resolved_at is None:
                        resolved_at = datetime.now()
                    
                    incident_data = {
                        "id": incident.id,
                        "title": incident.title or "Untitled Incident",
                        "service_name": incident.service_name or "Unknown Service",
                        "severity": incident.severity or "MEDIUM",
                        "status": incident.status,
                        "user_id": incident.user_id,
                        "created_at": incident.created_at.isoformat() if incident.created_at else None,
                        "resolved_at": resolved_at.isoformat() if hasattr(resolved_at, 'isoformat') else str(resolved_at),
                        "root_cause": incident.root_cause or "No root cause analysis available",
                        "action_taken": incident.action_taken or "No action details available"
                    }
                    
                    # Send email notification (non-blocking)
                    try:
                        send_incident_resolved_email(
                            recipient_email=user_email,
                            incident=incident_data,
                            db_session=db
                        )
                    except Exception as e:
                        # Log error but don't fail the request
                        print(f"⚠️  Failed to send incident resolved email notification: {e}")
                else:
                    print(f"⚠️  No user email found for incident {incident.id}, skipping email notification")
            except Exception as e:
                # Log error but don't fail the request
                print(f"⚠️  Error preparing incident resolved email notification: {e}")
                traceback.print_exc()
        
        return incident
    
    @staticmethod
    async def test_agent(incident_id: int, request: Request, db: Session):
        """
        Test endpoint to run the incident resolution pipeline (same as production).

        This endpoint calls the same code path as production incident resolution:
        run_incident_resolution_job -> analyze_incident_with_openrouter -> (if code needed) run_robust_crew.
        No duplicate logic; it only invokes the original coding agent flow.

        NOTE: This endpoint does NOT require authentication - it's for testing only.
        It will work with any incident ID without checking user permissions.

        Args:
            incident_id: ID of the incident to test

        Returns:
            Response with the same result structure as production resolution
        """
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            raise HTTPException(
                status_code=404,
                detail=f"Incident {incident_id} not found"
            )
        if not incident.user_id:
            raise HTTPException(
                status_code=400,
                detail="Incident has no user_id; resolution job requires it"
            )

        print(f"\n{'='*60}")
        print(f"🧪 TEST AGENT ENDPOINT - Incident {incident_id} (same path as production)")
        print(f"{'='*60}\n")

        try:
            start_time = time.time()
            job_result = run_incident_resolution_job(
                incident_id=incident_id,
                requested_by_user_id=incident.user_id,
            )
            execution_time = time.time() - start_time

            success = job_result.get("success", False)
            result = job_result.get("result") or {}

            response = {
                "success": success,
                "execution_time_seconds": round(execution_time, 2),
                "incident_id": incident_id,
                "result": result,
                "error": job_result.get("error"),
            }
            return response

        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"\n❌ Error in test agent endpoint: {e}")
            print(f"Full traceback:\n{error_trace}")
            return {
                "success": False,
                "incident_id": incident_id,
                "error": str(e),
                "error_trace": error_trace,
                "message": "Agent execution failed. Check error details.",
            }