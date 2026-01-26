"""
Stats Controller - Handles system statistics and overview.
"""
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case, distinct, cast, Date, Float, String
from sqlalchemy.dialects.postgresql import aggregate_order_by
from datetime import datetime, timedelta

from src.database.models import (
    Incident, LogEntry, IncidentStatus, IncidentSeverity,
    AgentEmployee, AgentPR, LinearResolutionAttempt, LinearResolutionAttemptStatus
)
from src.api.controllers.base import get_user_id_from_request


class StatsController:
    """Controller for system statistics."""
    
    @staticmethod
    def get_system_stats(request: Request, db: Session):
        """Get system overview statistics for the authenticated user."""
        try:
            # Get authenticated user (middleware ensures this is set)
            user_id = get_user_id_from_request(request, db=db)

            # ============================================================================
            # OPTIMIZED: Single query for all incident counts by status and severity
            # ============================================================================
            incident_counts = db.query(
                func.count(Incident.id).label('total'),
                func.sum(case((Incident.status == "OPEN", 1), else_=0)).label('open'),
                func.sum(case((Incident.status == "INVESTIGATING", 1), else_=0)).label('investigating'),
                func.sum(case((Incident.status == "HEALING", 1), else_=0)).label('healing'),
                func.sum(case((Incident.status == "RESOLVED", 1), else_=0)).label('resolved'),
                func.sum(case((Incident.status == "FAILED", 1), else_=0)).label('failed'),
                func.sum(case((Incident.severity == "CRITICAL", 1), else_=0)).label('critical'),
                func.sum(case((Incident.severity == "HIGH", 1), else_=0)).label('high'),
                func.sum(case((Incident.severity == "MEDIUM", 1), else_=0)).label('medium'),
                func.sum(case((Incident.severity == "LOW", 1), else_=0)).label('low')
            ).filter(Incident.user_id == user_id).first()
            
            total_incidents = incident_counts.total or 0
            open_incidents = incident_counts.open or 0
            investigating_incidents = incident_counts.investigating or 0
            healing_incidents = incident_counts.healing or 0
            resolved_incidents = incident_counts.resolved or 0
            failed_incidents = incident_counts.failed or 0
            critical_incidents = incident_counts.critical or 0
            high_incidents = incident_counts.high or 0
            medium_incidents = incident_counts.medium or 0
            low_incidents = incident_counts.low or 0
            
            # Count total error logs
            error_logs_count = db.query(func.count(LogEntry.id)).filter(
                LogEntry.user_id == user_id,
                func.upper(LogEntry.severity).in_(["ERROR", "CRITICAL"])
            ).scalar() or 0
            
            # Get unique services count - simplified approach
            # Get unique service names from logs
            log_services_set = set([
                s[0] for s in db.query(LogEntry.service_name).distinct().filter(
                    LogEntry.user_id == user_id,
                    LogEntry.service_name.isnot(None),
                    LogEntry.service_name != ""
                ).all() if s[0]
            ])
            
            # Get unique service names from incidents
            incident_services_set = set([
                s[0] for s in db.query(Incident.service_name).distinct().filter(
                    Incident.user_id == user_id,
                    Incident.service_name.isnot(None),
                    Incident.service_name != ""
                ).all() if s[0]
            ])
            
            # Combine and count unique services
            unique_services = len(log_services_set.union(incident_services_set))
            
            # Determine system status
            active_incidents = open_incidents + investigating_incidents + healing_incidents
            if critical_incidents > 0 or (active_incidents > 0 and high_incidents > 0):
                system_status = "CRITICAL"
                system_status_color = "text-red-500"
            elif active_incidents > 0:
                system_status = "DEGRADED"
                system_status_color = "text-yellow-500"
            else:
                system_status = "OPERATIONAL"
                system_status_color = "text-green-500"
            
            # Calculate unhealthy services (services with open incidents) - optimized
            unhealthy_services_count = db.query(
                func.count(distinct(Incident.service_name))
            ).filter(
                Incident.user_id == user_id,
                Incident.status.in_(["OPEN", "INVESTIGATING", "HEALING"]),
                Incident.service_name.isnot(None),
                Incident.service_name != ""
            ).scalar() or 0
            
            # ============================================================================
            # NEW METRICS: Incident Resolution Metrics - OPTIMIZED
            # ============================================================================
            
            # Calculate MTTR (Mean Time to Resolution) - optimized with SQL
            mttr_result = db.query(
                func.avg(
                    func.extract('epoch', Incident.updated_at - Incident.created_at)
                ).label('avg_resolution_time')
            ).filter(
                Incident.user_id == user_id,
                Incident.status == "RESOLVED",
                Incident.created_at.isnot(None),
                Incident.updated_at.isnot(None)
            ).scalar()
            
            mttr_seconds = float(mttr_result) if mttr_result else 0
            
            # Auto-fix success rate (incidents with successful PR creation) - optimized
            # Count total attempts
            total_auto_fix_attempts = db.query(func.count(Incident.id)).filter(
                Incident.user_id == user_id,
                Incident.action_result.isnot(None)
            ).scalar() or 0
            
            # Count successful PRs - use simpler approach: load incidents with action_result
            # and check in Python (still efficient as we only load action_result field)
            incidents_with_action = db.query(Incident.action_result).filter(
                Incident.user_id == user_id,
                Incident.action_result.isnot(None)
            ).all()
            
            successful_prs = sum([
                1 for action_result in incidents_with_action
                if action_result and isinstance(action_result, dict) and action_result.get("pr_url")
            ])
            
            auto_fix_success_rate = (successful_prs / total_auto_fix_attempts * 100) if total_auto_fix_attempts > 0 else 0
            
            # PR Statistics - optimized with SQL aggregation
            pr_stats_query = db.query(
                func.count(AgentPR.id).label('total'),
                func.sum(case((AgentPR.qa_review_status == "pending", 1), else_=0)).label('pending_qa'),
                func.sum(case((
                    and_(
                        AgentPR.pr_url.isnot(None),
                        func.lower(AgentPR.pr_url).like('%draft%')
                    ), 1), else_=0
                )).label('draft')
            ).join(
                Incident, AgentPR.incident_id == Incident.id
            ).filter(Incident.user_id == user_id).first()
            
            total_prs = pr_stats_query.total or 0
            draft_prs = pr_stats_query.draft or 0
            ready_prs = total_prs - draft_prs
            pending_qa_prs = pr_stats_query.pending_qa or 0
            
            # Average PR review time - optimized with SQL
            avg_pr_review_time_result = db.query(
                func.avg(
                    func.extract('epoch', AgentPR.qa_reviewed_at - AgentPR.pr_created_at)
                ).label('avg_review_time')
            ).join(
                Incident, AgentPR.incident_id == Incident.id
            ).filter(
                Incident.user_id == user_id,
                AgentPR.qa_reviewed_at.isnot(None),
                AgentPR.pr_created_at.isnot(None)
            ).scalar()
            
            avg_pr_review_time_seconds = float(avg_pr_review_time_result) if avg_pr_review_time_result else 0
            
            # ============================================================================
            # NEW METRICS: Agent Activity Dashboard - OPTIMIZED
            # ============================================================================
            
            # Get agent counts by status - optimized
            agent_counts = db.query(
                func.count(AgentEmployee.id).label('total'),
                func.sum(case((AgentEmployee.status == "available", 1), else_=0)).label('available'),
                func.sum(case((AgentEmployee.status == "working", 1), else_=0)).label('working'),
                func.sum(case((AgentEmployee.status == "idle", 1), else_=0)).label('idle')
            ).first()
            
            # Get agents with current tasks (only load what we need)
            agents_with_tasks = db.query(
                AgentEmployee.name,
                AgentEmployee.current_task
            ).filter(
                AgentEmployee.current_task.isnot(None),
                AgentEmployee.current_task != ""
            ).all()
            
            # Calculate total completed tasks - optimized
            # Note: This still requires loading JSON, but we can optimize by using SQL JSON functions
            # For now, we'll load only the completed_tasks field
            completed_tasks_data = db.query(AgentEmployee.completed_tasks).all()
            total_completed_tasks = sum([
                len(ct[0]) if ct[0] and isinstance(ct[0], list) else 0
                for ct in completed_tasks_data
            ])
            
            agent_stats = {
                "total_agents": agent_counts.total or 0,
                "available": agent_counts.available or 0,
                "working": agent_counts.working or 0,
                "idle": agent_counts.idle or 0,
                "current_tasks": [
                    {"agent_name": a.name, "task": a.current_task}
                    for a in agents_with_tasks
                ],
                "total_completed_tasks": total_completed_tasks
            }
            
            # ============================================================================
            # NEW METRICS: Linear Ticket Resolution - OPTIMIZED
            # ============================================================================
            
            # Get linear attempt counts by status - optimized
            linear_counts = db.query(
                func.count(LinearResolutionAttempt.id).label('total'),
                func.sum(case((LinearResolutionAttempt.status == LinearResolutionAttemptStatus.CLAIMED, 1), else_=0)).label('claimed'),
                func.sum(case((LinearResolutionAttempt.status == LinearResolutionAttemptStatus.ANALYZING, 1), else_=0)).label('analyzing'),
                func.sum(case((LinearResolutionAttempt.status == LinearResolutionAttemptStatus.IMPLEMENTING, 1), else_=0)).label('implementing'),
                func.sum(case((LinearResolutionAttempt.status == LinearResolutionAttemptStatus.TESTING, 1), else_=0)).label('testing'),
                func.sum(case((LinearResolutionAttempt.status == LinearResolutionAttemptStatus.COMPLETED, 1), else_=0)).label('completed'),
                func.sum(case((LinearResolutionAttempt.status == LinearResolutionAttemptStatus.FAILED, 1), else_=0)).label('failed')
            ).filter(
                LinearResolutionAttempt.user_id == user_id
            ).first()
            
            linear_stats = {
                "total_attempts": linear_counts.total or 0,
                "claimed": linear_counts.claimed or 0,
                "analyzing": linear_counts.analyzing or 0,
                "implementing": linear_counts.implementing or 0,
                "testing": linear_counts.testing or 0,
                "completed": linear_counts.completed or 0,
                "failed": linear_counts.failed or 0,
            }
            
            # Success rate
            total_finished = linear_stats["completed"] + linear_stats["failed"]
            linear_stats["success_rate"] = (linear_stats["completed"] / total_finished * 100) if total_finished > 0 else 0
            
            # Average resolution time for completed attempts - optimized with SQL
            avg_linear_resolution_time_result = db.query(
                func.avg(
                    func.extract('epoch', LinearResolutionAttempt.completed_at - LinearResolutionAttempt.claimed_at)
                ).label('avg_resolution_time')
            ).filter(
                LinearResolutionAttempt.user_id == user_id,
                LinearResolutionAttempt.status == LinearResolutionAttemptStatus.COMPLETED,
                LinearResolutionAttempt.completed_at.isnot(None),
                LinearResolutionAttempt.claimed_at.isnot(None)
            ).scalar()
            
            avg_linear_resolution_time_seconds = float(avg_linear_resolution_time_result) if avg_linear_resolution_time_result else 0
            linear_stats["avg_resolution_time_seconds"] = avg_linear_resolution_time_seconds
            
            # Average confidence score - optimized with SQL
            avg_confidence_result = db.query(
                func.avg(cast(LinearResolutionAttempt.confidence_score, Float))
            ).filter(
                LinearResolutionAttempt.user_id == user_id,
                LinearResolutionAttempt.confidence_score.isnot(None)
            ).scalar()
            
            linear_stats["avg_confidence_score"] = float(avg_confidence_result) if avg_confidence_result else 0
            
            # ============================================================================
            # NEW METRICS: Time-Based Trends - OPTIMIZED
            # ============================================================================
            
            now = datetime.utcnow()
            seven_days_ago = now - timedelta(days=7)
            thirty_days_ago = now - timedelta(days=30)
            
            # Incidents over time (last 7 and 30 days) - optimized
            incidents_7d = db.query(func.count(Incident.id)).filter(
                Incident.user_id == user_id,
                Incident.created_at >= seven_days_ago
            ).scalar() or 0
            
            incidents_30d = db.query(func.count(Incident.id)).filter(
                Incident.user_id == user_id,
                Incident.created_at >= thirty_days_ago
            ).scalar() or 0
            
            # Error rate trends - optimized
            errors_7d = db.query(func.count(LogEntry.id)).filter(
                LogEntry.user_id == user_id,
                LogEntry.timestamp >= seven_days_ago,
                func.upper(LogEntry.severity).in_(["ERROR", "CRITICAL"])
            ).scalar() or 0
            
            errors_30d = db.query(func.count(LogEntry.id)).filter(
                LogEntry.user_id == user_id,
                LogEntry.timestamp >= thirty_days_ago,
                func.upper(LogEntry.severity).in_(["ERROR", "CRITICAL"])
            ).scalar() or 0
            
            # Daily breakdown for last 7 days - OPTIMIZED: Single query with date_trunc
            # Use PostgreSQL date_trunc to group by day in a single query
            daily_incidents_query = db.query(
                func.date(cast(Incident.created_at, Date)).label('date'),
                func.count(Incident.id).label('count')
            ).filter(
                Incident.user_id == user_id,
                Incident.created_at >= seven_days_ago
            ).group_by(
                func.date(cast(Incident.created_at, Date))
            ).order_by(
                func.date(cast(Incident.created_at, Date))
            ).all()
            
            # Create a map of date -> count
            daily_map = {str(row.date): row.count for row in daily_incidents_query}
            
            # Fill in missing days with 0
            daily_incidents = []
            for i in range(6, -1, -1):  # Last 7 days, reverse order
                day_date = (now - timedelta(days=i)).date()
                day_str = day_date.strftime("%Y-%m-%d")
                daily_incidents.append({
                    "date": day_str,
                    "count": daily_map.get(day_str, 0)
                })
            
            # ============================================================================
            # NEW METRICS: Source Breakdown - OPTIMIZED
            # ============================================================================
            
            # Incidents by source - optimized with GROUP BY
            source_counts = db.query(
                func.coalesce(Incident.source, 'unknown').label('source'),
                func.count(Incident.id).label('count')
            ).filter(
                Incident.user_id == user_id
            ).group_by(
                func.coalesce(Incident.source, 'unknown')
            ).all()
            
            incidents_by_source = {
                row.source: row.count 
                for row in source_counts 
                if row.count > 0
            }
            
            # Most affected services (top 5)
            service_incident_counts = db.query(
                Incident.service_name,
                func.count(Incident.id).label("count")
            ).filter(
                Incident.user_id == user_id,
                Incident.service_name.isnot(None),
                Incident.service_name != ""
            ).group_by(Incident.service_name).order_by(func.count(Incident.id).desc()).limit(5).all()
            
            most_affected_services = [
                {"service": s[0], "incident_count": s[1]}
                for s in service_incident_counts
            ]
            
            # Error distribution by service
            service_error_counts = db.query(
                LogEntry.service_name,
                func.count(LogEntry.id).label("count")
            ).filter(
                LogEntry.user_id == user_id,
                LogEntry.service_name.isnot(None),
                LogEntry.service_name != "",
                func.upper(LogEntry.severity).in_(["ERROR", "CRITICAL"])
            ).group_by(LogEntry.service_name).order_by(func.count(LogEntry.id).desc()).limit(5).all()
            
            error_distribution_by_service = [
                {"service": s[0], "error_count": s[1]}
                for s in service_error_counts
            ]
            
            return {
                # Original metrics
                "system_status": system_status,
                "system_status_color": system_status_color,
                "total_incidents": total_incidents,
                "open_incidents": open_incidents,
                "investigating_incidents": investigating_incidents,
                "healing_incidents": healing_incidents,
                "resolved_incidents": resolved_incidents,
                "failed_incidents": failed_incidents,
                "critical_incidents": critical_incidents,
                "high_incidents": high_incidents,
                "medium_incidents": medium_incidents,
                "low_incidents": low_incidents,
                "active_incidents": active_incidents,
                "total_services": unique_services,
                "unhealthy_services": unhealthy_services_count,
                "error_logs_count": error_logs_count,
                
                # New metrics: Incident Resolution
                "mttr_seconds": mttr_seconds,
                "auto_fix_success_rate": auto_fix_success_rate,
                "total_auto_fix_attempts": total_auto_fix_attempts,
                "successful_prs": successful_prs,
                "pr_stats": {
                    "total": total_prs,
                    "draft": draft_prs,
                    "ready": ready_prs,
                    "pending_qa": pending_qa_prs,
                    "avg_review_time_seconds": avg_pr_review_time_seconds
                },
                
                # New metrics: Agent Activity
                "agent_stats": agent_stats,
                
                # New metrics: Linear Resolution
                "linear_stats": linear_stats,
                
                # New metrics: Time-Based Trends
                "trends": {
                    "incidents_7d": incidents_7d,
                    "incidents_30d": incidents_30d,
                    "errors_7d": errors_7d,
                    "errors_30d": errors_30d,
                    "daily_incidents": daily_incidents
                },
                
                # New metrics: Source Breakdown
                "incidents_by_source": incidents_by_source,
                "most_affected_services": most_affected_services,
                "error_distribution_by_service": error_distribution_by_service
            }
        except Exception as e:
            print(f"ERROR in get_system_stats: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Failed to fetch statistics: {str(e)}")
