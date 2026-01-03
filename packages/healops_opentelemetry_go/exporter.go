package healops

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"time"

	"go.opentelemetry.io/otel/sdk/trace"
)

// HealOpsExporter implements trace.SpanExporter
type HealOpsExporter struct {
	apiKey      string
	serviceName string
	endpoint    string
	client      *http.Client
}

// NewHealOpsExporter creates a new HealOpsExporter
func NewHealOpsExporter(apiKey string, serviceName string, endpoint string) *HealOpsExporter {
	return &HealOpsExporter{
		apiKey:      apiKey,
		serviceName: serviceName,
		endpoint:    endpoint,
		client:      &http.Client{Timeout: 10 * time.Second},
	}
}

// ExportSpans exports a batch of spans
func (e *HealOpsExporter) ExportSpans(ctx context.Context, spans []trace.ReadOnlySpan) error {
	if len(spans) == 0 {
		return nil
	}

	payload := map[string]interface{}{
		"apiKey":      e.apiKey,
		"serviceName": e.serviceName,
		"spans":       e.transformSpans(spans),
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, "POST", e.endpoint, bytes.NewBuffer(jsonData))
	if err != nil {
		return err
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "HealOps-OTel-Go-SDK/1.0")

	resp, err := e.client.Do(req)
	if err != nil {
		if os.Getenv("HEALOPS_DEBUG") != "" {
			fmt.Printf("Failed to export spans: %v\n", err)
		}
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return fmt.Errorf("failed to export spans, status: %s", resp.Status)
	}

	return nil
}

// Shutdown shuts down the exporter
func (e *HealOpsExporter) Shutdown(ctx context.Context) error {
	return nil
}

func (e *HealOpsExporter) transformSpans(spans []trace.ReadOnlySpan) []map[string]interface{} {
	transformed := make([]map[string]interface{}, 0, len(spans))

	for _, span := range spans {
		attributes := make(map[string]interface{})
		for _, kv := range span.Attributes() {
			attributes[string(kv.Key)] = kv.Value.AsInterface()
		}

		events := make([]map[string]interface{}, 0, len(span.Events()))
		for _, event := range span.Events() {
			evtAttrs := make(map[string]interface{})
			for _, kv := range event.Attributes {
				evtAttrs[string(kv.Key)] = kv.Value.AsInterface()
			}
			events = append(events, map[string]interface{}{
				"name":       event.Name,
				"time":       event.Time.UnixNano() / 1e6, // ms
				"attributes": evtAttrs,
			})
		}

        // Extract stack trace from events if present (Go OTel uses events for exceptions)
        // Similar logic to Node/Python SDKs could be added here to extract code info

		transformed = append(transformed, map[string]interface{}{
			"traceId":      span.SpanContext().TraceID().String(),
			"spanId":       span.SpanContext().SpanID().String(),
			"parentSpanId": span.Parent().SpanID().String(),
			"name":         span.Name(),
			"kind":         span.SpanKind().String(),
			"timestamp":    time.Now().UnixNano() / 1e6,
			"startTime":    span.StartTime().UnixNano() / 1e6,
			"endTime":      span.EndTime().UnixNano() / 1e6,
			"attributes":   attributes,
			"events":       events,
			"status": map[string]interface{}{
				"code":    span.Status().Code,
				"message": span.Status().Description,
			},
            // Resource attributes could also be added
		})
	}

	return transformed
}
