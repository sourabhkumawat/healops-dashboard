package healops

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
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

			// Check for exception stack trace to extract code info
			if event.Name == "exception" {
				if stackTrace, ok := evtAttrs["exception.stacktrace"].(string); ok {
					e.extractCodeInfo(stackTrace, attributes)
				}
			}
		}

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
		})
	}

	return transformed
}

// Regex to find file paths in Go stack traces
// Format: /path/to/file.go:123 +0x...
var fileLineRegex = regexp.MustCompile(`\s+([^\s]+:\d+)`)

func (e *HealOpsExporter) extractCodeInfo(stackTrace string, attributes map[string]interface{}) {
	// If code info is already present, don't overwrite
	if _, ok := attributes["code.file.path"]; ok {
		return
	}

	lines := strings.Split(stackTrace, "\n")
	for _, line := range lines {
		// Skip standard library or runtime lines if possible (simple heuristic)
		if strings.Contains(line, "/usr/local/go/src/") || strings.Contains(line, "runtime/") {
			continue
		}

		matches := fileLineRegex.FindStringSubmatch(line)
		if len(matches) > 1 {
			fullPath := matches[1] // /path/to/file.go:123
			parts := strings.Split(fullPath, ":")
			if len(parts) >= 2 {
				filePath := parts[0]
				lineNumStr := parts[1]

				// Ensure it looks like a Go source file
				if !strings.HasSuffix(filePath, ".go") {
					continue
				}

				attributes["code.file.path"] = filePath
				if lineNum, err := strconv.Atoi(lineNumStr); err == nil {
					attributes["code.line.number"] = lineNum
				}

				// Try to extract function name from previous line
				// Go stack traces usually have function name on the line before the file path
				// This is hard to do reliably without iterating carefully, but we can try simple approach
				// or just leave it for now.
				return
			}
		}
	}
}
