# Redpanda Integration for Healops

This document explains how to set up and use Redpanda for sequential log processing in your Healops system. Redpanda replaces Redis pub/sub and Celery background tasks with a more reliable, persistent streaming solution.

## What Changed

### Before (Redis + Celery)
```
Logs → /ingest/logs → Redis pub/sub → WebSocket broadcast
                  → Database → Celery tasks → Incident processing
```

### After (Redpanda)
```
Logs → /ingest/logs → Redpanda topic → WebSocket broadcast + Sequential processing
                  → Database → Sequential incident processing via consumer groups
```

## Benefits

- **Persistence**: Unlike Redis pub/sub, logs are persisted in Redpanda
- **Sequential Processing**: Guarantees logs are processed one by one
- **Better Reliability**: No lost messages during restarts
- **Scalability**: Can add multiple consumer instances for horizontal scaling
- **Monitoring**: Built-in web UI for monitoring topics and messages

## Prerequisites

- Docker and Docker Compose
- Python packages (automatically added to requirements.txt):
  - `kafka-python`

## Setup Instructions

### 1. Start Redpanda

```bash
# Create network (if it doesn't exist)
docker network create healops-network

# Start Redpanda and Console
docker-compose -f docker-compose.redpanda.yml up -d
```

This will start:
- **Redpanda** on `localhost:19092` (Kafka API)
- **Redpanda Console** on `localhost:8081` (Web UI)

### 2. Create Topics

```bash
# Make setup script executable
chmod +x scripts/setup_redpanda_topics.py

# Install kafka-python if not already installed
pip install kafka-python

# Run setup script
python scripts/setup_redpanda_topics.py
```

This creates two topics:
- `healops-logs` (3 partitions, 7-day retention)
- `healops-incidents` (2 partitions, 14-day retention)

### 3. Start Healops Engine

```bash
cd apps/engine
python main.py
```

You should see:
```
✓ Redpanda producer connected to localhost:19092
✓ Redpanda consumer connected to topic 'healops-logs' with group 'healops-websocket-broadcaster'
✓ Redpanda consumer connected to topic 'healops-incidents' with group 'healops-incident-processor'
✓ Redpanda WebSocket manager initialized
✓ Redpanda task processor initialized
```

## Configuration

Environment variables in `.env`:

```bash
# Redpanda Configuration
REDPANDA_BROKERS=localhost:19092
REDPANDA_LOG_TOPIC=healops-logs
REDPANDA_INCIDENT_TOPIC=healops-incidents
```

## How It Works

### Log Processing Flow

1. **Log Ingestion**: API receives logs via `/ingest/logs`, `/ingest/logs/batch`, or `/otel/errors`

2. **Redpanda Publishing**: Logs are published to `healops-logs` topic
   - Key: `service_name` (for consistent partitioning)
   - Value: Log data with metadata

3. **WebSocket Broadcasting**:
   - Consumer group `healops-websocket-broadcaster` reads from `healops-logs`
   - Broadcasts to all connected WebSocket clients in real-time

4. **Database Persistence**: ERROR/CRITICAL logs are saved to PostgreSQL

5. **Incident Processing**:
   - Task published to `healops-incidents` topic
   - Consumer group `healops-incident-processor` processes sequentially
   - Creates/updates incidents and triggers AI analysis

### Consumer Groups

- **`healops-websocket-broadcaster`**: Handles real-time WebSocket broadcasting
- **`healops-incident-processor`**: Sequential incident detection and processing

### Message Format

**Log Messages** (`healops-logs` topic):
```json
{
  "service_name": "web-server",
  "severity": "ERROR",
  "message": "Database connection failed",
  "source": "github",
  "timestamp": "2025-01-23T10:30:00Z",
  "metadata": { "stack_trace": "..." },
  "redpanda_timestamp": "2025-01-23T10:30:00.123Z"
}
```

**Incident Tasks** (`healops-incidents` topic):
```json
{
  "task_type": "process_log_entry",
  "log_id": 12345,
  "created_at": "2025-01-23T10:30:00Z",
  "redpanda_timestamp": "2025-01-23T10:30:00.123Z"
}
```

## Monitoring

### Redpanda Console (Web UI)

Visit `http://localhost:8081` to monitor:
- Topic health and message counts
- Consumer group lag and status
- Message content and metadata
- Partition distribution

### Health Checks

The application provides health checks:

```python
from src.services.redpanda_service import redpanda_service

# Check if Redpanda is healthy
is_healthy = redpanda_service.is_healthy()

# Get detailed health info
from src.utils.redpanda_websocket_manager import manager
health_info = manager.health_check()
```

### Logs

Monitor application logs for:
```
✓ Redpanda producer connected to localhost:19092
✓ Log published to topic healops-logs, partition 0, offset 1234
✓ Processing critical log: Database connection failed
✓ Created incident: 456
```

## Troubleshooting

### Redpanda Not Starting

```bash
# Check container status
docker-compose -f docker-compose.redpanda.yml ps

# View logs
docker-compose -f docker-compose.redpanda.yml logs redpanda

# Restart services
docker-compose -f docker-compose.redpanda.yml restart
```

### Connection Issues

1. **Check broker connectivity**:
```bash
python -c "from kafka import KafkaProducer; KafkaProducer(bootstrap_servers=['localhost:19092']).close(); print('✓ Connected')"
```

2. **Verify topics exist**:
```bash
python scripts/setup_redpanda_topics.py
```

3. **Check environment variables**:
```bash
echo $REDPANDA_BROKERS
```

### Consumer Lag

If logs aren't being processed:

1. **Check consumer group status** in Redpanda Console
2. **Monitor application logs** for consumer errors
3. **Restart the application** to reconnect consumers

### Fallback Behavior

If Redpanda is unavailable, the system falls back to:
- Direct WebSocket broadcasting (no persistence)
- Direct task execution (not recommended for production)

## Production Considerations

### Scaling

1. **Multiple Partitions**: Increase partitions for better throughput
2. **Multiple Consumers**: Run multiple Healops instances for horizontal scaling
3. **Resource Limits**: Configure Redpanda memory and disk limits

### Retention

- **healops-logs**: 7 days (configurable)
- **healops-incidents**: 14 days (configurable)

Adjust retention based on your storage and compliance requirements.

### Security

For production, consider:
- SASL/SCRAM authentication
- TLS encryption
- Network segmentation
- Access control lists (ACLs)

### Monitoring

Set up monitoring for:
- Broker health and disk usage
- Consumer lag and throughput
- Topic partition distribution
- Error rates and failed messages

## Reverting to Redis (if needed)

To revert back to Redis pub/sub:

1. **Update imports in `main.py`**:
```python
# Change this line:
from src.utils.redpanda_websocket_manager import connection_manager as manager

# Back to:
from src.utils.websocket_managers import connection_manager as manager
```

2. **Update logs controller imports**:
```python
# In logs_controller.py, change:
from src.utils.redpanda_websocket_manager import connection_manager as manager

# Back to:
from src.utils.websocket_managers import connection_manager as manager
```

3. **Revert task processing**:
```python
# Replace Redpanda task calls with:
from tasks import process_log_entry
background_tasks.add_task(process_log_entry, log_id)
```

## Next Steps

1. **Monitor Performance**: Check message throughput and consumer lag
2. **Tune Configuration**: Adjust partition counts and retention periods
3. **Set Up Alerts**: Monitor Redpanda health and consumer group status
4. **Scale Horizontally**: Add more consumer instances if needed

---

For questions or issues, check the Healops logs and Redpanda Console for debugging information.