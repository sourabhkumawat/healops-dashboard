# Linear Ticket Resolution Feature

This feature enables automatic resolution of Linear tickets using your existing coding agent infrastructure. The system polls Linear boards for open tickets, analyzes them for resolvability, and attempts to resolve them automatically using AI-powered coding agents.

## 🎯 Key Features

- **Automatic Ticket Discovery**: Polls Linear boards for open, unassigned tickets
- **AI-Powered Analysis**: Determines if tickets can be resolved automatically
- **Reuses Existing Agents**: Leverages your proven 11-agent system for resolution
- **Status Synchronization**: Updates Linear tickets with progress and results
- **Comprehensive Monitoring**: Track success rates and resolution analytics
- **Safety Mechanisms**: Human approval workflows and error handling

## 🏗️ Architecture Overview

### Components

1. **LinearTicketAnalyzer**: AI-powered ticket analysis for resolvability scoring
2. **LinearTicketResolver**: Converts tickets to pseudo-incidents for agent processing
3. **LinearPollingService**: Background service for automated ticket discovery
4. **LinearTicketController**: REST API for configuration and monitoring

### Flow

```
Linear Board → Ticket Discovery → AI Analysis → Agent Resolution → Status Update
```

## 🚀 Quick Start

### 1. Setup

```bash
# Run the setup script
python scripts/setup_linear_ticket_resolution.py

# Or manually run migration
python migrations/add_linear_resolution_attempts.py
```

### 2. Configure Linear Integration

Enable auto-resolution for your Linear integration:

```bash
curl -X PUT "http://localhost:8000/linear-tickets/integrations/{integration_id}/config" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "allowed_teams": ["team-id-1", "team-id-2"],
    "excluded_labels": ["manual-only", "design", "blocked"],
    "max_priority": 2,
    "confidence_threshold": 0.5,
    "max_concurrent_resolutions": 3
  }'
```

### 3. Start Polling Service

```bash
# Start the background polling service
python -m src.services.linear_polling_service

# Or control via API
curl -X POST "http://localhost:8000/linear-tickets/polling-service/start"
```

## 📡 API Endpoints

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/linear-tickets/integrations/{id}/config` | Get auto-resolution config |
| PUT | `/linear-tickets/integrations/{id}/config` | Update auto-resolution config |
| GET | `/linear-tickets/integrations/{id}/teams` | List available teams |

### Ticket Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/linear-tickets/integrations/{id}/resolvable-tickets` | Get tickets eligible for resolution |
| POST | `/linear-tickets/integrations/{id}/analyze-ticket` | Analyze a specific ticket |
| POST | `/linear-tickets/integrations/{id}/resolve-ticket` | Manually trigger ticket resolution |

### Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/linear-tickets/integrations/{id}/resolution-attempts` | List resolution attempts |
| GET | `/linear-tickets/integrations/{id}/analytics` | Get resolution analytics |
| GET | `/linear-tickets/health` | Health check |

### Polling Service Control

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/linear-tickets/polling-service/start` | Start polling service |
| POST | `/linear-tickets/polling-service/stop` | Stop polling service |
| GET | `/linear-tickets/polling-service/status` | Get service status |

## ⚙️ Configuration

### Auto-Resolution Settings

```json
{
  "enabled": true,
  "allowed_teams": ["team-id-1", "team-id-2"],
  "excluded_labels": ["manual-only", "design", "blocked"],
  "max_priority": 2,
  "confidence_threshold": 0.5,
  "max_concurrent_resolutions": 3,
  "require_approval": false
}
```

### Environment Variables

```bash
# Polling configuration
LINEAR_POLLING_INTERVAL_MINUTES=15
LINEAR_MAX_TICKETS_PER_INTEGRATION=5
LINEAR_MAX_CONCURRENT_INTEGRATIONS=3
LINEAR_MAX_CONSECUTIVE_ERRORS=5
LINEAR_ERROR_BACKOFF_MINUTES=30

# Email alerts
LINEAR_ENABLE_EMAIL_ALERTS=true
LINEAR_ADMIN_EMAIL=admin@yourcompany.com

# Service control
LINEAR_SHUTDOWN_TIMEOUT_SECONDS=300
LINEAR_HEALTH_CHECK_INTERVAL_MINUTES=60
```

## 🤖 AI Analysis

The system uses a two-stage analysis process:

### Stage 1: Quick Feasibility Check
- Uses fast, free model for initial screening
- Filters out obviously non-automatable tickets
- Returns basic categorization and confidence

### Stage 2: Detailed Analysis
- Uses advanced code-focused model
- Provides comprehensive resolvability assessment
- Includes complexity, effort estimation, and requirements analysis

### Ticket Scoring

- **Confidence Score**: 0.0 to 1.0 likelihood of successful automation
- **Ticket Types**: bug_fix, feature, improvement, documentation, etc.
- **Complexity**: simple, moderate, complex, unknown
- **Effort**: 15min, 1hr, 4hrs, 1day, unknown

## 🔄 Agent Integration

The system reuses your existing agent orchestrator (`run_robust_crew()`) by:

1. **Converting tickets to pseudo-incidents** with comprehensive context
2. **Invoking the existing 11-agent system** (Explorer, Fixer, Validator, etc.)
3. **Processing results** and updating Linear tickets with resolution details

### Supported Agent Operations

- ✅ Code exploration and analysis
- ✅ Bug fixes and feature implementation
- ✅ Testing and validation
- ✅ PR creation and review
- ✅ Error handling and rollback

## 📊 Monitoring & Analytics

### Resolution Attempts Tracking

Each resolution attempt is tracked with:

- Ticket information and analysis results
- Agent execution details and timeline
- Success/failure status and reasons
- Generated PRs and code changes

### Analytics Dashboard

```bash
curl -X GET "http://localhost:8000/linear-tickets/integrations/{id}/analytics?days=30"
```

Provides:
- Success rates by ticket type
- Average resolution times
- Top failure reasons
- Trend analysis

## 🛡️ Safety Mechanisms

### Automatic Safeguards

- **Rate limiting**: Max concurrent resolutions per integration
- **Confidence thresholds**: Only attempt high-confidence tickets
- **Label filtering**: Exclude tickets marked as manual-only
- **Team restrictions**: Limit to specific teams/projects
- **Retry limits**: Prevent infinite retry loops

### Human Oversight

- **Approval workflows**: Optional human approval before resolution
- **Progress monitoring**: Real-time updates via WebSocket
- **Manual intervention**: Ability to stop/modify ongoing resolutions
- **Audit trails**: Complete history of all actions taken

## 🔧 Troubleshooting

### Common Issues

1. **No tickets found**
   - Check team permissions in Linear
   - Verify ticket filters and labels
   - Ensure tickets are unassigned

2. **Low confidence scores**
   - Review ticket descriptions for clarity
   - Add more context or requirements
   - Check if tickets require design decisions

3. **Resolution failures**
   - Check agent logs for specific errors
   - Verify codebase access and permissions
   - Review repository structure and dependencies

4. **Linear API errors**
   - Verify OAuth token validity
   - Check API rate limits
   - Ensure workspace permissions

### Debugging

```bash
# Check service status
curl -X GET "http://localhost:8000/linear-tickets/polling-service/status"

# View recent attempts
curl -X GET "http://localhost:8000/linear-tickets/integrations/{id}/resolution-attempts?limit=10"

# Test ticket analysis
curl -X POST "http://localhost:8000/linear-tickets/integrations/{id}/analyze-ticket" \
  -d '{"ticket_id": "issue-uuid"}'

# Manual health check
curl -X GET "http://localhost:8000/linear-tickets/health"
```

### Log Locations

- **Polling Service**: Console output when running standalone
- **Agent Execution**: Agent event logs in database
- **API Requests**: FastAPI access logs
- **Linear API**: Integration logs in console

## 📈 Performance Optimization

### Tuning Parameters

- **Polling Interval**: Balance between responsiveness and API usage
- **Batch Size**: Number of tickets processed per cycle
- **Confidence Threshold**: Higher values reduce false positives
- **Concurrent Limits**: Prevent overwhelming the system

### Monitoring Metrics

- API request rates and response times
- Agent execution durations
- Success/failure ratios
- Linear API quota usage

## 🔐 Security Considerations

- **Authentication**: All API endpoints require valid user sessions
- **Authorization**: Users can only access their own integrations
- **Token Management**: Linear OAuth tokens are encrypted at rest
- **Audit Logging**: All resolution attempts are logged with metadata
- **Sandboxing**: Agent execution in isolated environments

## 🚀 Future Enhancements

- **Learning System**: Improve analysis based on historical success rates
- **Custom Prompts**: User-defined analysis and resolution prompts
- **Batch Processing**: Handle multiple related tickets together
- **Integration Webhooks**: Real-time ticket notifications from Linear
- **Advanced Filtering**: ML-based ticket categorization and routing

## 📞 Support

For issues and questions:

1. Check the troubleshooting section above
2. Review API endpoint responses for error details
3. Check service status and health endpoints
4. Review agent execution logs for detailed error information

## 🔄 Migration Notes

When upgrading:

1. **Database**: Run migration scripts for schema changes
2. **Configuration**: Review new environment variables
3. **API Changes**: Check for endpoint modifications
4. **Dependencies**: Update required packages

---

This feature seamlessly integrates with your existing Healops infrastructure while providing powerful automated ticket resolution capabilities. The system is designed to be safe, monitored, and easily configurable to match your team's workflow.