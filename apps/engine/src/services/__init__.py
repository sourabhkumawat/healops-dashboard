"""Services for external integrations."""
from .slack.service import SlackService
from .cleanup.service import cleanup_service
# Email service exports functions, not a class
from .email.service import (
    send_pr_creation_email,
    send_incident_resolved_email,
    send_test_email,
    log_email_to_database
)

__all__ = [
    'SlackService',
    'cleanup_service',
    'send_pr_creation_email',
    'send_incident_resolved_email',
    'send_test_email',
    'log_email_to_database',
]
