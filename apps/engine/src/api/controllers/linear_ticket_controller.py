"""
Linear Ticket Resolution API Controller

Provides REST endpoints for managing Linear ticket resolution configuration,
monitoring resolution attempts, and manually triggering resolution processes.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from pydantic import BaseModel, Field

from src.database.database import get_db
from src.database.models import (
    Integration, LinearResolutionAttempt, LinearResolutionAttemptStatus, User
)
from src.integrations.linear.integration import LinearIntegration
from src.core.linear_ticket_analyzer import LinearTicketAnalyzer, analyze_tickets_for_resolution
from src.services.linear_ticket_resolver import LinearTicketResolver, LinearTicketWorkflowManager
from src.services.linear_polling_service import polling_service_manager, PollingConfig
from src.api.controllers.auth_controller import get_current_user
from src.utils.integrations import get_linear_integration_for_user


router = APIRouter(prefix="/linear-tickets", tags=["Linear Ticket Resolution"])


# Pydantic models for request/response
class LinearAutoResolutionConfig(BaseModel):
    """Configuration for automatic Linear ticket resolution."""
    enabled: bool = Field(default=False, description="Enable automatic ticket resolution")
    allowed_teams: List[str] = Field(default=[], description="List of team IDs allowed for auto-resolution")
    excluded_labels: List[str] = Field(default=["manual-only", "design", "blocked"], description="Labels to exclude from auto-resolution")
    max_priority: Optional[int] = Field(default=2, description="Maximum priority level (0=urgent, 4=no priority)", ge=0, le=4)
    polling_interval: int = Field(default=300, description="Polling interval in seconds", ge=60, le=3600)
    max_concurrent_resolutions: int = Field(default=3, description="Maximum concurrent resolutions", ge=1, le=10)
    require_approval: bool = Field(default=False, description="Require human approval before resolution")
    confidence_threshold: float = Field(default=0.5, description="Minimum confidence threshold for auto-resolution", ge=0.0, le=1.0)


class TicketResolutionRequest(BaseModel):
    """Request to manually trigger ticket resolution."""
    ticket_id: str = Field(description="Linear ticket ID")
    force_resolution: bool = Field(default=False, description="Force resolution even if confidence is low")


class TicketAnalysisRequest(BaseModel):
    """Request to analyze a ticket for resolvability."""
    ticket_id: str = Field(description="Linear ticket ID")
    include_comments: bool = Field(default=True, description="Include comments in analysis")


# API Endpoints

@router.get("/integrations/{integration_id}/config")
async def get_auto_resolution_config(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the current auto-resolution configuration for a Linear integration."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Linear integration not found")

    # Get current config
    config = integration.config or {}
    auto_resolution_config = config.get("linear_auto_resolution", {})

    return LinearAutoResolutionConfig(**auto_resolution_config)


@router.put("/integrations/{integration_id}/config")
async def update_auto_resolution_config(
    integration_id: int,
    config: LinearAutoResolutionConfig,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the auto-resolution configuration for a Linear integration."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Active Linear integration not found")

    # Validate team IDs if provided
    if config.allowed_teams:
        try:
            linear = LinearIntegration(integration_id=integration_id)
            available_teams = linear.get_teams()
            available_team_ids = [team["id"] for team in available_teams]

            invalid_teams = [team_id for team_id in config.allowed_teams if team_id not in available_team_ids]
            if invalid_teams:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid team IDs: {', '.join(invalid_teams)}"
                )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error validating teams: {str(e)}")

    # Update integration config
    if not integration.config:
        integration.config = {}

    integration.config["linear_auto_resolution"] = config.dict()
    integration.updated_at = datetime.utcnow()

    db.commit()

    return {"message": "Configuration updated successfully", "config": config}


@router.get("/integrations/{integration_id}/teams")
async def get_available_teams(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get available teams for the Linear integration."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Active Linear integration not found")

    try:
        linear = LinearIntegration(integration_id=integration_id)
        teams = linear.get_teams()
        return {"teams": teams}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching teams: {str(e)}")


@router.get("/integrations/{integration_id}/resolvable-tickets")
async def get_resolvable_tickets(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, description="Maximum number of tickets to return", ge=1, le=200),
    team_ids: Optional[str] = Query(default=None, description="Comma-separated team IDs"),
    exclude_labels: Optional[str] = Query(default=None, description="Comma-separated labels to exclude"),
    max_priority: Optional[int] = Query(default=None, description="Maximum priority level", ge=0, le=4)
):
    """Get tickets that are potentially resolvable by coding agents."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Active Linear integration not found")

    # Parse query parameters
    filters = {}

    if team_ids:
        filters["team_ids"] = [tid.strip() for tid in team_ids.split(",") if tid.strip()]

    if exclude_labels:
        filters["exclude_labels"] = [label.strip() for label in exclude_labels.split(",") if label.strip()]

    if max_priority is not None:
        filters["max_priority"] = max_priority

    try:
        # Get and analyze tickets
        analyzed_tickets = analyze_tickets_for_resolution(
            integration_id=integration_id,
            db=db,
            ticket_filters=filters,
            limit=limit
        )

        return {
            "tickets": analyzed_tickets,
            "total": len(analyzed_tickets),
            "filters_applied": filters
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing tickets: {str(e)}")


@router.post("/integrations/{integration_id}/analyze-ticket")
async def analyze_ticket(
    integration_id: int,
    request: TicketAnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Analyze a specific ticket for resolvability."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Active Linear integration not found")

    try:
        # Get ticket details
        linear = LinearIntegration(integration_id=integration_id)
        ticket_details = linear.analyze_issue_for_resolution(request.ticket_id)

        if not ticket_details.get("issue"):
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Analyze ticket
        analyzer = LinearTicketAnalyzer(linear)
        analysis = analyzer.analyze_ticket_resolvability(
            ticket=ticket_details["issue"],
            include_comments=request.include_comments
        )

        return {
            "ticket": ticket_details["issue"],
            "analysis": analysis,
            "comments_count": len(ticket_details.get("comments", [])),
            "attachments_count": len(ticket_details.get("attachments", []))
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing ticket: {str(e)}")


@router.post("/integrations/{integration_id}/resolve-ticket")
async def resolve_ticket(
    integration_id: int,
    request: TicketResolutionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Manually trigger resolution of a specific ticket."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Active Linear integration not found")

    # Check if we've already attempted this ticket recently
    recent_attempt = db.query(LinearResolutionAttempt).filter(
        and_(
            LinearResolutionAttempt.integration_id == integration_id,
            LinearResolutionAttempt.issue_id == request.ticket_id,
            LinearResolutionAttempt.claimed_at >= datetime.utcnow() - timedelta(hours=1)
        )
    ).first()

    if recent_attempt and not request.force_resolution:
        raise HTTPException(
            status_code=400,
            detail=f"Ticket resolution already attempted within the last hour (status: {recent_attempt.status}). Use force_resolution=true to override."
        )

    try:
        # Get and analyze ticket
        linear = LinearIntegration(integration_id=integration_id)
        ticket_details = linear.analyze_issue_for_resolution(request.ticket_id)

        if not ticket_details.get("issue"):
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = ticket_details["issue"]

        # Analyze resolvability unless forced
        analysis = None
        if not request.force_resolution:
            analyzer = LinearTicketAnalyzer(linear)
            analysis = analyzer.analyze_ticket_resolvability(ticket=ticket, include_comments=True)

            # Use configured confidence threshold
            config = integration.config or {}
            auto_resolution_config = config.get("linear_auto_resolution", {})
            confidence_threshold = auto_resolution_config.get("confidence_threshold", 0.5)

            if analysis["confidence_score"] < confidence_threshold:
                raise HTTPException(
                    status_code=400,
                    detail=f"Ticket has low resolvability confidence ({analysis['confidence_score']:.2f} < {confidence_threshold}). Use force_resolution=true to override."
                )

        # Start resolution in background
        async def resolve_ticket_task():
            resolver_db = SessionLocal()
            try:
                resolver = LinearTicketResolver(integration_id, resolver_db)
                await resolver.resolve_ticket(ticket, analysis)
            finally:
                resolver_db.close()

        background_tasks.add_task(resolve_ticket_task)

        return {
            "message": "Ticket resolution started",
            "ticket_id": request.ticket_id,
            "ticket_identifier": ticket.get("identifier", "Unknown"),
            "analysis": analysis,
            "forced": request.force_resolution
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting ticket resolution: {str(e)}")


@router.get("/integrations/{integration_id}/resolution-attempts")
async def get_resolution_attempts(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, description="Maximum number of attempts to return", ge=1, le=200),
    status: Optional[str] = Query(default=None, description="Filter by status"),
    ticket_id: Optional[str] = Query(default=None, description="Filter by ticket ID")
):
    """Get resolution attempts for the integration."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Linear integration not found")

    # Build query
    query = db.query(LinearResolutionAttempt).filter(
        LinearResolutionAttempt.integration_id == integration_id
    )

    if status:
        query = query.filter(LinearResolutionAttempt.status == status.upper())

    if ticket_id:
        query = query.filter(LinearResolutionAttempt.issue_id == ticket_id)

    # Get attempts with pagination
    attempts = query.order_by(desc(LinearResolutionAttempt.claimed_at)).limit(limit).all()

    # Format response
    attempts_data = []
    for attempt in attempts:
        attempts_data.append({
            "id": attempt.id,
            "issue_id": attempt.issue_id,
            "issue_identifier": attempt.issue_identifier,
            "issue_title": attempt.issue_title,
            "agent_name": attempt.agent_name,
            "status": attempt.status,
            "confidence_score": float(attempt.confidence_score) if attempt.confidence_score else None,
            "ticket_type": attempt.ticket_type,
            "complexity": attempt.complexity,
            "estimated_effort": attempt.estimated_effort,
            "claimed_at": attempt.claimed_at.isoformat(),
            "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
            "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
            "resolution_summary": attempt.resolution_summary,
            "failure_reason": attempt.failure_reason,
            "metadata": attempt.resolution_metadata
        })

    return {
        "attempts": attempts_data,
        "total": len(attempts_data),
        "filters": {
            "status": status,
            "ticket_id": ticket_id,
            "limit": limit
        }
    }


@router.get("/integrations/{integration_id}/analytics")
async def get_resolution_analytics(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    days: int = Query(default=30, description="Number of days to analyze", ge=1, le=365)
):
    """Get analytics for ticket resolution performance."""

    # Verify user owns this integration
    integration = db.query(Integration).filter(
        and_(
            Integration.id == integration_id,
            Integration.user_id == current_user.id,
            Integration.provider == "LINEAR"
        )
    ).first()

    if not integration:
        raise HTTPException(status_code=404, detail="Linear integration not found")

    # Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get resolution attempts in date range
    attempts = db.query(LinearResolutionAttempt).filter(
        and_(
            LinearResolutionAttempt.integration_id == integration_id,
            LinearResolutionAttempt.claimed_at >= start_date
        )
    ).all()

    # Calculate statistics
    total_attempts = len(attempts)
    successful = len([a for a in attempts if a.status == LinearResolutionAttemptStatus.COMPLETED])
    failed = len([a for a in attempts if a.status == LinearResolutionAttemptStatus.FAILED])
    in_progress = len([a for a in attempts if a.status in [
        LinearResolutionAttemptStatus.CLAIMED,
        LinearResolutionAttemptStatus.ANALYZING,
        LinearResolutionAttemptStatus.IMPLEMENTING,
        LinearResolutionAttemptStatus.TESTING
    ]])

    success_rate = (successful / total_attempts) if total_attempts > 0 else 0

    # Calculate average resolution time for completed attempts
    completed_attempts = [a for a in attempts if a.status == LinearResolutionAttemptStatus.COMPLETED and a.completed_at and a.claimed_at]
    avg_resolution_time = None
    if completed_attempts:
        total_time = sum([(a.completed_at - a.claimed_at).total_seconds() for a in completed_attempts])
        avg_resolution_time = total_time / len(completed_attempts)

    # Group by ticket type
    ticket_types = {}
    for attempt in attempts:
        ticket_type = attempt.ticket_type or "unknown"
        if ticket_type not in ticket_types:
            ticket_types[ticket_type] = {"total": 0, "successful": 0}
        ticket_types[ticket_type]["total"] += 1
        if attempt.status == LinearResolutionAttemptStatus.COMPLETED:
            ticket_types[ticket_type]["successful"] += 1

    # Top failure reasons
    failure_reasons = {}
    for attempt in attempts:
        if attempt.status == LinearResolutionAttemptStatus.FAILED and attempt.failure_reason:
            reason = attempt.failure_reason[:100]  # Truncate long reasons
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    top_failure_reasons = sorted(failure_reasons.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": days
        },
        "summary": {
            "total_attempts": total_attempts,
            "successful": successful,
            "failed": failed,
            "in_progress": in_progress,
            "success_rate": round(success_rate, 3),
            "avg_resolution_time_seconds": avg_resolution_time
        },
        "ticket_types": ticket_types,
        "top_failure_reasons": top_failure_reasons,
        "integration_name": integration.name
    }


@router.post("/polling-service/start")
async def start_polling_service(
    config: Optional[PollingConfig] = None,
    current_user: User = Depends(get_current_user)
):
    """Start the Linear ticket polling service."""

    # Only allow admin users to control the polling service
    # You might want to add a proper admin check here

    try:
        await polling_service_manager.start_service(config)
        return {"message": "Polling service started successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting service: {str(e)}")


@router.post("/polling-service/stop")
async def stop_polling_service(current_user: User = Depends(get_current_user)):
    """Stop the Linear ticket polling service."""

    try:
        await polling_service_manager.stop_service()
        return {"message": "Polling service stopped successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error stopping service: {str(e)}")


@router.get("/polling-service/status")
async def get_polling_service_status(current_user: User = Depends(get_current_user)):
    """Get the status of the Linear ticket polling service."""

    return polling_service_manager.get_status()


@router.post("/polling-service/reload")
async def reload_polling_service(
    config: PollingConfig,
    current_user: User = Depends(get_current_user)
):
    """Reload the polling service with new configuration."""

    try:
        await polling_service_manager.reload_config(config)
        return {"message": "Polling service reloaded successfully", "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading service: {str(e)}")


# Health check endpoint
@router.get("/health")
async def health_check():
    """Health check endpoint for the Linear ticket resolution system."""

    db = SessionLocal()
    try:
        # Check database connectivity
        active_integrations = db.query(Integration).filter(
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        ).count()

        # Check polling service status
        service_status = polling_service_manager.get_status()

        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "active_integrations": active_integrations,
            "polling_service": service_status,
            "api_available": True
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }
        )
    finally:
        db.close()