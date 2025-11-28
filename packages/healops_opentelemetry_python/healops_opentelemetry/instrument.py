import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.auto_instrumentation import sitecustomize
from .exporter import HealOpsSpanExporter

def init_healops_otel(api_key: str, service_name: str, endpoint: str = "https://engine.healops.ai/otel/errors"):
    resource = Resource.create(attributes={
        "service.name": service_name
    })

    provider = TracerProvider(resource=resource)
    exporter = HealOpsSpanExporter(api_key=api_key, service_name=service_name, endpoint=endpoint)
    
    # 5 second batch interval
    processor = BatchSpanProcessor(exporter, schedule_delay_millis=5000)
    provider.add_span_processor(processor)
    
    trace.set_tracer_provider(provider)
    
    # Auto-instrumentation is typically handled by `opentelemetry-instrument` command or sitecustomize
    # However, if we want to programmatically instrument common libraries if they are installed:
    # This part depends on how the user runs the app. 
    # If they use `opentelemetry-instrument python app.py`, it works automatically.
    # If they just import this function, we might want to trigger some instrumentation manually 
    # or rely on the user to install `opentelemetry-distro`.
    # For this SDK, let's assume we want to make it easy.
    
    # We can try to load installed instrumentors
    try:
        from opentelemetry.instrumentation.dependencies import get_distro_dependencies
        from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
        from pkg_resources import iter_entry_points

        for entry_point in iter_entry_points("opentelemetry_instrumentor"):
            try:
                instrumentor: BaseInstrumentor = entry_point.load()()
                if not instrumentor.is_instrumented_by_opentelemetry:
                    instrumentor.instrument()
            except Exception:
                # Silently fail if instrumentation fails, as per requirements
                pass
    except ImportError:
        pass
