package main

import (
	"fmt"
	"time"

	"github.com/healops/healops-opentelemetry-go"
)

func main() {
	// Initialize HealOps
	logger, cleanup, err := healops.Init(healops.Config{
		APIKey:      "healops_live_L1UcKqhSM5ufKjUXnoaOK9E5eaGRVlilBS2xUld14zs",
		ServiceName: "my-go-service",
		Endpoint:    "http://localhost:8000",
		CaptureTraces: true,
		Debug:       true,
	})
	if err != nil {
		panic(err)
	}
	defer cleanup()

	// Use Logger
	logger.Info("Go service started", nil)

	logger.Warn("Cache missing", map[string]interface{}{
		"key": "user:123",
	})

	logger.Error("Database connection failed", map[string]interface{}{
		"db": "postgres",
		"error": "connection refused",
	})

    // Simulate work
    time.Sleep(2 * time.Second)

	fmt.Println("Logs sent!")
}
