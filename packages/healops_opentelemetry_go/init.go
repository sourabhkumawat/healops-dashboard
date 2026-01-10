package healops

import (
	"context"
	"fmt"
	"os"
)

// Config Universal configuration struct
type Config struct {
	APIKey         string
	ServiceName    string
	Endpoint       string
	CaptureConsole bool // Note: Go doesn't have console interception like Node/Python easily, so this might just be a flag
	CaptureErrors  bool // Go doesn't have global exception handler, but we can provide panic recovery middleware
	CaptureTraces  bool
	Debug          bool
	Environment    string
}

// Init initializes the HealOps SDK
func Init(config Config) (*Logger, func(), error) {
	if config.Debug {
		os.Setenv("HEALOPS_DEBUG", "1")
	}

	logger := NewLogger(LoggerConfig{
		APIKey:         config.APIKey,
		ServiceName:    config.ServiceName,
		Endpoint:       config.Endpoint,
		Source:         "go",
		Environment:    config.Environment,
		EnableBatching: true,
	})

	var shutdownTracer func(context.Context) error

	if config.CaptureTraces {
		var err error
		shutdownTracer, err = InitTracer(context.Background(), config.APIKey, config.ServiceName, config.Endpoint)
		if err != nil {
			if config.Debug {
				fmt.Printf("Failed to initialize OpenTelemetry: %v\n", err)
			}
		} else if config.Debug {
			fmt.Println("✓ HealOps OpenTelemetry initialized")
		}
	}

	cleanup := func() {
		logger.Shutdown()
		if shutdownTracer != nil {
			shutdownTracer(context.Background())
		}
	}

	if config.Debug {
		fmt.Printf("✓ HealOps initialized for %s\n", config.ServiceName)
	}

	return logger, cleanup, nil
}
