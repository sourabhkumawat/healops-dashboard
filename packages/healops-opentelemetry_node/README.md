# @sourabhkumawat0105/healops-opentelemetry

The official HealOps OpenTelemetry SDK for Node.js and Browser. Automatically captures and reports error spans to the HealOps platform.

## Installation

```bash
npm install @sourabhkumawat0105/healops-opentelemetry
```

## Usage

### Node.js (Backend)

Initialize the SDK at the very beginning of your application (e.g., in `index.ts` or `app.ts`).

```typescript
import { initHealOpsOTel } from '@sourabhkumawat0105/healops-opentelemetry';

initHealOpsOTel({
  apiKey: process.env.HEALOPS_API_KEY || 'your-api-key',
  serviceName: 'my-service',
  // Optional: Override endpoint
  // endpoint: 'https://engine.healops.ai/otel/errors'
});
```

### Browser (Frontend)

Use the logger directly in your frontend application. **File paths are automatically captured!**

```typescript
import { HealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

// Initialize once in your app
const logger = new HealOpsLogger({
  apiKey: process.env.NEXT_PUBLIC_HEALOPS_API_KEY || 'your-api-key',
  serviceName: 'my-frontend-app',
  endpoint: 'http://localhost:8000',
  source: 'frontend'
});

// Use anywhere - file path, line, and column are automatically captured!
logger.info('User logged in', { userId: '123' });
logger.warn('Slow response', { responseTime: 2500 });
logger.error('API call failed', { endpoint: '/api/users' });
logger.critical('Payment failed', { orderId: 'ORD-123' });
```

### Browser with Console Interception (Automatic)

**Automatically capture ALL console.log/warn/error calls without changing your code!**

```typescript
import { initHealOpsLogger } from '@sourabhkumawat0105/healops-opentelemetry';

// Initialize with console interception enabled (default)
const logger = initHealOpsLogger({
  apiKey: process.env.NEXT_PUBLIC_HEALOPS_API_KEY || 'your-api-key',
  serviceName: 'my-frontend-app',
  endpoint: 'http://localhost:8000',
  source: 'frontend'
});

// Now ALL console calls are automatically sent to HealOps!
console.log('User clicked button');           // → Sent as INFO
console.warn('API response slow');            // → Sent as WARNING
console.error('Failed to load data');         // → Sent as ERROR
console.error(new Error('Something broke')); // → Sent as ERROR with stack trace

// You can still use the logger directly
logger.critical('Critical issue', { data: 'important' });
```

**Disable console interception:**
```typescript
const logger = initHealOpsLogger(config, false); // false = don't intercept console
```

**What gets logged:**
```json
{
  "message": "API call failed",
  "metadata": {
    "endpoint": "/api/users",
    "filePath": "webpack-internal:///./src/components/UserProfile.tsx",
    "line": 42,
    "column": 15
  }
}
```

## Configuration

| Option | Type | Description |
|Params|---|---|
| `apiKey` | `string` | **Required**. Your HealOps API key. |
| `serviceName` | `string` | **Required**. The name of your service. |
| `endpoint` | `string` | Optional. The HealOps ingestion endpoint. Defaults to `https://engine.healops.ai/otel/errors`. |
| `source` | `string` | Optional. Source identifier (e.g., 'frontend', 'backend'). |

## Features

- **Auto-instrumentation** (Node.js): Automatically instruments supported libraries (Express, Http, etc.) using OpenTelemetry.
- **Automatic File Path Detection** (Browser): Captures file path, line number, and column number from stack traces.
- **Error Filtering**: Only exports spans with status code `ERROR` or containing exceptions.
- **Efficient Batching**: Batches spans and sends them every 5 seconds.
- **Retries**: Automatically retries failed exports with exponential backoff.
- **Browser Compatible**: Works in all modern browsers (Chrome, Firefox, Safari, Edge).

## Troubleshooting

If you don't see errors in HealOps:
1. Ensure `HEALOPS_API_KEY` is correct.
2. Verify your application has internet access to `engine.healops.ai`.
3. Check console for any "Failed to export spans" messages (only printed if export fails).

