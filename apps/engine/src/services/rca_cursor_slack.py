"""
RCA + Cursor prompt flow: build deep RCA with snippets, generate Cursor prompt, persist to action_result, post to Slack.

Scope: This flow stops at pushing RCA + Cursor prompt to Slack. It does NOT trigger resolution
(e.g. coding agent, PR creation, or auto-fix). Resolution can be wired here in the future.
Runs as a background task after incident analysis. Uses SLACK_BOT_TOKEN_ALEX; channel from
SLACK_RCA_CHANNEL_ID or Alex bot's connected channel (AgentEmployee.slack_channel_id).
"""
import os
import logging
from typing import Optional

from src.database.database import SessionLocal
from src.database.models import Incident, LogEntry, AgentEmployee
from src.core.ai_analysis import (
    get_repo_name_from_integration,
    fetch_code_snippets_for_rca,
    build_deep_rca_string,
    generate_cursor_prompt,
)
from src.utils.integrations import get_github_integration_for_user
from src.integrations.github.integration import GithubIntegration
from src.services.slack.service import SlackService, build_rca_cursor_slack_blocks

logger = logging.getLogger(__name__)


def rca_cursor_slack_flow(incident_id: int, user_id: Optional[int] = None) -> None:
    """
    Build deep RCA + Cursor prompt, persist to action_result, post to Slack. Stops at Slack;
    does not trigger resolution (coding agent/PR). Channel: SLACK_RCA_CHANNEL_ID if set,
    else Alex AgentEmployee.slack_channel_id. Uses SLACK_BOT_TOKEN_ALEX. Does not raise.
    """
    channel_id = os.getenv("SLACK_RCA_CHANNEL_ID")
    db = SessionLocal()
    try:
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            logger.warning("rca_cursor_slack_flow: incident %s not found", incident_id)
            return

        logs = []
        if incident.log_ids and isinstance(incident.log_ids, list) and incident.log_ids:
            logs = (
                db.query(LogEntry)
                .filter(LogEntry.id.in_(incident.log_ids))
                .order_by(LogEntry.timestamp.desc())
                .all()
            )

        root_cause = incident.root_cause or ""
        action_taken = incident.action_taken

        repo_name = None
        github_integration = None
        uid = incident.user_id or user_id
        if uid:
            github_integration = get_github_integration_for_user(db, uid)
            if github_integration:
                repo_name = get_repo_name_from_integration(
                    github_integration, incident.service_name
                )
        if not repo_name and incident.integration_id:
            from src.database.models import Integration

            integration = db.query(Integration).filter(
                Integration.id == incident.integration_id
            ).first()
            if integration:
                repo_name = get_repo_name_from_integration(
                    integration, incident.service_name
                )
        if not github_integration and incident.integration_id:
            from src.database.models import Integration

            integration = db.query(Integration).filter(
                Integration.id == incident.integration_id
            ).first()
            if integration and getattr(integration, "provider", None) == "GITHUB":
                try:
                    github_integration = GithubIntegration(
                        integration_id=integration.id
                    )
                except Exception:
                    github_integration = None

        snippets = fetch_code_snippets_for_rca(
            incident, logs, repo_name, github_integration
        )
        deep_rca_str = build_deep_rca_string(
            incident, logs, root_cause, action_taken, snippets
        )
        cursor_prompt_str = generate_cursor_prompt(
            deep_rca_str,
            incident.title or "Incident",
            incident.service_name or "unknown",
        )

        action_result = dict(incident.action_result or {})
        action_result["deep_rca"] = deep_rca_str
        action_result["cursor_prompt"] = cursor_prompt_str
        incident.action_result = action_result
        db.commit()

        if not channel_id:
            alex_agent = (
                db.query(AgentEmployee)
                .filter(AgentEmployee.email == "alexandra.chen@healops.work")
                .first()
            )
            if alex_agent and alex_agent.slack_channel_id:
                channel_id = alex_agent.slack_channel_id

        if channel_id:
            alex_token = os.getenv("SLACK_BOT_TOKEN_ALEX")
            if alex_token:
                try:
                    blocks, fallback_text = build_rca_cursor_slack_blocks(
                        incident_id,
                        incident.title or "Incident",
                        deep_rca_str,
                        cursor_prompt_str,
                    )
                    slack = SlackService(bot_token=alex_token)
                    slack.post_message(channel_id, fallback_text, blocks=blocks)
                    logger.info(
                        "rca_cursor_slack_flow: posted RCA + Cursor prompt to Slack for incident %s",
                        incident_id,
                    )
                # Future: optional resolution step (e.g. trigger coding agent / PR from here)
                except Exception as e:
                    logger.exception(
                        "rca_cursor_slack_flow: failed to post to Slack for incident %s: %s",
                        incident_id,
                        e,
                    )
            else:
                logger.warning(
                    "rca_cursor_slack_flow: SLACK_BOT_TOKEN_ALEX not set, skipping Slack"
                )
    except Exception as e:
        logger.exception(
            "rca_cursor_slack_flow: error for incident %s: %s", incident_id, e
        )
    finally:
        db.close()
