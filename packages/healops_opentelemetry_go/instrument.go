package healops

import (
	"context"
	"fmt"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.17.0"
)

// InitTracer initializes the OpenTelemetry tracer
func InitTracer(ctx context.Context, apiKey string, serviceName string, endpoint string) (func(context.Context) error, error) {
	if endpoint == "" {
		endpoint = "https://engine.healops.ai/otel/errors"
	}

    // Note: The standard OTLP exporter sends protobuf by default.
    // HealOps custom exporter in Node/Python uses a custom JSON format.
    // For Go, to match perfectly, we would implement a custom SpanExporter interface.
    // For now, we use standard OTLP HTTP which many backends support,
    // but if HealOps backend strictly requires the custom JSON format from Node/Python SDKs,
    // we would need to implement `sdktrace.SpanExporter`.

    // Assuming we want to use the standard OTLP exporter for now, or we can implement a custom one.
    // Given the Python/Node implementation uses a custom payload structure, let's implement a custom exporter.

    // Using custom exporter
    exporter := NewHealOpsExporter(apiKey, serviceName, endpoint)

	res, err := resource.New(ctx,
		resource.WithAttributes(
			semconv.ServiceName(serviceName),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create resource: %w", err)
	}

	bsp := sdktrace.NewBatchSpanProcessor(exporter, sdktrace.WithBatchTimeout(5*time.Second))
	tracerProvider := sdktrace.NewTracerProvider(
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
		sdktrace.WithResource(res),
		sdktrace.WithSpanProcessor(bsp),
	)
	otel.SetTracerProvider(tracerProvider)

	return tracerProvider.Shutdown, nil
}

// Standard OTLP fallback (if needed)
func initStandardOTLP(ctx context.Context, endpoint string) (*otlptrace.Exporter, error) {
    client := otlptracehttp.NewClient(
        otlptracehttp.WithEndpoint(endpoint),
        otlptracehttp.WithInsecure(), // If needed
    )
    return otlptrace.New(ctx, client)
}
