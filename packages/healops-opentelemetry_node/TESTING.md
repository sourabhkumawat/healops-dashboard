# Testing the HealOps OpenTelemetry Package Locally

This guide explains how to test the HealOps OpenTelemetry package to verify it correctly captures error spans and stores them in the database.

## Prerequisites

1. **Backend Server Running**
   ```bash
   cd apps/engine
   uvicorn main:app --reload
   ```

2. **Generate an API Key**
   - Start the backend server
   - Use the API or dashboard to generate an API key
   - Save the API key for testing

3. **Build the Package**
   ```bash
   cd packages/healops-opentelemetry_node
   npm install
   npm run build
   ```

## Running the Test

### Method 1: Using npm script

```bash
# Set your API key as an environment variable
export HEALOPS_API_KEY="your_api_key_here"

# Run the test
npm run test:local
```

### Method 2: Direct execution

```bash
# Set environment variables
export HEALOPS_API_KEY="your_api_key_here"
export HEALOPS_BACKEND_URL="http://localhost:8000"

# Run the test script
node test-local.js
```

## What the Test Does

The test script performs the following checks:

### 1. **Backend Health Check**
   - Verifies the backend is running and accessible

### 2. **Test Cases**
   The script creates 6 different test scenarios:

   - **Test 1: Error Status Code** - Creates a span with `SpanStatusCode.ERROR`
   - **Test 2: Exception Event** - Creates a span with an exception event
   - **Test 3: Exception Attributes** - Creates a span with exception attributes
   - **Test 4: Normal Span** - Creates a normal span (should be filtered out)
   - **Test 5: Nested Error Spans** - Creates parent-child error spans
   - **Test 6: Database Error** - Simulates a database operation error

### 3. **Export Verification**
   - Waits for the BatchSpanProcessor to export spans (6 seconds)
   - Queries the backend database to verify data was stored

### 4. **Database Verification**
   - Queries the `/logs` endpoint to check stored data
   - Displays sample log entries

## Expected Output

### Successful Test Run

```
🧪 Starting HealOps OpenTelemetry Package Tests

Configuration:
  Service Name: test-service
  Backend URL: http://localhost:8000
  API Key: healops_test_your_a...

✅ Backend is healthy: { status: 'online', service: 'engine' }

🔬 Running test cases...

📝 Test 1: Error span with ERROR status code
✅ Error span created with ERROR status

📝 Test 2: Span with exception event
✅ Span created with exception event

📝 Test 3: Span with exception attributes
✅ Span created with exception attributes

📝 Test 4: Normal span (should be filtered out)
✅ Normal span created (should be filtered)

📝 Test 5: Nested error spans
✅ Nested error spans created

📝 Test 6: Simulated database error
✅ Database error span created

⏳ Waiting 6 seconds for spans to be exported...

🔍 Querying database to verify data storage...

✅ SUCCESS! Found 5 log entries in database

Sample log entry:
{
  "id": 1,
  "service_name": "test-service",
  "severity": "ERROR",
  "message": "Test error occurred",
  "source": "otel",
  "metadata": {
    "traceId": "...",
    "spanId": "...",
    "spanName": "test-error-status",
    ...
  }
}

✨ Test completed!
```

## Verifying Database Storage

### Check the Database Directly

If you have database access, you can verify the data:

```sql
-- Check log entries from OpenTelemetry
SELECT id, service_name, severity, message, source, created_at 
FROM log_entries 
WHERE source = 'otel' 
ORDER BY created_at DESC 
LIMIT 10;

-- Check metadata
SELECT id, message, metadata_json 
FROM log_entries 
WHERE source = 'otel' 
LIMIT 1;
```

### Using the API

```bash
# Get logs (requires API key)
curl -H "X-API-Key: your_api_key_here" \
  http://localhost:8000/logs
```

## Troubleshooting

### No logs found in database

**Possible causes:**
1. The `/otel/errors` endpoint is not implemented
2. The API key is invalid or not active
3. The backend is not running
4. Network connectivity issues

**Solutions:**
- Check backend logs for errors
- Verify the API key is correct and active
- Ensure the backend server is running on the correct port
- Check that the endpoint exists: `curl http://localhost:8000/docs`

### Backend not reachable

**Solutions:**
- Start the backend: `cd apps/engine && uvicorn main:app --reload`
- Check the port: default is 8000
- Verify firewall settings

### Normal spans being exported

**This indicates a bug in the filtering logic.**

The exporter should only export spans with:
- Status code = ERROR
- Exception events
- Exception attributes

Check the `isErrorSpan()` method in `HealOpsExporter.ts`.

### Spans not being batched

The SDK uses `BatchSpanProcessor` with a 5-second interval. If you don't see spans being exported:
- Wait at least 6 seconds after creating spans
- Check that the SDK is properly initialized
- Verify the endpoint URL is correct

## Test Configuration

You can customize the test by setting environment variables:

```bash
# API Key (required)
export HEALOPS_API_KEY="your_api_key_here"

# Backend URL (optional, default: http://localhost:8000)
export HEALOPS_BACKEND_URL="http://localhost:8000"

# Service Name (optional, default: test-service)
export HEALOPS_SERVICE_NAME="my-test-service"
```

## Next Steps

After successful testing:

1. **Publish the Package**
   ```bash
   npm version patch
   git tag v0.1.1
   git push origin v0.1.1
   ```

2. **Use in Production**
   ```javascript
   const { initHealOpsOTel } = require('@healops/opentelemetry');
   
   initHealOpsOTel({
     apiKey: process.env.HEALOPS_API_KEY,
     serviceName: 'my-production-service',
     endpoint: 'https://engine.healops.ai/otel/errors'
   });
   ```

3. **Monitor the Dashboard**
   - Check the HealOps dashboard for incoming error logs
   - Verify incidents are being created
   - Test the AI analysis features
