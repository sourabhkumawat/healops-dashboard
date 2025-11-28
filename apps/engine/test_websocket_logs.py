#!/usr/bin/env python3
"""
Test script to verify WebSocket live logs and error-only persistence.
Sends a mix of INFO, WARNING, and ERROR logs to test:
1. WebSocket broadcasting (all logs)
2. Error-only persistence (only ERROR/CRITICAL saved to DB)
3. Incident creation (for ERROR/CRITICAL logs)
"""

import requests
import time
import json

API_KEY = "healops_live_YOUR_KEY_HERE"  # Replace with actual API key
BASE_URL = "http://localhost:8000"

def send_log(severity, message, service_name="test-service"):
    """Send a log to the backend"""
    payload = {
        "service_name": service_name,
        "severity": severity,
        "message": message,
        "source": "test-script",
        "metadata": {
            "test": True,
            "timestamp": time.time()
        }
    }
    
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/ingest/logs",
            json=payload,
            headers=headers
        )
        print(f"[{severity}] {message} -> {response.status_code}: {response.json()}")
    except Exception as e:
        print(f"Error sending log: {e}")

def main():
    print("=" * 60)
    print("WebSocket Live Logs Test")
    print("=" * 60)
    print("\nSending mixed severity logs...")
    print("Expected behavior:")
    print("  - ALL logs should appear in WebSocket (dashboard)")
    print("  - Only ERROR/CRITICAL should be saved to database")
    print("  - Incidents should be created for ERROR/CRITICAL\n")
    
    # Send INFO logs (should NOT be persisted)
    for i in range(3):
        send_log("INFO", f"Info message {i+1} - should NOT be in DB")
        time.sleep(0.5)
    
    # Send WARNING logs (should NOT be persisted)
    for i in range(2):
        send_log("WARNING", f"Warning message {i+1} - should NOT be in DB")
        time.sleep(0.5)
    
    # Send ERROR logs (SHOULD be persisted and create incidents)
    for i in range(2):
        send_log("ERROR", f"Error message {i+1} - SHOULD be in DB and create incident")
        time.sleep(0.5)
    
    # Send CRITICAL log (SHOULD be persisted and create/update incident)
    send_log("CRITICAL", "Critical error - SHOULD be in DB and escalate incident")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
    print("\nVerification steps:")
    print("1. Check dashboard - all 8 logs should appear in Live Logs")
    print("2. Check database - only 3 logs (2 ERROR + 1 CRITICAL) should be saved")
    print("3. Check incidents table - should have 1-2 incidents created")

if __name__ == "__main__":
    main()
