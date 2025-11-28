# HealOps OpenTelemetry SDK for Python

The official HealOps OpenTelemetry SDK for Python. Automatically captures and reports error spans to the HealOps platform.

## Installation

```bash
pip install healops-opentelemetry
```

## Usage

Initialize the SDK at the start of your application.

```python
import os
from healops_opentelemetry import init_healops_otel

init_healops_otel(
    api_key=os.getenv("HEALOPS_API_KEY", "your-api-key"),
    service_name="my-service"
)
```

## Configuration

| Option | Type | Description |
|---|---|---|
| `api_key` | `str` | **Required**. Your HealOps API key. |
| `service_name` | `str` | **Required**. The name of your service. |
| `endpoint` | `str` | Optional. The HealOps ingestion endpoint. Defaults to `https://engine.healops.ai/otel/errors`. |

## Features

- **Auto-instrumentation**: Automatically instruments supported libraries if `opentelemetry-instrument` is used or libraries are installed.
- **Error Filtering**: Only exports spans with status code `ERROR` or containing exceptions.
- **Efficient Batching**: Batches spans and sends them every 5 seconds.
- **Retries**: Automatically retries failed exports with exponential backoff.

## Troubleshooting

If you don't see errors in HealOps:
1. Ensure `HEALOPS_API_KEY` is correct.
2. Verify your application has internet access to `engine.healops.ai`.
3. Ensure you are actually generating errors (exceptions or spans with Error status).
