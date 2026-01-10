package healops

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"time"
)

// LogLevel defines the severity of a log
type LogLevel string

const (
	InfoLevel     LogLevel = "INFO"
	WarningLevel  LogLevel = "WARNING"
	ErrorLevel    LogLevel = "ERROR"
	CriticalLevel LogLevel = "CRITICAL"
)

// LoggerConfig Configuration for HealOps Logger
type LoggerConfig struct {
	APIKey          string
	ServiceName     string
	Endpoint        string
	Source          string
	Environment     string
	Release         string
	EnableBatching  bool
	BatchSize       int
	BatchInterval   time.Duration
}

// LogPayload represents a log message sent to the backend
type LogPayload struct {
	ServiceName string                 `json:"service_name"`
	Severity    LogLevel               `json:"severity"`
	Message     string                 `json:"message"`
	Source      string                 `json:"source"`
	Timestamp   string                 `json:"timestamp"`
	Environment string                 `json:"environment,omitempty"`
	Release     string                 `json:"release,omitempty"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

// Logger is the main struct for HealOps logging
type Logger struct {
	config    LoggerConfig
	logQueue  chan LogPayload
	done      chan struct{}
	wg        sync.WaitGroup
	isRunning bool
}

// NewLogger creates a new HealOps Logger
func NewLogger(config LoggerConfig) *Logger {
	if config.Endpoint == "" {
		config.Endpoint = "https://engine.healops.ai"
	}
	if config.Source == "" {
		config.Source = "go"
	}
	if config.BatchSize <= 0 {
		config.BatchSize = 50
	}
	if config.BatchInterval <= 0 {
		config.BatchInterval = 1 * time.Second
	}

	logger := &Logger{
		config:    config,
		logQueue:  make(chan LogPayload, config.BatchSize*2),
		done:      make(chan struct{}),
		isRunning: true,
	}

	if config.EnableBatching {
		logger.wg.Add(1)
		go logger.processBatch()
	}

	return logger
}

// Info logs an informational message
func (l *Logger) Info(message string, metadata map[string]interface{}) {
	l.log(InfoLevel, message, metadata)
}

// Warn logs a warning message
func (l *Logger) Warn(message string, metadata map[string]interface{}) {
	l.log(WarningLevel, message, metadata)
}

// Error logs an error message
func (l *Logger) Error(message string, metadata map[string]interface{}) {
	l.log(ErrorLevel, message, metadata)
}

// Critical logs a critical message
func (l *Logger) Critical(message string, metadata map[string]interface{}) {
	l.log(CriticalLevel, message, metadata)
}

// Shutdown gracefully shuts down the logger
func (l *Logger) Shutdown() {
	if !l.isRunning {
		return
	}
	l.isRunning = false
	close(l.done)
	l.wg.Wait()

	// Flush remaining logs in queue
    // Note: In a real implementation, we would want to ensure channel is drained
}

func (l *Logger) log(severity LogLevel, message string, metadata map[string]interface{}) {
	if !l.isRunning {
		return
	}

    // Enrich metadata with caller info (simplified for now)
    // In a full implementation, we would use runtime.Caller here
    if metadata == nil {
        metadata = make(map[string]interface{})
    }

	payload := LogPayload{
		ServiceName: l.config.ServiceName,
		Severity:    severity,
		Message:     message,
		Source:      l.config.Source,
		Timestamp:   time.Now().UTC().Format(time.RFC3339),
		Environment: l.config.Environment,
		Release:     l.config.Release,
		Metadata:    metadata,
	}

	if l.config.EnableBatching {
		select {
		case l.logQueue <- payload:
		default:
			// Queue full, drop log or send directly (fallback)
			if os.Getenv("HEALOPS_DEBUG") != "" {
				fmt.Println("HealOps log queue full, dropping log")
			}
		}
	} else {
		l.sendSingleLog(payload)
	}
}

func (l *Logger) processBatch() {
	defer l.wg.Done()

	batch := make([]LogPayload, 0, l.config.BatchSize)
	timer := time.NewTimer(l.config.BatchInterval)
    defer timer.Stop()

	flush := func() {
		if len(batch) > 0 {
			l.sendBatch(batch)
			batch = make([]LogPayload, 0, l.config.BatchSize)
		}
	}

	for {
		select {
		case log := <-l.logQueue:
			batch = append(batch, log)
			if len(batch) >= l.config.BatchSize {
				flush()
				// Reset timer
                if !timer.Stop() {
                    <-timer.C
                }
                timer.Reset(l.config.BatchInterval)
			}
		case <-timer.C:
			flush()
			timer.Reset(l.config.BatchInterval)
		case <-l.done:
			flush()
			return
		}
	}
}

func (l *Logger) sendBatch(logs []LogPayload) {
	url := fmt.Sprintf("%s/ingest/logs/batch", l.config.Endpoint)

    jsonData, err := json.Marshal(map[string]interface{}{"logs": logs})
	if err != nil {
		if os.Getenv("HEALOPS_DEBUG") != "" {
			fmt.Printf("Error marshalling batch logs: %v\n", err)
		}
		return
	}

	req, err := http.NewRequest("POST", url, bytes.NewBuffer(jsonData))
	if err != nil {
		return
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-HealOps-Key", l.config.APIKey)

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		if os.Getenv("HEALOPS_DEBUG") != "" {
			fmt.Printf("Error sending batch logs: %v\n", err)
		}
		// Fallback to single logs?
        for _, log := range logs {
            l.sendSingleLog(log)
        }
		return
	}
	defer resp.Body.Close()

    if os.Getenv("HEALOPS_DEBUG") != "" {
        fmt.Printf("HealOps flushed %d logs\n", len(logs))
    }
}

func (l *Logger) sendSingleLog(payload LogPayload) {
	url := fmt.Sprintf("%s/ingest/logs", l.config.Endpoint)
	jsonData, err := json.Marshal(payload)
	if err != nil {
		return
	}

	req, err := http.NewRequest("POST", url, bytes.NewBuffer(jsonData))
	if err != nil {
		return
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-HealOps-Key", l.config.APIKey)

	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		if os.Getenv("HEALOPS_DEBUG") != "" {
			fmt.Printf("Error sending log: %v\n", err)
		}
		return
	}
	defer resp.Body.Close()
}
