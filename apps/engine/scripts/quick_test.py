#!/usr/bin/env python3
"""Quick test to verify WebSocket flow"""
import requests
import time

# Use the newly generated API key
API_KEY = "healops_live_L1UcKqhSM5ufKjUXnoaOK9E5eaGRVlilBS2xUld14zs"
BASE_URL = "http://localhost:8000"

def send_log(severity, message):
    payload = {
        "service_name": "test-service",
        "severity": severity,
        "message": message,
        "source": "test",
        "metadata": {"test": True}
    }
    
    headers = {"X-HealOps-Key": API_KEY}
    
    try:
        response = requests.post(f"{BASE_URL}/ingest/logs", json=payload, headers=headers)
        result = response.json()
        print(f"✓ [{severity}] {message}")
        print(f"  Response: {result}")
        return result
    except Exception as e:
        print(f"✗ Error: {e}")
        return None

print("=" * 60)
print("Testing WebSocket Flow")
print("=" * 60)
print()

# Test 1: INFO log (should broadcast but NOT persist)
print("1. Sending INFO log (should NOT be saved to DB)...")
r1 = send_log("INFO", "This is an info message")
time.sleep(0.5)

# Test 2: WARNING log (should broadcast but NOT persist)
print("\n2. Sending WARNING log (should NOT be saved to DB)...")
r2 = send_log("WARNING", "This is a warning message")
time.sleep(0.5)

# Test 3: ERROR log (should broadcast AND persist)
print("\n3. Sending ERROR log (SHOULD be saved to DB)...")
r3 = send_log("ERROR", "This is an error message - should create incident")
time.sleep(0.5)

# Test 4: CRITICAL log (should broadcast AND persist)
print("\n4. Sending CRITICAL log (SHOULD be saved to DB)...")
r4 = send_log("CRITICAL", "This is a critical error - should create/update incident")

print("\n" + "=" * 60)
print("Test Summary:")
print("=" * 60)
print(f"INFO:     persisted={r1.get('persisted', 'N/A') if r1 else 'FAILED'} (expected: False)")
print(f"WARNING:  persisted={r2.get('persisted', 'N/A') if r2 else 'FAILED'} (expected: False)")
print(f"ERROR:    persisted={r3.get('persisted', 'N/A') if r3 else 'FAILED'} (expected: True)")
print(f"CRITICAL: persisted={r4.get('persisted', 'N/A') if r4 else 'FAILED'} (expected: True)")

print("\n✓ Check the dashboard - all 4 logs should appear in Live Logs!")
print("✓ Check database - only 2 logs (ERROR + CRITICAL) should be saved")
print("✓ Check incidents - should have 1-2 incidents created")
