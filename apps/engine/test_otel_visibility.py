#!/usr/bin/env python3
"""
Test script to verify Full Log Visibility for OTel spans.
Simulates OTel spans (success & error) being sent to the backend.
"""

import requests
import time
import json
import uuid

# Use the existing API key
API_KEY = "healops_live_L1UcKqhSM5ufKjUXnoaOK9E5eaGRVlilBS2xUld14zs"
BASE_URL = "http://localhost:8000"

def create_span(name, status_code, status_message=None):
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    now = time.time()
    
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": None,
        "name": name,
        "timestamp": int(now * 1000),
        "startTime": int(now * 1000),
        "endTime": int((now + 0.1) * 1000),
        "attributes": {"http.method": "GET", "http.url": "/api/test"},
        "events": [],
        "status": {
            "code": status_code, # 1=OK, 2=ERROR
            "message": status_message
        },
        "resource": {"service.name": "otel-test-service"}
    }

def send_otel_spans(spans):
    payload = {
        "apiKey": API_KEY,
        "serviceName": "otel-test-service",
        "spans": spans
    }
    
    try:
        response = requests.post(f"{BASE_URL}/otel/errors", json=payload)
        print(f"Sent {len(spans)} spans -> {response.status_code}")
        print(f"Response: {response.json()}")
        return response.json()
    except Exception as e:
        print(f"Error sending spans: {e}")
        return None

def main():
    print("=" * 60)
    print("Full Log Visibility Test (OTel Spans)")
    print("=" * 60)
    print("Expected behavior:")
    print("  - ALL spans (Success & Error) should be broadcast to WebSocket")
    print("  - ONLY Error spans should be persisted to DB")
    print()
    
    # Create 1 Success Span (Code 1 = OK)
    success_span = create_span("GET /api/users", 1)
    
    # Create 1 Error Span (Code 2 = ERROR)
    error_span = create_span("POST /api/checkout", 2, "Payment Gateway Timeout")
    
    print("Sending batch with 1 Success and 1 Error span...")
    result = send_otel_spans([success_span, error_span])
    
    if result:
        received = result.get("received", 0)
        persisted = result.get("persisted", 0)
        
        print("\nTest Results:")
        print(f"Received:  {received} (Expected: 2)")
        print(f"Persisted: {persisted} (Expected: 1)")
        
        if received == 2 and persisted == 1:
            print("\n✅ SUCCESS: Backend correctly handled mixed spans!")
            print("   - Success span: Broadcasted (INFO) but NOT persisted")
            print("   - Error span:   Broadcasted (ERROR) AND persisted")
        else:
            print("\n❌ FAILURE: Counts do not match expectations.")

    print("\nCheck Dashboard Live Logs:")
    print("1. You should see 'GET /api/users' (INFO)")
    print("2. You should see 'POST /api/checkout' (ERROR)")

if __name__ == "__main__":
    main()
