"""
Linear Ticket Resolution Service

This service handles the end-to-end process of resolving Linear tickets using
the existing agent orchestrator. It converts tickets to pseudo-incidents and
feeds them into the proven agent resolution system.
"""
import os
import json
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
import logging

from src.database.models import (
    Integration, LinearResolutionAttempt, LinearResolutionAttemptStatus,
    Incident, IncidentStatus, IncidentSeverity, User
)
from src.integrations.linear.integration import LinearIntegration
from src.core.linear_ticket_analyzer import LinearTicketAnalyzer, analyze_tickets_for_resolution
from src.agents.orchestrator import run_robust_crew
from src.utils.integrations import get_linear_integration_for_user
from src.integrations.github.integration import GithubIntegration
from src.database.database import SessionLocal
from src.services.redpanda_service import redpanda_service

# Configure logging
logger = logging.getLogger(__name__)

# Global thread pool for ticket resolution
_resolution_executor = ThreadPoolExecutor(
    max_workers=5,  # Limit concurrent resolutions
    thread_name_prefix="linear-resolver"
)


class LinearTicketResolver:
    """Manages the resolution of Linear tickets using existing agent infrastructure."""

    def __init__(self, integration_id: int, db: Session):
        self.integration_id = integration_id
        self.db = db

        # Get integration details
        self.integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        ).first()

        if not self.integration:
            raise ValueError(f"No active Linear integration found with ID {integration_id}")

        self.user_id = self.integration.user_id
        self.linear = LinearIntegration(integration_id=integration_id)
        self.analyzer = LinearTicketAnalyzer(self.linear)

    def create_incident_from_ticket(self, ticket: Dict[str, Any]) -> Incident:
        """
        Convert a Linear ticket to a pseudo-incident that can be processed by agents.

        Args:
            ticket: Linear ticket data

        Returns:
            Incident object (not yet saved to database)
        """
        # Extract ticket information
        ticket_id = ticket["id"]
        ticket_identifier = ticket.get("identifier", "Unknown")
        title = ticket.get("title", "Linear Ticket Resolution")
        description = ticket.get("description", "")
        labels = [label.get("name", "") for label in ticket.get("labels", [])]
        priority = ticket.get("priority", 2)  # Default to medium priority
        team = ticket.get("team", {})
        assignee = ticket.get("assignee")

        # Map Linear priority to incident severity
        priority_to_severity = {
            0: IncidentSeverity.CRITICAL,  # Urgent
            1: IncidentSeverity.HIGH,      # High
            2: IncidentSeverity.MEDIUM,    # Medium
            3: IncidentSeverity.LOW,       # Low
            4: IncidentSeverity.LOW        # No Priority
        }
        severity = priority_to_severity.get(priority, IncidentSeverity.MEDIUM)

        # Create service name from team
        service_name = team.get("name", "linear-tickets")

        # Build comprehensive description for agent analysis
        agent_description = self._build_agent_description(ticket)

        # Create pseudo-incident
        incident = Incident(
            title=f"[Linear {ticket_identifier}] {title}",
            description=agent_description,
            status=IncidentStatus.OPEN,
            severity=severity,
            service_name=service_name,
            source="linear",
            integration_id=self.integration_id,
            user_id=self.user_id,
            resolution_metadata_json={
                "linear_ticket": {
                    "id": ticket_id,
                    "identifier": ticket_identifier,
                    "url": ticket.get("url", ""),
                    "title": title,
                    "original_description": description,
                    "labels": labels,
                    "priority": priority,
                    "team": team,
                    "assignee": assignee
                },
                "ticket_resolution": {
                    "auto_resolution_attempt": True,
                    "created_for_resolution": True
                }
            },
            trigger_event={
                "type": "linear_ticket_resolution",
                "ticket_id": ticket_id,
                "ticket_identifier": ticket_identifier,
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        return incident

    def _build_agent_description(self, ticket: Dict[str, Any]) -> str:
        """Build comprehensive description for agent analysis."""
        parts = []

        # Header
        parts.append(f"# Linear Ticket Resolution: {ticket.get('identifier', 'Unknown')}")
        parts.append("")

        # Ticket details
        parts.append("## Ticket Information")
        parts.append(f"**Title**: {ticket.get('title', 'No title')}")
        parts.append(f"**URL**: {ticket.get('url', 'N/A')}")

        if ticket.get("description"):
            parts.append(f"**Description**:\n{ticket['description']}")

        # Metadata
        labels = ticket.get("labels", [])
        if labels:
            label_names = [label.get("name", str(label)) for label in labels]
            parts.append(f"**Labels**: {', '.join(label_names)}")

        priority = ticket.get("priority")
        if priority is not None:
            priority_map = {0: "Urgent", 1: "High", 2: "Medium", 3: "Low", 4: "No Priority"}
            parts.append(f"**Priority**: {priority_map.get(priority, f'Level {priority}')}")

        state = ticket.get("state", {})
        if state:
            parts.append(f"**Current Status**: {state.get('name', 'Unknown')}")

        team = ticket.get("team", {})
        if team:
            parts.append(f"**Team**: {team.get('name', 'Unknown')}")

        estimate = ticket.get("estimate")
        if estimate:
            parts.append(f"**Estimated Points**: {estimate}")

        # Add context for agents
        parts.append("")
        parts.append("## Agent Instructions")
        parts.append("This is an automated resolution attempt for a Linear ticket. Please:")
        parts.append("1. Analyze the ticket requirements carefully")
        parts.append("2. Explore the codebase to understand the context")
        parts.append("3. Implement the necessary changes following best practices")
        parts.append("4. Ensure proper testing and validation")
        parts.append("5. Create a PR with clear documentation of changes")

        return "\n".join(parts)

    async def resolve_ticket(
        self,
        ticket: Dict[str, Any],
        analysis: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Resolve a Linear ticket using the existing agent orchestrator.

        Args:
            ticket: Linear ticket data
            analysis: Optional pre-computed analysis results

        Returns:
            Resolution result dictionary
        """
        ticket_id = ticket["id"]
        ticket_identifier = ticket.get("identifier", "Unknown")

        print(f"🎯 Starting resolution for Linear ticket {ticket_identifier}")

        # Log ticket details for debugging
        if analysis:
            print(f"   📊 Confidence: {analysis.get('confidence_score', 0.0):.2f}")
            print(f"   🏷️  Type: {analysis.get('ticket_type', 'unknown')}")
            print(f"   ⚡ Complexity: {analysis.get('complexity', 'unknown')}")

        try:
            # 1. Create resolution attempt record with race condition protection
            attempt = LinearResolutionAttempt(
                integration_id=self.integration_id,
                user_id=self.user_id,
                issue_id=ticket_id,
                issue_identifier=ticket_identifier,
                issue_title=ticket.get("title", "")[:255],  # Truncate to fit column
                agent_name="linear-ticket-resolver",
                agent_version="1.0.0",
                status=LinearResolutionAttemptStatus.CLAIMED,
                claimed_at=datetime.utcnow()
            )

            # Add analysis data if available
            if analysis:
                attempt.confidence_score = str(analysis.get("confidence_score", 0.0))
                attempt.ticket_type = analysis.get("ticket_type", "unknown")
                attempt.complexity = analysis.get("complexity", "unknown")
                attempt.estimated_effort = analysis.get("estimated_effort", "unknown")
                attempt.resolution_metadata = {
                    "analysis": analysis,
                    "ticket_data": ticket
                }

            # Use try-except to handle race conditions
            try:
                self.db.add(attempt)
                self.db.commit()
                self.db.refresh(attempt)
                logger.info(f"Successfully claimed ticket {ticket_identifier} for resolution")

            except IntegrityError as e:
                # Another agent already claimed this ticket
                self.db.rollback()
                logger.warning(f"Ticket {ticket_identifier} already claimed by another agent")

                # Check existing attempt
                existing_attempt = self.db.query(LinearResolutionAttempt).filter(
                    and_(
                        LinearResolutionAttempt.integration_id == self.integration_id,
                        LinearResolutionAttempt.issue_id == ticket_id,
                        LinearResolutionAttempt.status.in_([
                            LinearResolutionAttemptStatus.CLAIMED,
                            LinearResolutionAttemptStatus.ANALYZING,
                            LinearResolutionAttemptStatus.IMPLEMENTING,
                            LinearResolutionAttemptStatus.TESTING
                        ])
                    )
                ).first()

                error_msg = f"Ticket {ticket_identifier} is already being resolved"
                if existing_attempt:
                    error_msg += f" by {existing_attempt.agent_name} (status: {existing_attempt.status})"

                return {
                    "success": False,
                    "error": error_msg,
                    "ticket_id": ticket_id,
                    "ticket_identifier": ticket_identifier,
                    "error_context": {
                        "phase": "ticket_claiming",
                        "reason": "already_claimed",
                        "timestamp": datetime.utcnow().isoformat()
                    }
                }

            # 2. Update Linear ticket status to "In Progress"
            await self._update_ticket_status(ticket_id, "In Progress",
                                           f"🤖 Automated resolution started by coding agent")

            # 3. Update attempt status
            attempt.status = LinearResolutionAttemptStatus.ANALYZING
            attempt.started_at = datetime.utcnow()
            self.db.commit()

            # 4. Convert ticket to pseudo-incident
            incident = self.create_incident_from_ticket(ticket)

            # Save the incident temporarily for agent processing
            self.db.add(incident)
            self.db.commit()
            self.db.refresh(incident)

            # 5. Update attempt status to implementing
            attempt.status = LinearResolutionAttemptStatus.IMPLEMENTING
            self.db.commit()

            # 6. Use existing agent orchestrator to resolve
            print(f"🚀 Invoking agent orchestrator for ticket {ticket_identifier}")

            # Get required parameters for agent orchestrator
            github_integration = self._get_github_integration()
            repo_name = self._get_repository_name()
            root_cause = self._generate_root_cause(ticket, analysis)

            if not github_integration:
                print(f"⚠️  No GitHub integration found - agent may not be able to create PRs")

            if not repo_name:
                print(f"⚠️  No repository name configured - agent may not be able to access code")

            agent_result = await asyncio.to_thread(
                run_robust_crew,
                incident=incident,
                logs=[],  # Linear tickets don't have logs like incidents
                root_cause=root_cause,
                github_integration=github_integration,
                repo_name=repo_name,
                db=self.db
            )

            # 7. Process results and update ticket
            resolution_result = await self._process_agent_result(
                ticket, incident, agent_result, attempt
            )

            print(f"✅ Completed resolution for ticket {ticket_identifier}")
            return resolution_result

        except Exception as e:
            print(f"❌ Error resolving ticket {ticket_identifier}: {e}")

            # Log more context for debugging
            import traceback
            print(f"   🔍 Full error traceback:")
            traceback.print_exc()

            # Mark attempt as failed
            if 'attempt' in locals():
                attempt.status = LinearResolutionAttemptStatus.FAILED
                attempt.failure_reason = str(e)[:1000]  # Truncate to fit
                attempt.completed_at = datetime.utcnow()

                # Store error context in resolution_metadata
                if attempt.resolution_metadata:
                    attempt.resolution_metadata.update({
                        "error": {
                            "message": str(e),
                            "timestamp": datetime.utcnow().isoformat(),
                            "phase": "resolution_execution"
                        }
                    })
                else:
                    attempt.resolution_metadata = {
                        "error": {
                            "message": str(e),
                            "timestamp": datetime.utcnow().isoformat(),
                            "phase": "resolution_execution"
                        }
                    }

                self.db.commit()

            # Update Linear ticket with more helpful error message
            error_msg = f"❌ Automated resolution failed: {str(e)[:100]}"
            await self._update_ticket_status(ticket_id, "Todo", error_msg)

            return {
                "success": False,
                "error": str(e),
                "ticket_id": ticket_id,
                "ticket_identifier": ticket_identifier,
                "error_context": {
                    "phase": "resolution_execution",
                    "timestamp": datetime.utcnow().isoformat()
                }
            }

    async def _process_agent_result(
        self,
        ticket: Dict[str, Any],
        incident: Incident,
        agent_result: Dict[str, Any],
        attempt: LinearResolutionAttempt
    ) -> Dict[str, Any]:
        """Process the results from the agent orchestrator."""

        ticket_id = ticket["id"]
        ticket_identifier = ticket.get("identifier", "Unknown")

        # Analyze agent result
        success = agent_result.get("status") == "success"
        action_taken = agent_result.get("action_taken", "")
        changes = agent_result.get("changes", {})
        pr_url = changes.get("pr_url") if changes else None

        if success and action_taken:
            # Successful resolution
            attempt.status = LinearResolutionAttemptStatus.COMPLETED
            attempt.resolution_summary = action_taken[:1000]  # Truncate to fit
            attempt.completed_at = datetime.utcnow()

            # Update resolution_metadata with results
            if attempt.resolution_metadata:
                attempt.resolution_metadata.update({
                    "resolution": {
                        "action_taken": action_taken,
                        "pr_url": pr_url,
                        "files_changed": changes.get("files_changed", []),
                        "completed_at": datetime.utcnow().isoformat()
                    }
                })
            else:
                attempt.resolution_metadata = {
                    "resolution": {
                        "action_taken": action_taken,
                        "pr_url": pr_url,
                        "files_changed": changes.get("files_changed", []),
                        "completed_at": datetime.utcnow().isoformat()
                    }
                }

            # Update Linear ticket
            resolution_message = self._build_resolution_message(action_taken, pr_url)
            await self._update_ticket_with_resolution(ticket_id, resolution_message)

            # Clean up temporary incident
            self.db.delete(incident)

        else:
            # Failed resolution
            attempt.status = LinearResolutionAttemptStatus.FAILED
            failure_reason = agent_result.get("error", "Agent did not complete successfully")
            attempt.failure_reason = failure_reason[:1000]  # Truncate to fit
            attempt.completed_at = datetime.utcnow()

            # Update Linear ticket
            await self._update_ticket_status(ticket_id, "Todo",
                                           f"❌ Automated resolution failed: {failure_reason[:100]}")

            # Clean up temporary incident
            self.db.delete(incident)

        self.db.commit()

        return {
            "success": success,
            "ticket_id": ticket_id,
            "ticket_identifier": ticket_identifier,
            "action_taken": action_taken,
            "pr_url": pr_url,
            "agent_result": agent_result
        }

    def _build_resolution_message(self, action_taken: str, pr_url: Optional[str]) -> str:
        """Build resolution message for Linear ticket."""
        parts = [
            "## 🤖 Automated Resolution Completed",
            "",
            "**Summary:**",
            action_taken,
            ""
        ]

        if pr_url:
            parts.extend([
                "**Pull Request:**",
                f"[View Changes]({pr_url})",
                ""
            ])

        parts.extend([
            "**Resolved by:** Healops Coding Agent",
            f"**Completed at:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        ])

        return "\n".join(parts)

    async def _update_ticket_status(self, ticket_id: str, state_name: str, comment: str):
        """Update Linear ticket status and add comment."""
        try:
            # Update state
            self.linear.update_issue_state(ticket_id, state_name)

            # Add comment
            self.linear.add_comment_to_issue(ticket_id, comment)

        except Exception as e:
            print(f"⚠️  Error updating Linear ticket status: {e}")

    async def _update_ticket_with_resolution(self, ticket_id: str, resolution_message: str):
        """Update Linear ticket with resolution details."""
        try:
            # Update ticket with resolution and mark as done
            self.linear.update_issue_with_resolution(ticket_id, resolution_message, "Done")

        except Exception as e:
            print(f"⚠️  Error updating Linear ticket with resolution: {e}")

    def _get_repository_name(self) -> Optional[str]:
        """Get the repository name for this integration."""
        config = self.integration.config or {}
        return config.get("repo_name") or config.get("repository")

    def _get_github_integration(self) -> Optional[GithubIntegration]:
        """Get GitHub integration for the user."""
        try:
            # Get user's GitHub integration
            github_integration_record = self.db.query(Integration).filter(
                and_(
                    Integration.user_id == self.user_id,
                    Integration.provider == "GITHUB",
                    Integration.status == "ACTIVE"
                )
            ).first()

            if not github_integration_record:
                print(f"⚠️  No active GitHub integration found for user {self.user_id}")
                return None

            # Create GithubIntegration instance
            github_integration = GithubIntegration(
                integration_id=github_integration_record.id
            )

            return github_integration

        except Exception as e:
            print(f"⚠️  Error getting GitHub integration: {e}")
            return None

    def _generate_root_cause(self, ticket: Dict[str, Any], analysis: Optional[Dict[str, Any]] = None) -> str:
        """Generate root cause description for the agent orchestrator."""
        parts = []

        # Basic ticket info
        title = ticket.get("title", "Linear ticket")
        identifier = ticket.get("identifier", "Unknown")
        parts.append(f"Linear ticket {identifier}: {title}")

        # Add ticket type if available
        if analysis:
            ticket_type = analysis.get("ticket_type", "unknown")
            complexity = analysis.get("complexity", "unknown")
            parts.append(f"Ticket type: {ticket_type} (complexity: {complexity})")

            # Add reasoning if available
            reasoning = analysis.get("reasoning", "").strip()
            if reasoning:
                parts.append(f"Analysis: {reasoning[:200]}")  # Limit length

        # Add description if available
        description = ticket.get("description", "").strip()
        if description:
            parts.append(f"Description: {description[:300]}")  # Limit length

        return ". ".join(parts)


class LinearTicketWorkflowManager:
    """Manages the overall workflow for Linear ticket resolution."""

    def __init__(self, db: Session):
        self.db = db

    def cleanup_stale_attempts(self, max_age_hours: int = 24) -> int:
        """
        Clean up stale resolution attempts that have been stuck in progress.

        Args:
            max_age_hours: Maximum age in hours for in-progress attempts

        Returns:
            Number of attempts cleaned up
        """
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)

        try:
            stale_attempts = self.db.query(LinearResolutionAttempt).filter(
                and_(
                    LinearResolutionAttempt.status.in_([
                        LinearResolutionAttemptStatus.CLAIMED,
                        LinearResolutionAttemptStatus.ANALYZING,
                        LinearResolutionAttemptStatus.IMPLEMENTING,
                        LinearResolutionAttemptStatus.TESTING
                    ]),
                    LinearResolutionAttempt.claimed_at <= cutoff_time
                )
            ).all()

            cleanup_count = 0
            for attempt in stale_attempts:
                attempt.status = LinearResolutionAttemptStatus.FAILED
                attempt.failure_reason = f"Stale attempt cleaned up after {max_age_hours} hours"
                attempt.completed_at = datetime.utcnow()

                # Update resolution_metadata
                if attempt.resolution_metadata:
                    attempt.resolution_metadata.update({
                        "cleanup": {
                            "reason": "stale_attempt",
                            "max_age_hours": max_age_hours,
                            "cleaned_up_at": datetime.utcnow().isoformat()
                        }
                    })
                else:
                    attempt.resolution_metadata = {
                        "cleanup": {
                            "reason": "stale_attempt",
                            "max_age_hours": max_age_hours,
                            "cleaned_up_at": datetime.utcnow().isoformat()
                        }
                    }

                cleanup_count += 1

            self.db.commit()

            if cleanup_count > 0:
                print(f"🧹 Cleaned up {cleanup_count} stale resolution attempts")

            return cleanup_count

        except Exception as e:
            print(f"⚠️  Error cleaning up stale attempts: {e}")
            self.db.rollback()
            return 0

    def get_active_integrations(self) -> List[Integration]:
        """Get all active Linear integrations with auto-resolution enabled."""
        return self.db.query(Integration).filter(
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        ).all()

    def should_attempt_resolution(
        self,
        integration: Integration,
        ticket: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Determine if a ticket should be attempted for resolution.

        Returns:
            (should_attempt, reason)
        """
        config = integration.config or {}
        auto_resolution_config = config.get("linear_auto_resolution", {})

        if not auto_resolution_config.get("enabled", False):
            return False, "Auto-resolution disabled for this integration"

        ticket_id = ticket["id"]

        # Check if we've already attempted this ticket recently
        recent_attempt = self.db.query(LinearResolutionAttempt).filter(
            and_(
                LinearResolutionAttempt.integration_id == integration.id,
                LinearResolutionAttempt.issue_id == ticket_id,
                LinearResolutionAttempt.claimed_at >= datetime.utcnow() - timedelta(hours=24)
            )
        ).first()

        if recent_attempt:
            return False, f"Already attempted within 24 hours (status: {recent_attempt.status})"

        # Check team restrictions
        allowed_teams = auto_resolution_config.get("allowed_teams", [])
        if allowed_teams:
            team = ticket.get("team", {})
            team_id = team.get("id", "")
            if team_id not in allowed_teams:
                return False, f"Team {team.get('name', 'Unknown')} not in allowed list"

        # Check excluded labels
        excluded_labels = auto_resolution_config.get("excluded_labels", [])
        if excluded_labels:
            ticket_labels = [label.get("name", "").lower() for label in ticket.get("labels", [])]
            for excluded in excluded_labels:
                if excluded.lower() in ticket_labels:
                    return False, f"Ticket has excluded label: {excluded}"

        # Check priority restrictions
        max_priority = auto_resolution_config.get("max_priority")
        if max_priority is not None:
            ticket_priority = ticket.get("priority", 999)
            if ticket_priority > max_priority:
                return False, f"Priority {ticket_priority} exceeds max allowed {max_priority}"

        # Check concurrent resolution limit
        max_concurrent = auto_resolution_config.get("max_concurrent_resolutions", 3)
        active_attempts = self.db.query(LinearResolutionAttempt).filter(
            and_(
                LinearResolutionAttempt.integration_id == integration.id,
                LinearResolutionAttempt.status.in_([
                    LinearResolutionAttemptStatus.CLAIMED,
                    LinearResolutionAttemptStatus.ANALYZING,
                    LinearResolutionAttemptStatus.IMPLEMENTING,
                    LinearResolutionAttemptStatus.TESTING
                ])
            )
        ).count()

        if active_attempts >= max_concurrent:
            return False, f"Max concurrent resolutions reached ({active_attempts}/{max_concurrent})"

        return True, "Eligible for resolution"

        return results

    async def process_tickets_for_integration(
        self,
        integration: Integration,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Process tickets for a single integration by publishing tasks to Redpanda.

        Returns:
            Summary of processing results
        """
        print(f"🔍 Processing tickets for integration {integration.id} ({integration.name})")

        results = {
            "integration_id": integration.id,
            "integration_name": integration.name,
            "tickets_analyzed": 0,
            "resolutions_queued": 0,
            "successful_resolutions": 0,  # Kept for backward compat, though now async
            "failed_resolutions": 0,
            "skipped_tickets": 0,
            "errors": []
        }

        try:
            # Get eligible tickets with error handling
            try:
                tickets = analyze_tickets_for_resolution(
                    integration_id=integration.id,
                    db=self.db,
                    limit=limit
                )
                results["tickets_analyzed"] = len(tickets)
            except Exception as e:
                print(f"❌ Error fetching/analyzing tickets for integration {integration.id}: {e}")
                results["errors"].append({
                    "integration": integration.id,
                    "phase": "ticket_analysis",
                    "error": str(e)
                })
                return results

            if not tickets:
                print(f"ℹ️  No eligible tickets found for integration {integration.id}")
                return results

            for analyzed_ticket in tickets:
                # analyzed_ticket has all ticket fields + "analysis" field
                ticket = analyzed_ticket  # The ticket data is at the root level
                analysis = analyzed_ticket.get("analysis", {})

                # Check if we should attempt resolution
                should_attempt, reason = self.should_attempt_resolution(integration, ticket)

                if not should_attempt:
                    print(f"⏭️  Skipping {ticket.get('identifier', 'Unknown')}: {reason}")
                    results["skipped_tickets"] += 1
                    continue

                # Check analysis confidence against configured threshold
                confidence = analysis.get("confidence_score", 0.0)
                config = integration.config or {}
                auto_resolution_config = config.get("linear_auto_resolution", {})
                confidence_threshold = auto_resolution_config.get("confidence_threshold", 0.5)

                if confidence < confidence_threshold:
                    print(f"⏭️  Skipping {ticket.get('identifier', 'Unknown')}: Low confidence ({confidence:.2f} < {confidence_threshold})")
                    results["skipped_tickets"] += 1
                    continue

                print(f"🚀 Queuing resolution for {ticket.get('identifier', 'Unknown')} (confidence: {confidence})")
                
                # Publish task to Redpanda
                task_data = {
                    "task_type": "resolve_linear_ticket",
                    "integration_id": integration.id,
                    "ticket_id": ticket["id"],
                    "ticket_identifier": ticket.get("identifier"),
                    "ticket_data": ticket,
                    "analysis": analysis,
                    "queued_at": datetime.utcnow().isoformat()
                }
                
                try:
                    success = redpanda_service.publish_ticket_task(task_data)
                    
                    if success:
                        results["resolutions_queued"] += 1
                        results["resolutions_attempted"] += 1 # Compatibility
                        print(f"✓ Queued task for {ticket.get('identifier', 'Unknown')}")
                    else:
                        results["failed_resolutions"] += 1
                        results["errors"].append({
                            "ticket": ticket.get("identifier", "Unknown"),
                            "error": "Failed to publish to Redpanda"
                        })
                        print(f"❌ Failed to queue task for {ticket.get('identifier', 'Unknown')}")
                        
                except Exception as e:
                    results["failed_resolutions"] += 1
                    results["errors"].append({
                        "ticket": ticket.get("identifier", "Unknown"),
                        "error": str(e)
                    })
                    print(f"❌ Error queuing {ticket.get('identifier', 'Unknown')}: {e}")

        except Exception as e:
            results["errors"].append({
                "integration": integration.id,
                "error": str(e)
            })
            print(f"❌ Error processing integration {integration.id}: {e}")

        return results

    async def run_resolution_cycle(self, max_tickets_per_integration: int = 5) -> Dict[str, Any]:
        """
        Run a complete resolution cycle across all active integrations.

        Returns:
            Summary of the entire cycle
        """
        print("🚀 Starting Linear ticket resolution cycle")

        # Clean up stale attempts first
        cleanup_count = self.cleanup_stale_attempts()
        print(f"🧹 Cleanup: {cleanup_count} stale attempts cleaned up")

        cycle_results = {
            "cycle_start": datetime.utcnow().isoformat(),
            "integrations_processed": 0,
            "total_tickets_analyzed": 0,
            "total_resolutions_queued": 0,
            "total_successful_resolutions": 0, # Kept for backward compat
            "total_failed_resolutions": 0,
            "integration_results": [],
            "errors": []
        }

        try:
            # Get all active integrations
            integrations = self.get_active_integrations()

            if not integrations:
                print("ℹ️  No active Linear integrations found")
                return cycle_results

            # Process each integration
            for integration in integrations:
                try:
                    result = await self.process_tickets_for_integration(
                        integration,
                        limit=max_tickets_per_integration
                    )

                    cycle_results["integrations_processed"] += 1
                    cycle_results["total_tickets_analyzed"] += result["tickets_analyzed"]
                    cycle_results["total_resolutions_queued"] += result["resolutions_queued"]
                    cycle_results["total_failed_resolutions"] += result["failed_resolutions"]
                    cycle_results["integration_results"].append(result)

                except Exception as e:
                    cycle_results["errors"].append({
                        "integration_id": integration.id,
                        "error": str(e)
                    })
                    print(f"❌ Error processing integration {integration.id}: {e}")

            cycle_results["cycle_end"] = datetime.utcnow().isoformat()

            print(f"🏁 Resolution cycle completed: {cycle_results['total_resolutions_queued']} tasks queued")

        except Exception as e:
            cycle_results["errors"].append({
                "cycle": str(e)
            })
            print(f"❌ Error in resolution cycle: {e}")

        return cycle_results


def process_ticket_task_from_redpanda(task_data: Dict[str, Any]):
    """
    Process Linear ticket resolution task from Redpanda message.
    This function is called by the Redpanda consumer when a ticket task is received.

    Args:
        task_data: Dictionary containing task information:
            - task_type: "resolve_linear_ticket"
            - integration_id: ID of the Linear integration
            - ticket_id: Linear ticket ID
            - ticket_identifier: Linear ticket identifier (e.g., "ENG-123")
            - ticket_data: Full ticket data dictionary
            - analysis: Analysis results from LinearTicketAnalyzer
            - queued_at: ISO timestamp when task was queued
    """
    task_type = task_data.get('task_type')
    if task_type != 'resolve_linear_ticket':
        logger.warning(f"Unknown task type: {task_type}")
        return

    integration_id = task_data.get('integration_id')
    ticket_data = task_data.get('ticket_data')
    analysis = task_data.get('analysis', {})
    ticket_identifier = task_data.get('ticket_identifier', 'Unknown')

    if not integration_id or not ticket_data:
        logger.error(f"Missing required data: integration_id={integration_id}, ticket_data={bool(ticket_data)}")
        return

    logger.info(f"🔄 [Redpanda Consumer] Processing ticket resolution task for {ticket_identifier}...")

    # Submit to thread pool with timeout and proper error handling
    def run_resolution_with_timeout():
        """Run resolution in a proper async context with timeout."""
        db = SessionLocal()
        try:
            # Create resolver instance
            resolver = LinearTicketResolver(integration_id=integration_id, db=db)

            # Create event loop for this thread (ThreadPoolExecutor provides clean thread)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Run with timeout to prevent hanging
                result = loop.run_until_complete(
                    asyncio.wait_for(
                        resolver.resolve_ticket(ticket_data, analysis),
                        timeout=1800  # 30 minutes max per resolution
                    )
                )
                logger.info(f"✅ Completed ticket resolution for {ticket_identifier}: {result.get('success', False)}")
                return result
            except asyncio.TimeoutError:
                logger.error(f"❌ Ticket resolution timed out for {ticket_identifier} after 30 minutes")
                # Update attempt status to failed due to timeout
                _mark_attempt_as_failed(
                    db, integration_id, ticket_data.get('id'),
                    "Resolution timed out after 30 minutes"
                )
                return {"success": False, "error": "Resolution timeout", "ticket_identifier": ticket_identifier}
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"❌ Error in ticket resolution for {ticket_identifier}: {e}", exc_info=True)
            # Update attempt status to failed
            _mark_attempt_as_failed(
                db, integration_id, ticket_data.get('id'),
                f"Resolution failed: {str(e)[:500]}"
            )
            return {"success": False, "error": str(e), "ticket_identifier": ticket_identifier}
        finally:
            db.close()

    try:
        # Submit to thread pool (non-blocking)
        future: Future = _resolution_executor.submit(run_resolution_with_timeout)

        # Store future for optional tracking (don't wait for result)
        logger.info(f"✓ Submitted ticket resolution task for {ticket_identifier} to thread pool")

        # Optional: Add callback for completion logging (non-blocking)
        def on_completion(fut: Future):
            try:
                result = fut.result(timeout=0.1)  # Non-blocking check
                if result and result.get('success'):
                    logger.info(f"🎉 Resolution successful for {ticket_identifier}")
                else:
                    logger.warning(f"⚠️ Resolution failed for {ticket_identifier}: {result.get('error', 'Unknown error')}")
            except FuturesTimeoutError:
                pass  # Still running, which is fine
            except Exception as e:
                logger.error(f"❌ Resolution completed with error for {ticket_identifier}: {e}")

        future.add_done_callback(on_completion)

    except Exception as e:
        logger.error(f"✗ Error submitting ticket task to thread pool: {e}", exc_info=True)


def _mark_attempt_as_failed(db: Session, integration_id: int, issue_id: str, failure_reason: str):
    """Helper to mark resolution attempt as failed."""
    try:
        attempt = db.query(LinearResolutionAttempt).filter(
            and_(
                LinearResolutionAttempt.integration_id == integration_id,
                LinearResolutionAttempt.issue_id == issue_id,
                LinearResolutionAttempt.status.in_([
                    LinearResolutionAttemptStatus.CLAIMED,
                    LinearResolutionAttemptStatus.ANALYZING,
                    LinearResolutionAttemptStatus.IMPLEMENTING,
                    LinearResolutionAttemptStatus.TESTING
                ])
            )
        ).first()

        if attempt:
            attempt.status = LinearResolutionAttemptStatus.FAILED
            attempt.failure_reason = failure_reason
            attempt.completed_at = datetime.utcnow()
            db.commit()
            logger.info(f"Marked resolution attempt as failed for issue {issue_id}")
    except Exception as e:
        logger.error(f"Error marking attempt as failed: {e}")
        db.rollback()


def shutdown_resolution_executor():
    """Gracefully shutdown the resolution thread pool."""
    logger.info("Shutting down Linear ticket resolution executor...")
    _resolution_executor.shutdown(wait=True)
    logger.info("Linear ticket resolution executor shutdown complete")