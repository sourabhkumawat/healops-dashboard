"""
Email service for sending notifications about PR creation and incidents.
Uses Brevo (formerly Sendinblue) API for sending emails.
"""
import os
from typing import Optional, Dict, Any
from datetime import datetime

try:
    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException
    BREVO_AVAILABLE = True
except ImportError:
    BREVO_AVAILABLE = False
    print("⚠️  sib-api-v3-sdk not installed. Install it with: pip install sib-api-v3-sdk")


def get_email_template(incident: Dict[str, Any], pr_url: str, pr_number: int) -> str:
    """
    Generate a beautiful HTML email template for PR creation notification.
    
    Args:
        incident: Incident data dictionary
        pr_url: URL to the pull request
        pr_number: Pull request number
        
    Returns:
        HTML email template string
    """
    severity_colors = {
        "CRITICAL": "#DC2626",  # red-600
        "HIGH": "#EA580C",      # orange-600
        "MEDIUM": "#D97706",    # amber-600
        "LOW": "#059669"        # emerald-600
    }
    
    severity = incident.get("severity", "MEDIUM").upper()
    severity_color = severity_colors.get(severity, "#6B7280")
    
    status_colors = {
        "OPEN": "#DC2626",
        "INVESTIGATING": "#EA580C",
        "HEALING": "#059669",
        "RESOLVED": "#10B981",
        "FAILED": "#DC2626"
    }
    
    status = incident.get("status", "OPEN").upper()
    status_color = status_colors.get(status, "#6B7280")
    
    # Format dates
    created_at = incident.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                created_at = dt.strftime("%B %d, %Y at %I:%M %p UTC")
            except:
                created_at = str(created_at)
        elif hasattr(created_at, 'strftime'):
            created_at = created_at.strftime("%B %d, %Y at %I:%M %p UTC")
    
    root_cause = incident.get("root_cause", "Analysis in progress...")
    action_taken = incident.get("action_taken", "No action taken yet")
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Pull Request Created - HealOps</title>
    </head>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #18181B;">
        <table role="presentation" style="width: 100%; border-collapse: collapse; background-color: #18181B;">
            <tr>
                <td style="padding: 40px 20px;">
                    <table role="presentation" style="max-width: 640px; margin: 0 auto; background-color: #27272A; border-radius: 12px; border: 1px solid #3F3F46; box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3); overflow: hidden;">
                        <!-- Header with Branding -->
                        <tr>
                            <td style="padding: 32px 40px 24px; background: linear-gradient(135deg, #18181B 0%, #27272A 100%); border-bottom: 1px solid #3F3F46;">
                                <table role="presentation" style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="text-align: left; vertical-align: middle;">
                                            <div style="display: inline-block; background-color: #3F3F46; border-radius: 8px; padding: 8px; margin-bottom: 16px;">
                                                <span style="color: #16A34A; font-size: 24px; font-weight: 700; letter-spacing: -0.5px;">HealOps</span>
                                            </div>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td>
                                            <h1 style="margin: 0; color: #FAFAFA; font-size: 32px; font-weight: 700; letter-spacing: -0.5px; line-height: 1.2;">
                                                Pull Request Created
                                            </h1>
                                            <p style="margin: 12px 0 0; color: #A1A1AA; font-size: 16px; line-height: 1.5;">
                                                Your incident has been automatically fixed
                                            </p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                        
                        <!-- Content -->
                        <tr>
                            <td style="padding: 40px;">
                                <!-- PR Highlight Card -->
                                <div style="background: linear-gradient(135deg, #16A34A 0%, #15803D 100%); border-radius: 12px; padding: 32px; text-align: center; margin-bottom: 32px; box-shadow: 0 4px 12px rgba(22, 163, 74, 0.3);">
                                    <div style="margin-bottom: 16px;">
                                        <span style="display: inline-block; background-color: rgba(255, 255, 255, 0.2); border-radius: 50%; width: 64px; height: 64px; line-height: 64px; font-size: 32px;">
                                            ✓
                                        </span>
                                    </div>
                                    <h2 style="margin: 0 0 8px; color: #FFFFFF; font-size: 24px; font-weight: 700; letter-spacing: -0.3px;">
                                        Pull Request #{pr_number}
                                    </h2>
                                    <p style="margin: 0 0 24px; color: rgba(255, 255, 255, 0.9); font-size: 14px;">
                                        Ready for review
                                    </p>
                                    <a href="{pr_url}" style="display: inline-block; background-color: #FFFFFF; color: #16A34A; text-decoration: none; padding: 14px 32px; border-radius: 8px; font-weight: 600; font-size: 15px; letter-spacing: 0.2px; transition: transform 0.2s;">
                                        View Pull Request →
                                    </a>
                                </div>
                                
                                <!-- Incident Details Card -->
                                <div style="background-color: #3F3F46; border-radius: 12px; padding: 28px; margin-bottom: 24px; border: 1px solid #52525B;">
                                    <h3 style="margin: 0 0 20px; color: #FAFAFA; font-size: 18px; font-weight: 600; letter-spacing: -0.2px; border-bottom: 1px solid #52525B; padding-bottom: 12px;">
                                        Incident Details
                                    </h3>
                                    
                                    <table role="presentation" style="width: 100%; border-collapse: collapse;">
                                        <tr>
                                            <td style="padding: 10px 0; color: #A1A1AA; font-size: 14px; font-weight: 500; width: 140px; vertical-align: top;">
                                                Incident ID
                                            </td>
                                            <td style="padding: 10px 0; color: #FAFAFA; font-size: 14px; font-weight: 600;">
                                                #{incident.get("id", "N/A")}
                                            </td>
                                        </tr>
                                        <tr>
                                            <td style="padding: 10px 0; color: #A1A1AA; font-size: 14px; font-weight: 500; vertical-align: top;">
                                                Title
                                            </td>
                                            <td style="padding: 10px 0; color: #FAFAFA; font-size: 14px; line-height: 1.5;">
                                                {incident.get("title", "N/A")}
                                            </td>
                                        </tr>
                                        <tr>
                                            <td style="padding: 10px 0; color: #A1A1AA; font-size: 14px; font-weight: 500; vertical-align: top;">
                                                Service
                                            </td>
                                            <td style="padding: 10px 0; color: #FAFAFA; font-size: 14px; font-weight: 600;">
                                                {incident.get("service_name", "N/A")}
                                            </td>
                                        </tr>
                                        <tr>
                                            <td style="padding: 10px 0; color: #A1A1AA; font-size: 14px; font-weight: 500; vertical-align: top;">
                                                Severity
                                            </td>
                                            <td style="padding: 10px 0;">
                                                <span style="display: inline-block; background-color: {severity_color}; color: #FFFFFF; padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">
                                                    {severity}
                                                </span>
                                            </td>
                                        </tr>
                                        <tr>
                                            <td style="padding: 10px 0; color: #A1A1AA; font-size: 14px; font-weight: 500; vertical-align: top;">
                                                Status
                                            </td>
                                            <td style="padding: 10px 0;">
                                                <span style="display: inline-block; background-color: {status_color}; color: #FFFFFF; padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">
                                                    {status}
                                                </span>
                                            </td>
                                        </tr>
                                        {f'<tr><td style="padding: 10px 0; color: #A1A1AA; font-size: 14px; font-weight: 500; vertical-align: top;">Created</td><td style="padding: 10px 0; color: #FAFAFA; font-size: 14px;">{created_at}</td></tr>' if created_at else ''}
                                    </table>
                                </div>
                                
                                <!-- Root Cause Analysis -->
                                <div style="background-color: #3F3F46; border-left: 4px solid #F59E0B; border-radius: 8px; padding: 24px; margin-bottom: 24px; border: 1px solid #52525B;">
                                    <div style="display: flex; align-items: center; margin-bottom: 12px;">
                                        <span style="font-size: 20px; margin-right: 10px;">🔍</span>
                                        <h3 style="margin: 0; color: #FAFAFA; font-size: 16px; font-weight: 600; letter-spacing: -0.1px;">
                                            Root Cause Analysis
                                        </h3>
                                    </div>
                                    <p style="margin: 0; color: #D4D4D8; font-size: 14px; line-height: 1.7;">
                                        {root_cause[:600]}{"..." if len(root_cause) > 600 else ""}
                                    </p>
                                </div>
                                
                                <!-- Action Taken -->
                                <div style="background-color: #1F2937; border-left: 4px solid #16A34A; border-radius: 8px; padding: 24px; margin-bottom: 24px; border: 1px solid #374151;">
                                    <div style="display: flex; align-items: center; margin-bottom: 12px;">
                                        <span style="font-size: 20px; margin-right: 10px;">✅</span>
                                        <h3 style="margin: 0; color: #FAFAFA; font-size: 16px; font-weight: 600; letter-spacing: -0.1px;">
                                            Recommended Action
                                        </h3>
                                    </div>
                                    <p style="margin: 0; color: #D4D4D8; font-size: 14px; line-height: 1.7;">
                                        {action_taken[:600]}{"..." if len(action_taken) > 600 else ""}
                                    </p>
                                </div>
                                
                                <!-- Files Changed -->
                                {f'''
                                <div style="background-color: #3F3F46; border-radius: 12px; padding: 24px; margin-bottom: 24px; border: 1px solid #52525B;">
                                    <div style="display: flex; align-items: center; margin-bottom: 16px;">
                                        <span style="font-size: 20px; margin-right: 10px;">📝</span>
                                        <h3 style="margin: 0; color: #FAFAFA; font-size: 16px; font-weight: 600; letter-spacing: -0.1px;">
                                            Files Changed
                                        </h3>
                                    </div>
                                    <div style="background-color: #27272A; border-radius: 8px; padding: 16px;">
                                        <ul style="margin: 0; padding-left: 20px; color: #D4D4D8; font-size: 14px; line-height: 2;">
                                            {chr(10).join(f"<li style='margin-bottom: 4px;'><code style='background-color: #18181B; color: #16A34A; padding: 4px 8px; border-radius: 4px; font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace; font-size: 13px; border: 1px solid #3F3F46;'>{file}</code></li>" for file in incident.get("pr_files_changed", []))}
                                        </ul>
                                    </div>
                                </div>
                                ''' if incident.get("pr_files_changed") else ''}
                                
                                <!-- Primary CTA Button -->
                                <div style="text-align: center; margin-top: 32px; margin-bottom: 8px;">
                                    <a href="{pr_url}" style="display: inline-block; background: linear-gradient(135deg, #16A34A 0%, #15803D 100%); color: #FFFFFF; text-decoration: none; padding: 16px 48px; border-radius: 8px; font-weight: 600; font-size: 16px; letter-spacing: 0.2px; box-shadow: 0 4px 12px rgba(22, 163, 74, 0.4); transition: transform 0.2s;">
                                        Review Pull Request
                                    </a>
                                </div>
                                <p style="text-align: center; margin: 12px 0 0; color: #71717A; font-size: 13px;">
                                    This PR was automatically generated by HealOps AI
                                </p>
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td style="padding: 32px 40px; background-color: #18181B; border-top: 1px solid #3F3F46; border-radius: 0 0 12px 12px;">
                                <table role="presentation" style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="text-align: center; padding-bottom: 16px;">
                                            <p style="margin: 0; color: #71717A; font-size: 13px; line-height: 1.5;">
                                                This email was automatically generated by <strong style="color: #16A34A;">HealOps</strong>
                                            </p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="text-align: center; padding-top: 16px; border-top: 1px solid #3F3F46;">
                                            <p style="margin: 0; color: #52525B; font-size: 12px;">
                                                © {datetime.now().year} HealOps. All rights reserved.
                                            </p>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    return html


def send_pr_creation_email(
    recipient_email: str,
    incident: Dict[str, Any],
    pr_url: str,
    pr_number: int
) -> bool:
    """
    Send an email notification when a PR is created using Brevo API.
    
    Args:
        recipient_email: Email address of the recipient
        incident: Incident data dictionary
        pr_url: URL to the pull request
        pr_number: Pull request number
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    if not BREVO_AVAILABLE:
        print("⚠️  Brevo SDK not available. Skipping email notification.")
        return False
    
    # Get Brevo API configuration from environment variables
    brevo_api_key = os.getenv("BREVO_API_KEY")
    from_email = os.getenv("BREVO_FROM_EMAIL", "noreply@healops.ai")
    from_name = os.getenv("BREVO_FROM_NAME", "HealOps")
    
    # If Brevo API key is not configured, skip sending email
    if not brevo_api_key:
        print("⚠️  Brevo API key not configured. Skipping email notification.")
        print(f"   To enable emails, set BREVO_API_KEY environment variable")
        return False
    
    try:
        # Configure API key
        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key['api-key'] = brevo_api_key
        
        # Create API instance
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
        
        # Generate HTML content
        html_content = get_email_template(incident, pr_url, pr_number)
        
        # Create plain text version
        text_content = f"""
Pull Request Created - HealOps

A new pull request has been created to fix an incident:

Incident ID: #{incident.get('id', 'N/A')}
Title: {incident.get('title', 'N/A')}
Service: {incident.get('service_name', 'N/A')}
Severity: {incident.get('severity', 'N/A')}
Status: {incident.get('status', 'N/A')}

Root Cause:
{incident.get('root_cause', 'Analysis in progress...')}

Recommended Action:
{incident.get('action_taken', 'No action taken yet')}

View the pull request: {pr_url}

---
This email was automatically generated by HealOps
        """.strip()
        
        # Create send email request
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": recipient_email}],
            sender={"name": from_name, "email": from_email},
            subject=f"🚀 Pull Request #{pr_number} Created - {incident.get('title', 'Incident Fix')}",
            html_content=html_content,
            text_content=text_content
        )
        
        # Send email
        api_response = api_instance.send_transac_email(send_smtp_email)
        
        print(f"✅ Email notification sent to {recipient_email} for PR #{pr_number}")
        print(f"   Message ID: {api_response.message_id}")
        return True
        
    except ApiException as e:
        print(f"❌ Brevo API error: {e}")
        print(f"   Status: {e.status}, Reason: {e.reason}")
        if e.body:
            print(f"   Body: {e.body}")
        return False
    except Exception as e:
        print(f"❌ Failed to send email notification: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_test_email(recipient_email: str, subject: str = "HealOps Email Test") -> bool:
    """
    Send a simple test email to verify Brevo email configuration.
    
    Args:
        recipient_email: Email address of the recipient
        subject: Email subject line (default: "HealOps Email Test")
        
    Returns:
        True if email was sent successfully, False otherwise
    """
    if not BREVO_AVAILABLE:
        print("⚠️  Brevo SDK not available. Skipping email notification.")
        return False
    
    # Get Brevo API configuration from environment variables
    brevo_api_key = os.getenv("BREVO_API_KEY")
    from_email = os.getenv("BREVO_FROM_EMAIL", "noreply@healops.ai")
    from_name = os.getenv("BREVO_FROM_NAME", "HealOps")
    
    # If Brevo API key is not configured, skip sending email
    if not brevo_api_key:
        print("⚠️  Brevo API key not configured. Skipping email notification.")
        print(f"   To enable emails, set BREVO_API_KEY environment variable")
        return False
    
    try:
        # Configure API key
        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key['api-key'] = brevo_api_key
        
        # Create API instance
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
        
        # Simple HTML content
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667EEA 0%, #764BA2 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f9fafb; padding: 30px; border-radius: 0 0 8px 8px; }}
                .success {{ background: #d1fae5; border-left: 4px solid #10b981; padding: 15px; margin: 20px 0; border-radius: 4px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>✅ Email Test Successful!</h1>
                </div>
                <div class="content">
                    <div class="success">
                        <h2>🎉 Email Service is Working!</h2>
                        <p>This is a test email from HealOps to verify that your Brevo email configuration is working correctly.</p>
                        <p><strong>If you received this email, it means:</strong></p>
                        <ul>
                            <li>✅ Brevo API connection is successful</li>
                            <li>✅ API authentication is working</li>
                            <li>✅ Email sending functionality is operational</li>
                        </ul>
                        <p><strong>Test Details:</strong></p>
                        <ul>
                            <li>Service: Brevo (Sendinblue)</li>
                            <li>From: {from_name} &lt;{from_email}&gt;</li>
                            <li>To: {recipient_email}</li>
                            <li>Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</li>
                        </ul>
                        <p>You can now use the email service to send notifications about incidents and pull requests.</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_content = f"""
Email Test Successful - HealOps

This is a test email from HealOps to verify that your Brevo email configuration is working correctly.

If you received this email, it means:
- Brevo API connection is successful
- API authentication is working
- Email sending functionality is operational

Test Details:
- Service: Brevo (Sendinblue)
- From: {from_name} <{from_email}>
- To: {recipient_email}
- Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

You can now use the email service to send notifications about incidents and pull requests.
        """.strip()
        
        # Create send email request
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": recipient_email}],
            sender={"name": from_name, "email": from_email},
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )
        
        # Send email
        api_response = api_instance.send_transac_email(send_smtp_email)
        
        print(f"✅ Test email sent successfully to {recipient_email}")
        print(f"   Message ID: {api_response.message_id}")
        return True
        
    except ApiException as e:
        print(f"❌ Brevo API error: {e}")
        print(f"   Status: {e.status}, Reason: {e.reason}")
        if e.body:
            print(f"   Body: {e.body}")
        return False
    except Exception as e:
        print(f"❌ Failed to send test email: {e}")
        import traceback
        traceback.print_exc()
        return False

