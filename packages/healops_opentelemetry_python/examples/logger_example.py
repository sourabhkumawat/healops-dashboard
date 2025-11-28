#!/usr/bin/env python3
"""Example usage of HealOps Logger for Python"""

from healops_opentelemetry.logger import HealOpsLogger

# Initialize the logger
logger = HealOpsLogger(
    api_key="healops_live_L1UcKqhSM5ufKjUXnoaOK9E5eaGRVlilBS2xUld14zs",  # Replace with your API key
    service_name="my-python-app",
    endpoint="http://localhost:8000",
    source="python-app"
)

# Example usage
print("Sending logs to HealOps...\n")

# INFO logs - will be broadcast but NOT persisted
logger.info("Application started successfully")
logger.info("User logged in", metadata={"user_id": "12345", "username": "jane_doe"})

# WARNING logs - will be broadcast but NOT persisted
logger.warn("High memory usage detected", metadata={"memory": "85%"})
logger.warn("API rate limit approaching", metadata={"remaining": 10})

# ERROR logs - will be broadcast AND persisted, may create incident
logger.error("Database connection failed", metadata={
    "error": "Connection timeout",
    "database": "postgres",
    "host": "db.example.com"
})

# CRITICAL logs - will be broadcast AND persisted, may create incident
logger.critical("Payment processing service down", metadata={
    "service": "stripe",
    "last_success": "2024-01-01T12:00:00Z"
})

print("\n✓ Logs sent! Check your HealOps dashboard at http://localhost:3001")
print("  - All 6 logs should appear in Live Logs")
print("  - Only 2 logs (ERROR + CRITICAL) should be in database")
print("  - Incidents should be created for errors")
