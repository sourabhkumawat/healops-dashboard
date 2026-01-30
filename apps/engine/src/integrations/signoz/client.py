"""
SigNoz API client - fetch error logs and error traces only.
Uses POST /api/v5/query_range with signal=logs (filter ERROR/CRITICAL) and signal=traces (hasError=true).
"""
import requests
from typing import Any, Dict, List, Optional


def _build_logs_query_payload(start_ts_ms: int, end_ts_ms: int, limit: int = 100) -> Dict[str, Any]:
    """Build query_range JSON for logs with signal=logs, requestType=raw, filter for severity ERROR or CRITICAL only."""
    return {
        "start": start_ts_ms,
        "end": end_ts_ms,
        "requestType": "raw",
        "variables": {},
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "logs",
                        "stepInterval": 60,
                        "filter": {
                            "expression": "severity_text IN ('ERROR', 'error', 'CRITICAL', 'critical')"
                        },
                        "limit": limit,
                        "disabled": False,
                    },
                }
            ]
        },
    }


def _build_traces_query_payload(start_ts_ms: int, end_ts_ms: int, limit: int = 100) -> Dict[str, Any]:
    """Build query_range JSON for traces with signal=traces, filter hasError=true."""
    return {
        "start": start_ts_ms,
        "end": end_ts_ms,
        "requestType": "raw",
        "variables": {},
        "compositeQuery": {
            "queries": [
                {
                    "type": "builder_query",
                    "spec": {
                        "name": "A",
                        "signal": "traces",
                        "filter": {"expression": "hasError = true"},
                        "limit": limit,
                        "disabled": False,
                    },
                }
            ]
        },
    }


def _post_query_range(base_url: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST to {base_url}/api/v5/query_range with header SIGNOZ-API-KEY; return response JSON."""
    url = base_url.rstrip("/") + "/api/v5/query_range"
    headers = {
        "Content-Type": "application/json",
        "SIGNOZ-API-KEY": api_key,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_logs_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse SigNoz logs API response into list of dicts: service_name, message, severity, timestamp_ms, metadata."""
    events: List[Dict[str, Any]] = []
    # Response shape varies; common: result.queryA (or similar) with list of rows
    data = response.get("result", {}) or response.get("data", response)
    if isinstance(data, dict):
        # Often first query result is under key "A" or "queryA"
        for key in ("queryA", "A", "logs"):
            rows = data.get(key)
            if isinstance(rows, list):
                break
        else:
            rows = data.get("list", []) if isinstance(data.get("list"), list) else []
    elif isinstance(data, list):
        rows = data
    else:
        return events

    for row in rows:
        if not isinstance(row, dict):
            continue
        # SigNoz raw logs: body/message, severity_text/level, timestamp, attributes
        body = row.get("body") or row.get("message") or row.get("bodyMessage") or ""
        if isinstance(body, dict):
            body = body.get("string", str(body))
        severity = (
            (row.get("severity_text") or row.get("severity") or row.get("level") or "ERROR")
        ).upper()
        if severity not in ("ERROR", "CRITICAL"):
            continue
        ts = row.get("timestamp") or row.get("timestamp_nano")
        if ts is not None and ts > 1e12:
            ts = int(ts // 1_000_000)
        elif ts is not None:
            ts = int(ts)
        else:
            ts = 0
        service_name = (
            (row.get("resourceAttributes") or {}) if isinstance(row.get("resourceAttributes"), dict) else {}
        )
        if isinstance(service_name, dict):
            service_name = service_name.get("service.name") or service_name.get("service_name") or "unknown"
        else:
            service_name = str(service_name) if service_name else "unknown"
        metadata = {
            "traceId": row.get("traceId"),
            "spanId": row.get("spanId"),
            "attributes": row.get("attributes") if isinstance(row.get("attributes"), dict) else {},
        }
        events.append({
            "service_name": service_name,
            "message": str(body)[:2000] if body else "Error log",
            "severity": severity,
            "timestamp_ms": ts,
            "metadata": metadata,
        })
    return events


def _parse_traces_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse SigNoz traces API response into same normalized format: service_name, message, severity ERROR, metadata."""
    events: List[Dict[str, Any]] = []
    data = response.get("result", {}) or response.get("data", response)
    if isinstance(data, dict):
        for key in ("queryA", "A", "spans", "traces"):
            rows = data.get(key)
            if isinstance(rows, list):
                break
        else:
            rows = data.get("list", []) if isinstance(data.get("list"), list) else []
    elif isinstance(data, list):
        rows = data
    else:
        return events

    for row in rows:
        if not isinstance(row, dict):
            continue
        service_name = "unknown"
        res = row.get("resourceAttributes") or row.get("resource") or {}
        if isinstance(res, dict):
            service_name = res.get("service.name") or res.get("service_name") or "unknown"
        msg = (
            row.get("statusMessage")
            or row.get("errorMessage")
            or row.get("name")
            or "Error span"
        )
        ts = row.get("startTime") or row.get("startTimeUnixNano") or row.get("timestamp")
        if ts is not None and ts > 1e12:
            ts = int(ts // 1_000_000)
        elif ts is not None:
            ts = int(ts)
        else:
            ts = 0
        metadata = {
            "traceId": row.get("traceID") or row.get("traceId"),
            "spanId": row.get("spanID") or row.get("spanId"),
            "attributes": row.get("attributes") if isinstance(row.get("attributes"), dict) else {},
        }
        events.append({
            "service_name": service_name,
            "message": str(msg)[:2000],
            "severity": "ERROR",
            "timestamp_ms": ts,
            "metadata": metadata,
        })
    return events


def fetch_error_events(
    base_url: str,
    api_key: str,
    start_ts_ms: int,
    end_ts_ms: int,
    logs_limit: int = 100,
    traces_limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Fetch error logs and error traces from SigNoz; return single list of error events only.
    Only ERROR/CRITICAL logs and error spans are included.
    """
    events: List[Dict[str, Any]] = []
    try:
        logs_payload = _build_logs_query_payload(start_ts_ms, end_ts_ms, limit=logs_limit)
        logs_resp = _post_query_range(base_url, api_key, logs_payload)
        events.extend(_parse_logs_response(logs_resp))
    except Exception as e:
        print(f"⚠️ SigNoz logs query failed: {e}")
    try:
        traces_payload = _build_traces_query_payload(start_ts_ms, end_ts_ms, limit=traces_limit)
        traces_resp = _post_query_range(base_url, api_key, traces_payload)
        events.extend(_parse_traces_response(traces_resp))
    except Exception as e:
        print(f"⚠️ SigNoz traces query failed: {e}")
    return events
