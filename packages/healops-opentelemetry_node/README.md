# @sourabhkumawat0105/healops-opentelemetry

The official HealOps OpenTelemetry SDK for Node.js. Automatically captures and reports error spans to the HealOps platform.

## Installation

```bash
npm install @sourabhkumawat0105/healops-opentelemetry
```

## Usage

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

## Configuration

| Option | Type | Description |
|Params|---|---|
| `apiKey` | `string` | **Required**. Your HealOps API key. |
| `serviceName` | `string` | **Required**. The name of your service. |
| `endpoint` | `string` | Optional. The HealOps ingestion endpoint. Defaults to `https://engine.healops.ai/otel/errors`. |

## Features

- **Auto-instrumentation**: Automatically instruments supported libraries (Express, Http, etc.) using OpenTelemetry.
- **Error Filtering**: Only exports spans with status code `ERROR` or containing exceptions.
- **Efficient Batching**: Batches spans and sends them every 5 seconds.
- **Retries**: Automatically retries failed exports with exponential backoff.

## Troubleshooting

If you don't see errors in HealOps:
1. Ensure `HEALOPS_API_KEY` is correct.
2. Verify your application has internet access to `engine.healops.ai`.
3. Check console for any "Failed to export spans" messages (only printed if export fails).
