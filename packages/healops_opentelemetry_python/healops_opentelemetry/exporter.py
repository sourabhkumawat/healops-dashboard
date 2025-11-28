import json
import time
import requests
from typing import Sequence, Optional
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

class HealOpsSpanExporter(SpanExporter):
    def __init__(self, api_key: str, service_name: str, endpoint: str = "https://engine.healops.ai/otel/errors"):
        self.api_key = api_key
        self.service_name = service_name
        self.endpoint = endpoint

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS

        payload = {
            "apiKey": self.api_key,
            "serviceName": self.service_name,
            "spans": [self._transform_span(span) for span in spans]
        }

        try:
            self._send(payload)
            return SpanExportResult.SUCCESS
        except Exception as e:
            # In a real scenario, we might want to log this, but requirements say silence logs in production
            # We can print to stderr if needed, or just return FAILURE
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    # _is_error_span method removed as we now export all spans

    def _transform_span(self, span: ReadableSpan) -> dict:
        return {
            "traceId": f"{span.context.trace_id:032x}",
            "spanId": f"{span.context.span_id:016x}",
            "parentSpanId": f"{span.parent.span_id:016x}" if span.parent else None,
            "name": span.name,
            "timestamp": int(time.time() * 1000),
            "startTime": self._ns_to_ms(span.start_time),
            "endTime": self._ns_to_ms(span.end_time),
            "attributes": dict(span.attributes) if span.attributes else {},
            "events": [
                {
                    "name": event.name,
                    "time": self._ns_to_ms(event.timestamp),
                    "attributes": dict(event.attributes) if event.attributes else {}
                }
                for event in span.events
            ],
            "status": {
                "code": span.status.status_code.value,
                "message": span.status.description
            },
            "resource": dict(span.resource.attributes)
        }

    def _ns_to_ms(self, ns: int) -> int:
        return int(ns / 1e6) if ns else 0

    def _send(self, payload: dict, attempt: int = 1) -> None:
        max_retries = 3
        timeout = 3 # 3 seconds

        try:
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "HealOps-OTel-SDK/1.0"
            }
            response = requests.post(self.endpoint, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            if attempt < max_retries:
                delay = (2 ** attempt) * 0.1 # Exponential backoff
                time.sleep(delay)
                self._send(payload, attempt + 1)
            else:
                raise e
