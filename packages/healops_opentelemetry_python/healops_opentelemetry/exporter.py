import json
import time
import requests
import os
from typing import Sequence, Optional, Dict
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode, SpanKind
from .utils import extract_file_path_from_stack

class HealOpsSpanExporter(SpanExporter):
    def __init__(self, api_key: str, service_name: str, endpoint: str = "https://engine.healops.ai/otel/errors"):
        self.api_key = api_key
        self.service_name = service_name
        self.endpoint = endpoint

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS

        # Transform spans
        transformed_spans = [self._transform_span(span) for span in spans]

        payload = {
            "apiKey": self.api_key,
            "serviceName": self.service_name,
            "spans": transformed_spans
        }

        try:
            self._send(payload)
            return SpanExportResult.SUCCESS
        except Exception as e:
            if os.getenv("HEALOPS_DEBUG"):
                 print(f"Failed to export spans: {e}")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def _transform_span(self, span: ReadableSpan) -> dict:
        attributes = dict(span.attributes) if span.attributes else {}

        # Check for exception events
        stack_trace = None
        for event in span.events:
            if event.name == "exception":
                # OTel Python convention for exception
                if "exception.stacktrace" in event.attributes:
                    stack_trace = event.attributes["exception.stacktrace"]
                    attributes["exception.stacktrace"] = stack_trace
                if "exception.type" in event.attributes:
                    attributes["exception.type"] = event.attributes["exception.type"]
                if "exception.message" in event.attributes:
                    attributes["exception.message"] = event.attributes["exception.message"]

        # If no stack trace in events, check attributes
        if not stack_trace and span.status.status_code == StatusCode.ERROR:
            stack_trace = attributes.get("error.stack") or attributes.get("stack")
            if stack_trace:
                attributes["exception.stacktrace"] = stack_trace

        # Extract code location if available
        if stack_trace and "code.file.path" not in attributes:
            extracted_path = extract_file_path_from_stack(stack_trace)
            if extracted_path:
                attributes["code.file.path"] = extracted_path
                # We could parse line number too if we want to be more precise
                # but util currently returns just path.

        return {
            "traceId": f"{span.context.trace_id:032x}",
            "spanId": f"{span.context.span_id:016x}",
            "parentSpanId": f"{span.parent.span_id:016x}" if span.parent else None,
            "name": span.name,
            "kind": span.kind.name if hasattr(span.kind, 'name') else str(span.kind),
            "timestamp": int(time.time() * 1000), # Export time
            "startTime": self._ns_to_ms(span.start_time),
            "endTime": self._ns_to_ms(span.end_time),
            "attributes": attributes,
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
