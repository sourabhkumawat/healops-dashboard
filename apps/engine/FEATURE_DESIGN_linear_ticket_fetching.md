# Linear Ticket Fetching API Design

## Overview
Extend the existing LinearIntegration class with methods to fetch and filter tickets from Linear boards, enabling automatic ticket resolution by coding agents.

## New LinearIntegration Methods

### 1. Core Ticket Fetching

```python
def get_issues(
    self,
    team_id: Optional[str] = None,
    status: Optional[str] = None,
    labels: Optional[List[str]] = None,
    assignee: Optional[str] = None,
    page_size: int = 50,
    cursor: Optional[str] = None,
    include_completed: bool = False
) -> Dict[str, Any]:
    """
    Fetch issues from Linear with filtering options.

    Args:
        team_id: Filter by specific team (default: all teams user has access to)
        status: Filter by workflow state name (e.g., "Todo", "In Progress")
        labels: Filter by label names
        assignee: Filter by assignee ID or "unassigned"
        page_size: Number of issues per page (1-250)
        cursor: Pagination cursor for next page
        include_completed: Include completed/canceled issues

    Returns:
        {
            "issues": [
                {
                    "id": "issue-uuid",
                    "identifier": "ID-123",
                    "title": "Issue title",
                    "description": "Issue description",
                    "url": "https://linear.app/...",
                    "state": {"id": "state-id", "name": "Todo", "type": "triage"},
                    "labels": [{"name": "bug", "color": "#ff0000"}],
                    "assignee": {"id": "user-id", "name": "John Doe"} | null,
                    "team": {"id": "team-id", "name": "Engineering", "key": "ENG"},
                    "priority": 1,
                    "estimate": 3,
                    "createdAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-01T00:00:00Z"
                }
            ],
            "pageInfo": {
                "hasNextPage": true,
                "endCursor": "cursor-string"
            }
        }
    """

def get_open_resolvable_issues(
    self,
    team_ids: Optional[List[str]] = None,
    exclude_labels: Optional[List[str]] = None,
    max_priority: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Get open issues that might be resolvable by coding agents.

    Filters for:
    - Status: Todo, Backlog, Triage (not In Progress, Done, Canceled)
    - No current assignee OR assignee is a bot/automation user
    - Excludes issues with certain labels (e.g., "manual-only", "design")
    - Optional priority filtering

    Args:
        team_ids: List of team IDs to search (default: all teams)
        exclude_labels: Labels to exclude from results
        max_priority: Maximum priority level (0=urgent, 4=no priority)

    Returns:
        List of issues sorted by priority and creation date
    """

def analyze_issue_for_resolution(self, issue_id: str) -> Dict[str, Any]:
    """
    Get detailed issue information for resolution analysis.

    Returns:
        {
            "issue": {/* full issue details */},
            "comments": [
                {
                    "id": "comment-id",
                    "body": "Comment text",
                    "user": {"name": "John Doe"},
                    "createdAt": "2024-01-01T00:00:00Z"
                }
            ],
            "attachments": [
                {"name": "file.png", "url": "https://..."}
            ],
            "relations": [
                {"type": "blocks", "issue": {"identifier": "ID-124"}}
            ]
        }
    """

def search_issues(
    self,
    query: str,
    team_ids: Optional[List[str]] = None,
    include_archived: bool = False
) -> List[Dict[str, Any]]:
    """
    Search issues by text query.

    Args:
        query: Search query (searches title, description, comments)
        team_ids: Limit to specific teams
        include_archived: Include archived issues

    Returns:
        List of matching issues with relevance scoring
    """
```

### 2. Resolution Tracking Methods

```python
def claim_issue_for_resolution(
    self,
    issue_id: str,
    agent_name: str,
    estimated_duration: Optional[str] = None
) -> Dict[str, Any]:
    """
    Claim an issue for automatic resolution.

    - Updates assignee to bot user
    - Sets status to "In Progress"
    - Adds comment about automated resolution attempt

    Returns:
        Updated issue data
    """

def update_resolution_progress(
    self,
    issue_id: str,
    progress_message: str,
    stage: str  # "analyzing", "implementing", "testing", "completed", "failed"
) -> Dict[str, Any]:
    """
    Add progress update comment to issue.
    """

def complete_issue_resolution(
    self,
    issue_id: str,
    resolution_summary: str,
    code_changes: Optional[Dict[str, Any]] = None,
    test_results: Optional[str] = None
) -> Dict[str, Any]:
    """
    Mark issue as resolved by automation.

    - Updates status to "Done"
    - Adds resolution comment with:
      - Summary of changes made
      - Links to PRs/commits
      - Test results
      - Time taken
    """

def fail_issue_resolution(
    self,
    issue_id: str,
    failure_reason: str,
    suggestions: Optional[str] = None
) -> Dict[str, Any]:
    """
    Mark automated resolution as failed.

    - Resets assignee to unassigned
    - Adds comment explaining failure
    - Sets status back to original state
    """
```

## New API Endpoints

### 1. Ticket Discovery Endpoints

```
GET /integrations/linear/{integration_id}/issues
Query Parameters:
  - team_id: string (optional)
  - status: string (optional)
  - labels: comma-separated strings (optional)
  - assignee: string (optional)
  - page_size: integer (default: 50, max: 250)
  - cursor: string (optional)
  - include_completed: boolean (default: false)

Response: Paginated list of issues with filtering applied
```

```
GET /integrations/linear/{integration_id}/resolvable-issues
Query Parameters:
  - team_ids: comma-separated team IDs (optional)
  - exclude_labels: comma-separated labels (optional)
  - max_priority: integer 0-4 (optional)
  - limit: integer (default: 100, max: 500)

Response: List of issues eligible for automatic resolution
```

```
GET /integrations/linear/{integration_id}/issues/{issue_id}/details
Response: Full issue details including comments, attachments, relations
```

### 2. Resolution Management Endpoints

```
POST /integrations/linear/{integration_id}/issues/{issue_id}/claim
Body: {
  "agent_name": "coding-agent-v1",
  "estimated_duration": "30 minutes"
}
Response: Updated issue with claimed status
```

```
POST /integrations/linear/{integration_id}/issues/{issue_id}/progress
Body: {
  "stage": "implementing",
  "message": "Generated code changes for user authentication bug"
}
Response: Comment added confirmation
```

```
POST /integrations/linear/{integration_id}/issues/{issue_id}/complete
Body: {
  "resolution_summary": "Fixed authentication bug by...",
  "code_changes": {
    "files_modified": ["auth.py", "tests/test_auth.py"],
    "pr_url": "https://github.com/...",
    "commit_hash": "abc123"
  },
  "test_results": "All tests passing (15/15)"
}
Response: Issue marked as completed
```

```
POST /integrations/linear/{integration_id}/issues/{issue_id}/fail
Body: {
  "failure_reason": "Unable to reproduce the bug in current codebase",
  "suggestions": "May need manual investigation of environment-specific issues"
}
Response: Issue returned to original state
```

### 3. Search and Analytics

```
GET /integrations/linear/{integration_id}/search
Query Parameters:
  - q: search query (required)
  - team_ids: comma-separated team IDs (optional)
  - include_archived: boolean (default: false)

Response: Search results with relevance scores
```

```
GET /integrations/linear/{integration_id}/resolution-analytics
Response: Statistics about automated resolutions
{
  "total_attempted": 150,
  "successful": 120,
  "failed": 30,
  "success_rate": 0.8,
  "avg_resolution_time": "45 minutes",
  "top_failure_reasons": [...]
}
```

## Database Extensions

### 1. New Tables

```sql
-- Track automated resolution attempts
CREATE TABLE linear_resolution_attempts (
    id SERIAL PRIMARY KEY,
    integration_id INTEGER REFERENCES integrations(id),
    issue_id VARCHAR NOT NULL, -- Linear issue UUID
    issue_identifier VARCHAR NOT NULL, -- e.g., "ID-123"
    agent_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL, -- 'claimed', 'analyzing', 'implementing', 'testing', 'completed', 'failed'
    claimed_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    resolution_summary TEXT,
    failure_reason TEXT,
    metadata JSONB, -- Store PR URLs, commit hashes, etc.
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_linear_resolution_attempts_integration_id ON linear_resolution_attempts(integration_id);
CREATE INDEX idx_linear_resolution_attempts_status ON linear_resolution_attempts(status);
```

### 2. Configuration Extensions

```python
# Add to Integration.config JSON field:
{
    "linear_auto_resolution": {
        "enabled": true,
        "allowed_teams": ["team-id-1", "team-id-2"],
        "excluded_labels": ["manual-only", "design", "blocked"],
        "max_priority": 2, // Only attempt priority 0-2 issues
        "polling_interval": 300, // seconds
        "max_concurrent_resolutions": 3,
        "require_approval": false
    }
}
```

## Error Handling & Rate Limiting

### 1. API Rate Limits
- Linear API: 1000 requests/hour per integration
- Implement exponential backoff for 429 responses
- Cache frequently accessed data (teams, workflow states)

### 2. Error Responses
```json
{
    "error": "linear_api_error",
    "message": "Rate limit exceeded",
    "details": {
        "reset_at": "2024-01-01T15:00:00Z",
        "retry_after": 300
    }
}
```

### 3. Validation
- Verify integration is active and tokens are valid
- Validate team IDs exist and user has access
- Check issue exists and is in correct state for operations

## Security Considerations

### 1. Authorization
- All endpoints require valid user session
- Verify user owns the integration_id
- Respect Linear's team/project permissions

### 2. Input Validation
- Sanitize search queries
- Validate issue IDs are UUIDs
- Limit pagination parameters

### 3. Audit Trail
- Log all automated resolution attempts
- Track which agent performed each action
- Maintain history of status changes

## Implementation Phases

### Phase 1: Core Fetching (Tasks #2, #8)
- Add GraphQL queries for issue fetching
- Implement filtering and pagination
- Create basic API endpoints

### Phase 2: Resolution Management (Tasks #4, #5, #6)
- Add issue claiming and progress tracking
- Implement coding agent integration
- Create workflow management

### Phase 3: Safety & Monitoring (Tasks #7, #10, #11)
- Add approval mechanisms
- Implement comprehensive testing
- Create monitoring and analytics

This design leverages the existing Linear integration architecture while adding the new ticket discovery and resolution capabilities in a clean, extensible way.