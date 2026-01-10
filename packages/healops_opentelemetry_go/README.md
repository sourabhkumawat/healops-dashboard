# HealOps OpenTelemetry Go SDK

HealOps OpenTelemetry SDK for Go applications.

## Installation

```bash
go get github.com/healops/healops-opentelemetry-go
```

## Usage

```go
package main

import (
	"context"
	"github.com/healops/healops-opentelemetry-go"
)

func main() {
	// Initialize HealOps SDK
	logger, cleanup, err := healops.Init(healops.Config{
		APIKey:        "your-api-key",
		ServiceName:   "your-service-name",
		CaptureTraces: true,
	})
	if err != nil {
		panic(err)
	}
	defer cleanup()

	// Use the logger
	logger.Info("Application started", nil)
}
```

## Deployment / Release

To release a new version of this Go module (assuming it is part of the `healops-opentelemetry` monorepo):

1.  Ensure `go.mod` module path matches your repository URL structure.
2.  Create a git tag with the prefix `packages/healops_opentelemetry_go/`:
    ```bash
    git tag packages/healops_opentelemetry_go/v0.1.0
    git push origin packages/healops_opentelemetry_go/v0.1.0
    ```
