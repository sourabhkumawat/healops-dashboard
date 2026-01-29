"""
Structured observability logging for incident resolution and agent flows.

Use for tracing phases, durations, and counts so log aggregators (Datadog, Splunk, etc.)
can index and alert. All logs use a dedicated logger and consistent extra= fields.
"""
import logging
import time
from typing import Any, Optional

OBS_LOGGER_NAME = "healops.observability"
_logger: Optional[logging.Logger] = None


def _logger_obs() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger(OBS_LOGGER_NAME)
    return _logger


def log_phase(
    phase: str,
    *,
    incident_id: Optional[int] = None,
    duration_sec: Optional[float] = None,
    **kwargs: Any,
) -> None:
    """
    Log a single observability event with structured fields.

    Args:
        phase: Phase name (e.g. run_start, memory_retrieved, plan_created, crew_completed).
        incident_id: Optional incident ID for correlation.
        duration_sec: Optional duration in seconds (logged as duration_ms in extra).
        **kwargs: Additional key-value pairs (ints, floats, strings, bools) for extra.
    """
    extra: dict[str, Any] = {"phase": phase}
    if incident_id is not None:
        extra["incident_id"] = incident_id
    if duration_sec is not None:
        extra["duration_ms"] = round(duration_sec * 1000)
    for k, v in kwargs.items():
        if v is not None and k != "phase":
            extra[k] = v
    msg = f"obs phase={phase}"
    if incident_id is not None:
        msg += f" incident_id={incident_id}"
    if duration_sec is not None:
        msg += f" duration_ms={round(duration_sec * 1000)}"
    for k, v in kwargs.items():
        if v is not None and k != "phase":
            msg += f" {k}={v}"
    _logger_obs().info(msg, extra=extra)


def log_phase_start(phase: str, incident_id: Optional[int] = None, **kwargs: Any) -> float:
    """
    Log phase start and return current time for later duration calculation.

    Returns:
        time.time() value to pass to log_phase(..., duration_sec=time.time() - t0).
    """
    log_phase(phase, incident_id=incident_id, **kwargs)
    return time.time()
