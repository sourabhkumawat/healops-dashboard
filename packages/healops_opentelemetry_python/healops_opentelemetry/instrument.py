import os
import sys
import logging
import traceback
from typing import Optional, Any
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from pkg_resources import iter_entry_points

from .exporter import HealOpsSpanExporter
from .logger import HealOpsLogger, HealOpsLogHandler

def init(
    api_key: str,
    service_name: str,
    endpoint: str = "https://engine.healops.ai",
    capture_console: bool = True,
    capture_errors: bool = True,
    capture_traces: bool = True,
    debug: bool = False,
    environment: Optional[str] = None
) -> HealOpsLogger:
    """
    Initialize HealOps SDK - Universal init function

    Args:
        api_key: Your HealOps API key
        service_name: Name of your service
        endpoint: Backend endpoint (optional)
        capture_console: Whether to capture logs (logging module)
        capture_errors: Whether to capture unhandled exceptions
        capture_traces: Whether to initialize OpenTelemetry tracing
        debug: Enable debug output
        environment: Environment name (prod, dev, etc.)
    """

    if debug:
        os.environ["HEALOPS_DEBUG"] = "1"

    # Create main logger instance
    logger = HealOpsLogger(
        api_key=api_key,
        service_name=service_name,
        endpoint=endpoint,
        source="python",
        environment=environment
    )

    # 1. Initialize OpenTelemetry (for Traces)
    if capture_traces:
        try:
            _init_otel(api_key, service_name, f"{endpoint}/otel/errors")
            if debug:
                print("✓ HealOps OpenTelemetry initialized")
        except Exception as e:
            if debug:
                print(f"Failed to initialize OpenTelemetry: {e}")

    # 2. Setup Console/Logging Interception
    if capture_console:
        # Add handler to root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO) # Ensure we capture info by default
        handler = HealOpsLogHandler(logger)
        root_logger.addHandler(handler)
        if debug:
            print("✓ HealOps logging handler initialized")

    # 3. Setup Global Error Handlers
    if capture_errors:
        sys.excepthook = _make_excepthook(logger)
        if debug:
            print("✓ HealOps error handlers initialized")

    if debug:
        print(f"✓ HealOps initialized for {service_name}")

    return logger

def _init_otel(api_key: str, service_name: str, endpoint: str):
    """Initialize OpenTelemetry"""
    resource = Resource.create(attributes={
        "service.name": service_name
    })

    provider = TracerProvider(resource=resource)
    exporter = HealOpsSpanExporter(api_key=api_key, service_name=service_name, endpoint=endpoint)
    
    processor = BatchSpanProcessor(exporter, schedule_delay_millis=5000)
    provider.add_span_processor(processor)
    
    trace.set_tracer_provider(provider)
    
    # Auto-instrumentation
    try:
        for entry_point in iter_entry_points("opentelemetry_instrumentor"):
            try:
                instrumentor: BaseInstrumentor = entry_point.load()()
                if not instrumentor.is_instrumented_by_opentelemetry:
                    instrumentor.instrument()
            except Exception:
                pass
    except ImportError:
        pass

def _make_excepthook(logger: HealOpsLogger):
    """Create a custom excepthook that logs to HealOps"""
    original_excepthook = sys.excepthook

    def healops_excepthook(type_, value, tb):
        # Log to HealOps
        try:
            error_msg = str(value)
            stack = "".join(traceback.format_exception(type_, value, tb))

            logger.critical(
                f"Uncaught Exception: {error_msg}",
                metadata={
                    "errorName": type_.__name__,
                    "errorMessage": error_msg,
                    "errorStack": stack,
                    "exception": {
                        "type": type_.__name__,
                        "message": error_msg,
                        "stacktrace": stack
                    },
                    "type": "uncaught_exception"
                }
            )
            # Ensure it sends
            logger.destroy() # Flushes batch

        except Exception:
            pass

        # Call original excepthook
        original_excepthook(type_, value, tb)

    return healops_excepthook

# Legacy support
init_healops_otel = lambda api_key, service_name, **kwargs: init(api_key, service_name, capture_console=False, capture_errors=False, **kwargs)
