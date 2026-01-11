"""
QA Orchestrator for PR Review

Orchestrates the QA review process for pull requests created by Alex.
"""
from typing import Dict, Any, Optional
import os
import logging
from datetime import datetime
from src.agents.definitions import create_qa_agents
from src.tools.qa_review import (
    review_pr,
    get_pr_file_contents,
    comment_on_pr,
    request_pr_changes,
    approve_pr,
    analyze_code_quality,
    check_antipatterns,
    validate_solution
)
from src.integrations.github.integration import GithubIntegration
from src.database.database import SessionLocal
from src.database.models import AgentEmployee, Incident, LogEntry, AgentPR
from src.services.slack.service import SlackService
from crewai import Task, Crew
from sqlalchemy.orm import Session
from src.agents.orchestrator import (
    _update_agent_employee_status,
    AGENT_STATUS_AVAILABLE,
    AGENT_STATUS_WORKING
)

logger = logging.getLogger(__name__)

# Alex's GitHub username - could be "alexandra.chen" or the actual GitHub username
ALEX_GITHUB_USERNAME = os.getenv("ALEX_GITHUB_USERNAME", "alexandra.chen")


async def review_pr_for_alex(
    repo_name: str,
    pr_number: int,
    user_id: Optional[int] = None,
    integration_id: Optional[int] = None,
    db: Optional[Session] = None
) -> Dict[str, Any]:
    """
    Review a PR created by Alex and provide feedback.
    
    Args:
        repo_name: Repository name in format "owner/repo"
        pr_number: PR number
        user_id: Optional user ID for GitHub integration
        integration_id: Optional integration ID
        db: Optional database session
        
    Returns:
        Dictionary with review results
    """
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True
    
    try:
        # Get QA agent employee
        qa_agent = db.query(AgentEmployee).filter(
            AgentEmployee.role == "Senior QA Engineer",
            AgentEmployee.department == "QA"
        ).first()
        
        if not qa_agent:
            logger.warning("QA agent not found in database. Please onboard QA agent first.")
            return {
                "success": False,
                "error": "QA agent not found. Please onboard QA agent using onboard_agent_employee.py --role qa_reviewer"
            }
        
        # Initialize GitHub integration
        github = GithubIntegration(integration_id=integration_id) if integration_id else None
        if not github:
            # Try to get from user_id
            if user_id:
                from src.database.models import Integration
                integration = db.query(Integration).filter(
                    Integration.user_id == user_id,
                    Integration.provider == "GITHUB"
                ).first()
                if integration:
                    github = GithubIntegration(integration_id=integration.id)
        
        if not github or not github.client:
            return {
                "success": False,
                "error": "GitHub integration not found or not authenticated"
            }
        
        # Check if PR was created by Alex using database tracking
        # Since Alex doesn't have a GitHub account, PRs are created by HealOps app
        # We track this in the AgentPR table
        agent_pr = db.query(AgentPR).filter(
            AgentPR.pr_number == pr_number,
            AgentPR.repo_name == repo_name,
            AgentPR.qa_review_status.in_(["pending", "in_review"])
        ).first()
        
        if not agent_pr:
            logger.info(f"PR #{pr_number} is not tracked as created by Alex. Skipping review.")
            return {
                "success": False,
                "skipped": True,
                "reason": f"PR #{pr_number} is not tracked as created by Alex"
            }
        
        # Verify it's by Alex and get Alex's agent info
        alex_agent = db.query(AgentEmployee).filter(
            AgentEmployee.id == agent_pr.agent_employee_id,
            AgentEmployee.email == "alexandra.chen@healops.work"
        ).first()
        
        if not alex_agent:
            logger.info(f"PR #{pr_number} is not by Alex. Skipping review.")
            return {
                "success": False,
                "skipped": True,
                "reason": f"PR #{pr_number} is not by Alex"
            }
        
        # Get PR details
        pr_details = github.get_pr_details(repo_name, pr_number)
        if pr_details.get("status") != "success":
            return {
                "success": False,
                "error": f"Failed to get PR details: {pr_details.get('message')}"
            }
        
        # Update PR tracking status to "in_review"
        agent_pr.qa_review_status = "in_review"
        db.commit()
        
        logger.info(f"Starting QA review for PR #{pr_number} created by {alex_agent.name}")
        
        # Update QA agent status to "working"
        task_description = f"Reviewing PR #{pr_number}: {pr_details.get('title', '')[:100]}"
        _update_agent_employee_status(
            db=db,
            crewai_role="qa_reviewer",
            status=AGENT_STATUS_WORKING,
            current_task=task_description
        )
        
        # Initialize Slack service for QA agent
        slack_service = None
        if qa_agent.slack_bot_token:
            try:
                from src.auth.crypto_utils import decrypt_token
                bot_token = decrypt_token(qa_agent.slack_bot_token)
                slack_service = SlackService(bot_token=bot_token)
            except Exception as e:
                logger.warning(f"Failed to initialize Slack service for QA agent: {e}")
        
        # Notify in Slack that review is starting
        if slack_service and qa_agent.slack_channel_id:
            try:
                slack_service.post_message(
                    channel_id=qa_agent.slack_channel_id,
                    text=f"🔍 Starting review of PR #{pr_number} by Alex: {pr_details.get('title')}",
                    agent_name=qa_agent.name,
                    agent_department=qa_agent.department
                )
            except Exception as e:
                logger.warning(f"Failed to post Slack message: {e}")
        
        # Get PR review data
        pr_review_data = review_pr(repo_name, pr_number, user_id, integration_id)
        if not pr_review_data.get("success"):
            # Clear status if we can't proceed with review
            try:
                _update_agent_employee_status(
                    db=db,
                    crewai_role="qa_reviewer",
                    status=AGENT_STATUS_AVAILABLE,
                    current_task=None  # Clear current task
                )
            except Exception as status_error:
                logger.warning(f"Failed to update QA agent status on early return: {status_error}")
            return {
                "success": False,
                "error": pr_review_data.get("error", "Failed to review PR")
            }
        
        # Extract relevant information for review
        files_changed = pr_review_data.get("files", [])
        pr_title = pr_details.get("title", "")
        pr_body = pr_details.get("body", "")
        pr_author = pr_details.get("author", "") or alex_agent.name
        
        # Try to find related incident from PR body/description
        incident_id = None
        related_incident = None
        related_logs = []
        
        # Look for incident ID in PR body
        import re
        incident_match = re.search(r'Incident ID[:\s]*#?(\d+)', pr_body, re.IGNORECASE)
        if incident_match:
            try:
                incident_id = int(incident_match.group(1))
                related_incident = db.query(Incident).filter(Incident.id == incident_id).first()
                if related_incident:
                    related_logs = db.query(LogEntry).filter(
                        LogEntry.service_name == related_incident.service_name
                    ).order_by(LogEntry.timestamp.desc()).limit(10).all()
            except (ValueError, Exception) as e:
                logger.warning(f"Failed to get related incident: {e}")
        
        # Create QA review task
        qa_agents = create_qa_agents()
        qa_reviewer = qa_agents[0]
        
        # Build review context
        review_context = f"""
PR Number: {pr_number}
PR Title: {pr_title}
PR Author: {pr_author}
Files Changed: {len(files_changed)}

PR Description:
{pr_body[:1000]}

Files Changed:
{chr(10).join([f"- {f['filename']} ({f['status']}, +{f['additions']}/-{f['deletions']})" for f in files_changed[:10]])}
"""
        
        if related_incident:
            review_context += f"""
Related Incident:
- ID: {related_incident.id}
- Title: {related_incident.title}
- Root Cause: {related_incident.root_cause or 'Not specified'}
- Service: {related_incident.service_name}
"""
        
        if related_logs:
            error_messages = [log.message for log in related_logs if log.message]
            review_context += f"""
Related Error Logs:
{chr(10).join([f"- {msg[:200]}" for msg in error_messages[:5]])}
"""
        
        # Create review task
        review_task = Task(
            description=f"""
Review the following pull request created by Alex (Alexandra Chen):

{review_context}

Your task is to:
1. Review all changed files for code quality, antipatterns, and best practices
2. Analyze each file's changes (patches are provided in the PR details)
3. Check if the solution matches the error context and root cause (if available)
4. Identify any issues, antipatterns, or improvements needed
5. Comment on the PR with specific, actionable feedback
6. Request changes if critical issues are found, or approve if everything looks good

Review each changed file thoroughly. For each file:
- Get the file contents using get_pr_file_contents
- Analyze code quality using analyze_code_quality
- Check for antipatterns using check_antipatterns
- Validate the solution matches the error logs/context if available

After your review, provide a summary and:
- Comment on the PR with your findings
- Request changes if needed, or approve if everything is good
- Notify Alex via Slack about any issues found

Be thorough but constructive. Prioritize functional correctness and antipatterns over minor style issues.
""",
            agent=qa_reviewer,
            expected_output="A comprehensive PR review with specific issues found, comments posted on the PR, and either approval or request for changes."
        )
        
        # Create crew and run review
        crew = Crew(
            agents=[qa_reviewer],
            tasks=[review_task],
            verbose=True
        )
        
        logger.info(f"Running QA review for PR #{pr_number}")
        review_result = crew.kickoff()
        
        logger.info(f"QA review completed for PR #{pr_number}")
        
        # Parse review result to determine approval status
        review_status = "reviewed"
        review_result_str = str(review_result).lower()
        
        # Try to determine if changes were requested or approved
        if "request changes" in review_result_str or "changes requested" in review_result_str:
            review_status = "changes_requested"
        elif "approve" in review_result_str and "request" not in review_result_str:
            review_status = "approved"
        
        # Update PR tracking status
        agent_pr.qa_review_status = review_status
        agent_pr.qa_reviewed_by_id = qa_agent.id
        agent_pr.qa_reviewed_at = datetime.utcnow()
        agent_pr.last_reviewed_at = datetime.utcnow()
        db.commit()
        
        # Update QA agent status back to "available" and clear current_task
        completed_task_description = f"Reviewed PR #{pr_number}: {pr_details.get('title', '')[:100]}"
        _update_agent_employee_status(
            db=db,
            crewai_role="qa_reviewer",
            status=AGENT_STATUS_AVAILABLE,
            current_task=None,  # Clear current task
            task_completed=completed_task_description
        )
        
        # Notify in Slack that review is complete
        if slack_service and qa_agent.slack_channel_id:
            try:
                status_emoji = "✅" if review_status == "approved" else "⚠️" if review_status == "changes_requested" else "✅"
                slack_service.post_message(
                    channel_id=qa_agent.slack_channel_id,
                    text=f"{status_emoji} Completed review of PR #{pr_number}. Status: {review_status}. Check GitHub for details.",
                    agent_name=qa_agent.name,
                    agent_department=qa_agent.department
                )
            except Exception as e:
                logger.warning(f"Failed to post Slack completion message: {e}")
        
        # Notify Alex via Slack if issues found
        if review_status == "changes_requested" and slack_service and alex_agent.slack_channel_id:
            try:
                # Get Alex's Slack service
                if alex_agent.slack_bot_token:
                    from src.auth.crypto_utils import decrypt_token
                    alex_bot_token = decrypt_token(alex_agent.slack_bot_token)
                    alex_slack = SlackService(bot_token=alex_bot_token)
                    
                    # Format mention if Alex has a Slack user ID
                    alex_mention = ""
                    if alex_agent.slack_user_id:
                        alex_mention = f"<@{alex_agent.slack_user_id}>"
                    else:
                        alex_mention = "Alex"
                    
                    alex_slack.post_message(
                        channel_id=alex_agent.slack_channel_id,
                        text=f"⚠️ Hey {alex_mention}! I reviewed your PR #{pr_number} and found some issues. Please check the PR comments and fix them. PR: {pr_details.get('url', '')}",
                        agent_name=qa_agent.name,
                        agent_department=qa_agent.department
                    )
            except Exception as e:
                logger.warning(f"Failed to notify Alex via Slack: {e}")
        
        return {
            "success": True,
            "pr_number": pr_number,
            "review_result": str(review_result),
            "files_reviewed": len(files_changed),
            "review_status": review_status,
            "message": "QA review completed"
        }
        
    except Exception as e:
        logger.error(f"Error during QA review: {e}", exc_info=True)
        # Update QA agent status back to "available" on error
        try:
            _update_agent_employee_status(
                db=db,
                crewai_role="qa_reviewer",
                status=AGENT_STATUS_AVAILABLE,
                current_task=None  # Clear current task
            )
        except Exception as status_error:
            logger.warning(f"Failed to update QA agent status on error: {status_error}")
        
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        if should_close_db:
            db.close()
