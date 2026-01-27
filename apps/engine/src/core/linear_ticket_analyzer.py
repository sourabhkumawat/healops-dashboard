"""
Linear Ticket Analysis module for determining resolution feasibility using AI.

This module analyzes Linear tickets to determine if they can be resolved automatically
by coding agents, providing scoring and recommendations for ticket prioritization.
"""
import os
import json
import re
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from src.database.models import Integration, LinearResolutionAttempt
from src.integrations.linear.integration import LinearIntegration
from sqlalchemy.orm import Session

# Cost-optimized model configuration for ticket analysis
TICKET_ANALYSIS_MODEL_CONFIG = {
    "feasibility_check": {
        "model": "deepseek/deepseek-r1-0528:free",  # Paid model for initial screening (free tier ended)
        "max_tokens": 300,
        "temperature": 0.2
    },
    "detailed_analysis": {
        "model": "x-ai/grok-code-fast-1",  # Better model for complex analysis
        "max_tokens": 800,
        "temperature": 0.3
    },
    "code_assessment": {
        "model": "x-ai/grok-code-fast-1",  # Code-focused model
        "max_tokens": 1000,
        "temperature": 0.2
    }
}

# Token limits for ticket analysis
MAX_TICKET_CONTENT_TOKENS = 4000  # Ticket title, description, comments
MAX_CONTEXT_TOKENS = 2000  # Additional context (labels, history, etc.)
MAX_TOTAL_ANALYSIS_TOKENS = 8000  # Total tokens for analysis prompt

# Scoring thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.8  # Likely resolvable
MEDIUM_CONFIDENCE_THRESHOLD = 0.5  # Maybe resolvable
LOW_CONFIDENCE_THRESHOLD = 0.2  # Unlikely resolvable


class LinearTicketAnalyzer:
    """Analyzes Linear tickets for automated resolution potential."""

    def __init__(self, integration: LinearIntegration):
        self.integration = integration
        self.api_key = os.getenv("OPENCOUNCIL_API")
        if not self.api_key:
            print("⚠️  OPENCOUNCIL_API not set, ticket analysis will be limited")

    def analyze_ticket_resolvability(
        self,
        ticket: Dict[str, Any],
        include_comments: bool = True,
        codebase_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Analyze a Linear ticket to determine if it can be resolved automatically.

        Args:
            ticket: Linear ticket data with title, description, labels, etc.
            include_comments: Whether to include comments in analysis
            codebase_context: Optional context about the codebase structure

        Returns:
            {
                "resolvable": bool,
                "confidence_score": float,  # 0.0 to 1.0
                "ticket_type": str,  # "bug_fix", "feature", "improvement", "documentation", etc.
                "complexity": str,  # "simple", "moderate", "complex", "unknown"
                "estimated_effort": str,  # "15min", "1hr", "4hrs", "1day", "unknown"
                "blockers": List[str],  # Reasons why it might not be resolvable
                "requirements": List[str],  # What would be needed to resolve
                "tags": List[str],  # Categorization tags
                "reasoning": str,  # Explanation of the decision
                "recommended_agent": Optional[str]  # Which agent type would be best
            }
        """
        if not self.api_key:
            return self._basic_resolvability_check(ticket)

        try:
            # Get detailed ticket information
            if include_comments:
                detailed_ticket = self.integration.analyze_issue_for_resolution(ticket["id"])
                ticket_with_context = detailed_ticket.get("issue", ticket)
                comments = detailed_ticket.get("comments", [])
            else:
                ticket_with_context = ticket
                comments = []

            # Perform initial feasibility check
            feasibility = self._check_initial_feasibility(ticket_with_context, comments)

            if feasibility["confidence"] < LOW_CONFIDENCE_THRESHOLD:
                return self._format_low_confidence_result(ticket_with_context, feasibility)

            # Perform detailed analysis for promising tickets
            detailed_analysis = self._perform_detailed_analysis(
                ticket_with_context,
                comments,
                codebase_context
            )

            return detailed_analysis

        except Exception as e:
            print(f"❌ Error analyzing ticket {ticket.get('identifier', ticket.get('id'))}: {e}")
            return self._fallback_analysis(ticket)

    def _check_initial_feasibility(
        self,
        ticket: Dict[str, Any],
        comments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Perform quick initial feasibility check using free model."""

        # Build context for analysis
        context = self._build_ticket_context(ticket, comments, max_tokens=2000)

        prompt = f"""Analyze this Linear ticket to determine if it could be resolved automatically by a coding agent.

{context}

Provide a quick assessment:
1. Is this likely a coding task that can be automated? (YES/NO/MAYBE)
2. What type of task is this? (bug_fix, feature, documentation, configuration, design, manual_task, unclear)
3. Confidence level (0.0 to 1.0) that this can be automated
4. One-sentence reason

Format: DECISION|TYPE|CONFIDENCE|REASON
Example: YES|bug_fix|0.8|Clear bug with specific error and code location"""

        try:
            response = self._make_ai_request(
                prompt=prompt,
                model_config=TICKET_ANALYSIS_MODEL_CONFIG["feasibility_check"]
            )

            return self._parse_feasibility_response(response)

        except Exception as e:
            print(f"⚠️  Error in initial feasibility check: {e}")
            return {"decision": "MAYBE", "type": "unclear", "confidence": 0.3, "reason": "Analysis failed"}

    def _perform_detailed_analysis(
        self,
        ticket: Dict[str, Any],
        comments: List[Dict[str, Any]],
        codebase_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Perform detailed analysis using more sophisticated model."""

        context = self._build_ticket_context(ticket, comments, max_tokens=4000)
        codebase_info = self._format_codebase_context(codebase_context) if codebase_context else ""

        prompt = f"""You are an expert software engineer analyzing a Linear ticket for automated resolution.

{context}

{codebase_info}

Analyze this ticket comprehensively:

1. **RESOLVABILITY**: Can this be resolved by an automated coding agent? Consider:
   - Is it a well-defined technical task?
   - Are requirements clear and specific?
   - Does it require human judgment, design decisions, or stakeholder input?
   - Are there external dependencies or complex integrations?

2. **TICKET TYPE**: Categorize as one of:
   - bug_fix: Clear bug with reproducible steps
   - feature: New functionality with defined requirements
   - improvement: Enhancement to existing functionality
   - documentation: Code comments, README updates, etc.
   - configuration: Config file changes, environment setup
   - testing: Adding or fixing tests
   - refactoring: Code restructuring without behavior changes
   - infrastructure: CI/CD, deployment, tooling changes
   - unclear: Requirements not clear enough

3. **COMPLEXITY ASSESSMENT**:
   - simple: Single file, <50 lines of code
   - moderate: 2-5 files, clear patterns to follow
   - complex: Many files, requires architectural understanding
   - unknown: Cannot determine from available information

4. **EFFORT ESTIMATION**:
   - 15min: Trivial changes (typos, simple config)
   - 1hr: Small bug fixes, minor features
   - 4hrs: Moderate features, multi-file changes
   - 1day: Complex features, significant refactoring
   - unknown: Cannot estimate

5. **BLOCKERS**: What might prevent automated resolution?
   - Unclear requirements
   - Missing context or documentation
   - Requires external APIs or credentials
   - Needs human design decisions
   - Complex business logic
   - Requires testing with real data
   - Security implications

6. **REQUIREMENTS**: What would be needed for successful resolution?
   - Access to specific files or documentation
   - Environment setup or credentials
   - Test data or examples
   - Clarification from stakeholders
   - Integration with external services

Provide your analysis in this JSON format:
{
    "resolvable": boolean,
    "confidence_score": float (0.0 to 1.0),
    "ticket_type": string,
    "complexity": string,
    "estimated_effort": string,
    "blockers": [list of strings],
    "requirements": [list of strings],
    "tags": [list of categorization tags],
    "reasoning": "detailed explanation",
    "recommended_agent": "coding_agent|documentation_agent|config_agent|null"
}"""

        try:
            response = self._make_ai_request(
                prompt=prompt,
                model_config=TICKET_ANALYSIS_MODEL_CONFIG["detailed_analysis"]
            )

            return self._parse_detailed_response(response, ticket)

        except Exception as e:
            print(f"⚠️  Error in detailed analysis: {e}")
            return self._fallback_analysis(ticket)

    def _build_ticket_context(
        self,
        ticket: Dict[str, Any],
        comments: List[Dict[str, Any]],
        max_tokens: int = 4000
    ) -> str:
        """Build formatted context from ticket and comments."""

        context_parts = []

        # Basic ticket info
        context_parts.append(f"**TICKET**: {ticket.get('identifier', 'Unknown')} - {ticket.get('title', 'No title')}")

        if ticket.get('description'):
            context_parts.append(f"**DESCRIPTION**:\n{ticket['description']}")

        # Labels and metadata
        labels = ticket.get('labels', [])
        if labels:
            label_names = [label.get('name', str(label)) for label in labels]
            context_parts.append(f"**LABELS**: {', '.join(label_names)}")

        priority = ticket.get('priority')
        if priority is not None:
            priority_map = {0: "Urgent", 1: "High", 2: "Medium", 3: "Low", 4: "No Priority"}
            context_parts.append(f"**PRIORITY**: {priority_map.get(priority, f'Level {priority}')}")

        state = ticket.get('state', {})
        if state:
            context_parts.append(f"**STATUS**: {state.get('name', 'Unknown')}")

        team = ticket.get('team', {})
        if team:
            context_parts.append(f"**TEAM**: {team.get('name', 'Unknown')}")

        # Add relevant comments
        if comments:
            context_parts.append("**COMMENTS**:")
            for comment in comments[:5]:  # Limit to first 5 comments
                user_name = comment.get('user', {}).get('name', 'Unknown')
                body = comment.get('body', '')[:200]  # Limit comment length
                context_parts.append(f"- {user_name}: {body}")

        # Join and truncate if needed
        full_context = "\n\n".join(context_parts)

        # Rough token estimation and truncation
        if self._estimate_tokens(full_context) > max_tokens:
            full_context = self._truncate_to_token_limit(full_context, max_tokens)

        return full_context

    def _format_codebase_context(self, codebase_context: Dict[str, Any]) -> str:
        """Format codebase context information."""
        if not codebase_context:
            return ""

        parts = ["**CODEBASE CONTEXT**:"]

        if codebase_context.get('language'):
            parts.append(f"- Language: {codebase_context['language']}")

        if codebase_context.get('framework'):
            parts.append(f"- Framework: {codebase_context['framework']}")

        if codebase_context.get('relevant_files'):
            files = codebase_context['relevant_files'][:5]  # Limit to 5 files
            parts.append(f"- Relevant files: {', '.join(files)}")

        if codebase_context.get('patterns'):
            parts.append(f"- Code patterns: {', '.join(codebase_context['patterns'][:3])}")

        return "\n".join(parts) + "\n"

    def _make_ai_request(self, prompt: str, model_config: Dict[str, Any]) -> str:
        """Make request to AI API using OpenRouter."""
        import requests

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://healops.ai",
            "X-Title": "Healops Linear Ticket Analysis"
        }

        payload = {
            "model": model_config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": model_config["max_tokens"],
            "temperature": model_config["temperature"]
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()

        result = response.json()
        if "error" in result:
            raise Exception(f"AI API error: {result['error']}")

        choices = result.get("choices", [])
        if not choices:
            raise Exception("No response from AI API")

        return choices[0]["message"]["content"]

    def _parse_feasibility_response(self, response: str) -> Dict[str, Any]:
        """Parse the initial feasibility check response."""
        try:
            # Expected format: DECISION|TYPE|CONFIDENCE|REASON
            parts = response.strip().split('|')

            if len(parts) >= 4:
                decision = parts[0].strip()
                ticket_type = parts[1].strip()
                confidence = float(parts[2].strip())
                reason = parts[3].strip()
            else:
                # Fallback parsing for malformed responses
                decision = "MAYBE"
                ticket_type = "unclear"
                confidence = 0.3
                reason = response[:100]

            return {
                "decision": decision,
                "type": ticket_type,
                "confidence": max(0.0, min(1.0, confidence)),
                "reason": reason
            }
        except Exception as e:
            print(f"⚠️  Error parsing feasibility response: {e}")
            return {
                "decision": "MAYBE",
                "type": "unclear",
                "confidence": 0.3,
                "reason": "Could not parse response"
            }

    def _parse_detailed_response(self, response: str, ticket: Dict[str, Any]) -> Dict[str, Any]:
        """Parse the detailed analysis response."""
        try:
            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                # Fallback: try to parse the entire response as JSON
                result = json.loads(response)

            # Validate and clean up the response
            return self._validate_analysis_result(result, ticket)

        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to parse JSON response: {e}")
            return self._fallback_analysis(ticket)
        except Exception as e:
            print(f"⚠️  Error parsing detailed response: {e}")
            return self._fallback_analysis(ticket)

    def _validate_analysis_result(self, result: Dict[str, Any], ticket: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean up analysis result."""

        # Ensure required fields exist with defaults
        validated = {
            "resolvable": bool(result.get("resolvable", False)),
            "confidence_score": max(0.0, min(1.0, float(result.get("confidence_score", 0.3)))),
            "ticket_type": result.get("ticket_type", "unclear"),
            "complexity": result.get("complexity", "unknown"),
            "estimated_effort": result.get("estimated_effort", "unknown"),
            "blockers": result.get("blockers", [])[:10],  # Limit to 10 blockers
            "requirements": result.get("requirements", [])[:10],  # Limit to 10 requirements
            "tags": result.get("tags", [])[:15],  # Limit to 15 tags
            "reasoning": result.get("reasoning", "Analysis completed")[:1000],  # Limit length
            "recommended_agent": result.get("recommended_agent"),
            "analyzed_at": datetime.utcnow().isoformat(),
            "ticket_id": ticket.get("id"),
            "ticket_identifier": ticket.get("identifier")
        }

        # Validate enums
        valid_ticket_types = [
            "bug_fix", "feature", "improvement", "documentation",
            "configuration", "testing", "refactoring", "infrastructure", "unclear"
        ]
        if validated["ticket_type"] not in valid_ticket_types:
            validated["ticket_type"] = "unclear"

        valid_complexity = ["simple", "moderate", "complex", "unknown"]
        if validated["complexity"] not in valid_complexity:
            validated["complexity"] = "unknown"

        valid_efforts = ["15min", "1hr", "4hrs", "1day", "unknown"]
        if validated["estimated_effort"] not in valid_efforts:
            validated["estimated_effort"] = "unknown"

        return validated

    def _basic_resolvability_check(self, ticket: Dict[str, Any]) -> Dict[str, Any]:
        """Perform basic rule-based resolvability check without AI."""

        title = ticket.get("title", "").lower()
        description = ticket.get("description", "").lower()
        labels = [label.get("name", "").lower() for label in ticket.get("labels", [])]

        # Rule-based scoring
        score = 0.3  # Base score
        ticket_type = "unclear"
        blockers = []
        requirements = []

        # Positive indicators
        if any(keyword in title + description for keyword in [
            "bug", "fix", "error", "broken", "issue", "problem"
        ]):
            score += 0.2
            ticket_type = "bug_fix"

        if any(keyword in title + description for keyword in [
            "implement", "add", "create", "new feature"
        ]):
            score += 0.15
            ticket_type = "feature"

        if any(keyword in title + description for keyword in [
            "update", "improve", "enhance", "optimize"
        ]):
            score += 0.1
            ticket_type = "improvement"

        if any(keyword in title + description for keyword in [
            "documentation", "readme", "comment", "docs"
        ]):
            score += 0.15
            ticket_type = "documentation"

        # Negative indicators
        if any(keyword in title + description + " ".join(labels) for keyword in [
            "design", "ui", "ux", "mockup", "prototype", "research"
        ]):
            score -= 0.3
            blockers.append("Requires design or UX decisions")

        if any(keyword in title + description for keyword in [
            "discuss", "meeting", "clarify", "unclear", "tbd", "investigate"
        ]):
            score -= 0.2
            blockers.append("Requirements not clear")

        if any(label in labels for label in [
            "blocked", "waiting", "needs-design", "needs-approval"
        ]):
            score -= 0.25
            blockers.append("Ticket is blocked or waiting")

        score = max(0.0, min(1.0, score))

        return {
            "resolvable": score >= MEDIUM_CONFIDENCE_THRESHOLD,
            "confidence_score": score,
            "ticket_type": ticket_type,
            "complexity": "unknown",
            "estimated_effort": "unknown",
            "blockers": blockers,
            "requirements": requirements,
            "tags": ["rule-based-analysis"],
            "reasoning": f"Basic rule-based analysis. Score: {score:.2f}",
            "recommended_agent": "coding_agent" if score >= MEDIUM_CONFIDENCE_THRESHOLD else None,
            "analyzed_at": datetime.utcnow().isoformat(),
            "ticket_id": ticket.get("id"),
            "ticket_identifier": ticket.get("identifier")
        }

    def _format_low_confidence_result(
        self,
        ticket: Dict[str, Any],
        feasibility: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format result for low-confidence tickets."""
        return {
            "resolvable": False,
            "confidence_score": feasibility["confidence"],
            "ticket_type": feasibility["type"],
            "complexity": "unknown",
            "estimated_effort": "unknown",
            "blockers": [feasibility["reason"]],
            "requirements": ["Clearer requirements needed"],
            "tags": ["low-confidence", "needs-review"],
            "reasoning": f"Initial screening suggests low automation potential: {feasibility['reason']}",
            "recommended_agent": None,
            "analyzed_at": datetime.utcnow().isoformat(),
            "ticket_id": ticket.get("id"),
            "ticket_identifier": ticket.get("identifier")
        }

    def _fallback_analysis(self, ticket: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback analysis when AI analysis fails."""
        return {
            "resolvable": False,
            "confidence_score": 0.2,
            "ticket_type": "unclear",
            "complexity": "unknown",
            "estimated_effort": "unknown",
            "blockers": ["Analysis failed - manual review needed"],
            "requirements": ["Manual analysis required"],
            "tags": ["analysis-failed"],
            "reasoning": "Automated analysis could not be completed",
            "recommended_agent": None,
            "analyzed_at": datetime.utcnow().isoformat(),
            "ticket_id": ticket.get("id"),
            "ticket_identifier": ticket.get("identifier")
        }

    def _estimate_tokens(self, text: str) -> int:
        """Rough estimation of token count."""
        return len(text) // 4  # Approximate 4 characters per token

    def _truncate_to_token_limit(self, text: str, token_limit: int) -> str:
        """Truncate text to approximate token limit."""
        char_limit = token_limit * 4  # Approximate 4 characters per token
        if len(text) <= char_limit:
            return text

        truncated = text[:char_limit]
        # Try to truncate at word boundary
        last_space = truncated.rfind(' ')
        if last_space > char_limit * 0.8:  # If we can truncate reasonably close to limit
            truncated = truncated[:last_space]

        return truncated + "...[truncated]"


def analyze_tickets_for_resolution(
    integration_id: int,
    db: Session,
    ticket_filters: Optional[Dict[str, Any]] = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Analyze multiple Linear tickets for resolution potential.

    Args:
        integration_id: Linear integration ID
        db: Database session
        ticket_filters: Optional filters for ticket selection
        limit: Maximum number of tickets to analyze

    Returns:
        List of analyzed tickets sorted by confidence score
    """
    try:
        # Get Linear integration
        integration = db.query(Integration).filter(
            Integration.id == integration_id,
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        ).first()

        if not integration:
            print(f"❌ No active Linear integration found with ID {integration_id}")
            return []

        # Initialize Linear client
        linear = LinearIntegration(integration_id=integration_id)

        # Get tickets from Linear
        filters = ticket_filters or {}
        tickets_response = linear.get_open_resolvable_issues(
            team_ids=filters.get("team_ids"),
            exclude_labels=filters.get("exclude_labels", ["manual-only", "design", "blocked"]),
            max_priority=filters.get("max_priority", 2)  # Only high/medium priority
        )

        if not tickets_response:
            print("ℹ️  No tickets found matching criteria")
            return []

        # Initialize analyzer
        analyzer = LinearTicketAnalyzer(linear)

        # Analyze tickets with error handling
        analyzed_tickets = []
        for i, ticket in enumerate(tickets_response[:limit]):
            ticket_id = ticket.get('identifier', ticket.get('id', 'Unknown'))
            print(f"🔍 Analyzing ticket {i+1}/{min(len(tickets_response), limit)}: {ticket_id}")

            try:
                analysis = analyzer.analyze_ticket_resolvability(
                    ticket=ticket,
                    include_comments=True
                )

                analyzed_tickets.append({
                    **ticket,
                    "analysis": analysis
                })

            except Exception as e:
                print(f"⚠️  Error analyzing ticket {ticket_id}: {e}")

                # Create fallback analysis to preserve the ticket
                fallback_analysis = {
                    "resolvable": False,
                    "confidence_score": 0.0,
                    "ticket_type": "unclear",
                    "complexity": "unknown",
                    "estimated_effort": "unknown",
                    "blockers": [f"Analysis failed: {str(e)[:100]}"],
                    "requirements": ["Manual analysis required"],
                    "tags": ["analysis-error", "needs-manual-review"],
                    "reasoning": f"Automated analysis failed: {str(e)[:200]}",
                    "recommended_agent": None,
                    "analyzed_at": datetime.utcnow().isoformat(),
                    "ticket_id": ticket.get("id"),
                    "ticket_identifier": ticket.get("identifier"),
                    "error": str(e)
                }

                analyzed_tickets.append({
                    **ticket,
                    "analysis": fallback_analysis
                })

                # Continue with other tickets instead of failing entirely

        # Sort by confidence score (highest first)
        analyzed_tickets.sort(
            key=lambda x: x["analysis"]["confidence_score"],
            reverse=True
        )

        print(f"✅ Analyzed {len(analyzed_tickets)} tickets")
        return analyzed_tickets

    except Exception as e:
        print(f"❌ Error analyzing tickets: {e}")
        import traceback
        traceback.print_exc()
        return []