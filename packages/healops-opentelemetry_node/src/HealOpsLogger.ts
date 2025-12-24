import axios from 'axios';

export interface HealOpsLoggerConfig {
    apiKey: string;
    serviceName: string;
    endpoint?: string;
    source?: string;
    // Batching configuration
    enableBatching?: boolean;       // Default: true
    batchSize?: number;             // Default: 50
    batchIntervalMs?: number;       // Default: 1000ms
}

export interface LogPayload {
    service_name: string;
    severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
    message: string;
    source: string;
    timestamp?: string;
    metadata?: Record<string, any>;
}

export class HealOpsLogger {
    private config: HealOpsLoggerConfig;
    private endpoint: string;

    // Batching properties
    private logQueue: LogPayload[] = [];
    private batchTimeout: NodeJS.Timeout | null = null;
    private readonly BATCH_SIZE: number;
    private readonly BATCH_INTERVAL_MS: number;
    private readonly BATCHING_ENABLED: boolean;
    private isFlushing: boolean = false;
    private isDestroyed: boolean = false;

    // Shutdown handlers (stored for cleanup)
    private shutdownHandlers: {
        beforeExit?: () => void;
        sigint?: () => void;
        sigterm?: () => void;
    } = {};

    constructor(config: HealOpsLoggerConfig) {
        this.config = config;
        this.endpoint = config.endpoint || 'https://engine.healops.ai';

        // Validate batching configuration
        this.BATCHING_ENABLED = config.enableBatching !== false; // Default: true
        this.BATCH_SIZE = Math.max(1, Math.min(config.batchSize || 50, 1000)); // Clamp between 1-1000
        this.BATCH_INTERVAL_MS = Math.max(100, Math.min(config.batchIntervalMs || 1000, 60000)); // Clamp between 100ms-60s

        // Setup graceful shutdown handlers (with cleanup tracking)
        if (typeof process !== 'undefined' && process.on) {
            this.shutdownHandlers.beforeExit = () => {
                this.flushBatch().catch(err => {
                    console.error('Failed to flush logs on shutdown:', err);
                });
            };

            this.shutdownHandlers.sigint = () => {
                this.destroy();
                process.exit(0);
            };

            this.shutdownHandlers.sigterm = () => {
                this.destroy();
                process.exit(0);
            };

            process.on('beforeExit', this.shutdownHandlers.beforeExit);
            process.on('SIGINT', this.shutdownHandlers.sigint);
            process.on('SIGTERM', this.shutdownHandlers.sigterm);
        }
    }

    /**
     * Cleanup resources and remove event listeners
     */
    public destroy(): void {
        if (this.isDestroyed) return;

        this.isDestroyed = true;

        // Flush remaining logs synchronously (best effort)
        this.flushBatch().catch(() => {
            // Silent fail during destruction
        });

        // Clear timeout
        if (this.batchTimeout) {
            clearTimeout(this.batchTimeout);
            this.batchTimeout = null;
        }

        // Remove event listeners to prevent memory leaks
        if (typeof process !== 'undefined' && process.removeListener) {
            if (this.shutdownHandlers.beforeExit) {
                process.removeListener('beforeExit', this.shutdownHandlers.beforeExit);
            }
            if (this.shutdownHandlers.sigint) {
                process.removeListener('SIGINT', this.shutdownHandlers.sigint);
            }
            if (this.shutdownHandlers.sigterm) {
                process.removeListener('SIGTERM', this.shutdownHandlers.sigterm);
            }
        }
    }

    /**
     * Extract caller information from stack trace
     * This works in browsers (Chrome, Firefox, Safari, Edge)
     */
    private getCallerInfo(): {
        filePath?: string;
        line?: number;
        column?: number;
    } {
        try {
            const stack = new Error().stack;
            if (!stack) return {};

            const stackLines = stack.split('\n');
            // Skip: "Error", "getCallerInfo", "sendLog", and the public method (info/warn/error/critical)
            // Line 4 is the actual caller
            const callerLine = stackLines[4] || stackLines[3];
            if (!callerLine) return {};

            // Chrome/Edge format: "at functionName (file:line:column)" or "at file:line:column"
            const chromeMatch =
                callerLine.match(/\(([^)]+):(\d+):(\d+)\)/) ||
                callerLine.match(/at\s+([^:]+):(\d+):(\d+)/);
            if (chromeMatch) {
                return {
                    filePath: chromeMatch[1].trim(),
                    line: parseInt(chromeMatch[2]),
                    column: parseInt(chromeMatch[3])
                };
            }

            // Firefox format: "functionName@file:line:column"
            const firefoxMatch = callerLine.match(/@([^:]+):(\d+):(\d+)/);
            if (firefoxMatch) {
                return {
                    filePath: firefoxMatch[1].trim(),
                    line: parseInt(firefoxMatch[2]),
                    column: parseInt(firefoxMatch[3])
                };
            }

            return {};
        } catch (e) {
            // Silent fail - don't break logging if stack parsing fails
            return {};
        }
    }

    /**
     * Send an INFO level log
     */
    info(message: string, metadata?: Record<string, any>): void {
        // Handle promise rejection to prevent unhandled promise rejection warnings
        // Errors are already logged in sendLog, so we just need to catch to prevent unhandled rejections
        this.sendLog('INFO', message, metadata).catch(() => {
            // Error already logged in sendLog
        });
    }

    /**
     * Send a WARNING level log
     */
    warn(message: string, metadata?: Record<string, any>): void {
        // Handle promise rejection to prevent unhandled promise rejection warnings
        // Errors are already logged in sendLog, so we just need to catch to prevent unhandled rejections
        this.sendLog('WARNING', message, metadata).catch(() => {
            // Error already logged in sendLog
        });
    }

    /**
     * Send an ERROR level log (will be persisted and may create incident)
     */
    error(message: string, metadata?: Record<string, any>): void {
        // Handle promise rejection to prevent unhandled promise rejection warnings
        // Errors are already logged in sendLog, so we just need to catch to prevent unhandled rejections
        this.sendLog('ERROR', message, metadata).catch(() => {
            // Error already logged in sendLog
        });
    }

    /**
     * Send a CRITICAL level log (will be persisted and may create incident)
     */
    critical(message: string, metadata?: Record<string, any>): void {
        // Handle promise rejection to prevent unhandled promise rejection warnings
        // Errors are already logged in sendLog, so we just need to catch to prevent unhandled rejections
        this.sendLog('CRITICAL', message, metadata).catch(() => {
            // Error already logged in sendLog
        });
    }

    private async sendLog(
        severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL',
        message: string,
        metadata?: Record<string, any>
    ): Promise<void> {
        // Don't send logs if logger is destroyed
        if (this.isDestroyed) {
            return;
        }

        // Automatically capture caller info and merge with user metadata
        const callerInfo = this.getCallerInfo();
        const enrichedMetadata = {
            ...metadata,
            ...callerInfo
        };

        const payload: LogPayload = {
            service_name: this.config.serviceName,
            severity,
            message,
            source: this.config.source || 'healops-sdk',
            timestamp: new Date().toISOString(),
            metadata: enrichedMetadata
        };

        // If batching is disabled, send immediately
        if (!this.BATCHING_ENABLED) {
            await this.sendSingleLog(payload);
            return;
        }

        // Add to batch queue (thread-safe in Node.js single-threaded model)
        this.logQueue.push(payload);

        // Flush immediately if batch size reached
        if (this.logQueue.length >= this.BATCH_SIZE) {
            // Don't await - let it flush in background
            this.flushBatch().catch(err => {
                if (process.env.HEALOPS_DEBUG) {
                    console.error('Background flush failed:', err);
                }
            });
        } else {
            // Schedule batch flush if not already scheduled
            this.scheduleBatchFlush();
        }
    }

    /**
     * Schedule a batch flush after the configured interval
     */
    private scheduleBatchFlush(): void {
        if (this.batchTimeout) {
            return; // Already scheduled
        }

        this.batchTimeout = setTimeout(() => {
            this.flushBatch().catch(err => {
                if (process.env.HEALOPS_DEBUG) {
                    console.error('Failed to flush batch:', err);
                }
            });
        }, this.BATCH_INTERVAL_MS);
    }

    /**
     * Flush all queued logs to the backend
     */
    private async flushBatch(): Promise<void> {
        // Clear the timeout
        if (this.batchTimeout) {
            clearTimeout(this.batchTimeout);
            this.batchTimeout = null;
        }

        // Nothing to flush
        if (this.logQueue.length === 0 || this.isFlushing) {
            return;
        }

        // Mark as flushing to prevent concurrent flushes
        this.isFlushing = true;

        // Get current batch and clear queue
        const batch = [...this.logQueue];
        this.logQueue = [];

        try {
            // Try to send as batch first
            await this.sendBatchLogs(batch);

            if (process.env.HEALOPS_DEBUG) {
                console.log(`✓ HealOps flushed ${batch.length} logs`);
            }
        } catch (error: any) {
            // If batch endpoint fails, fall back to individual sends
            if (process.env.HEALOPS_DEBUG) {
                console.warn('Batch send failed, falling back to individual sends:', error.message);
            }

            // Try sending individually (without awaiting to avoid blocking)
            for (const log of batch) {
                this.sendSingleLog(log).catch(err => {
                    // Silent fail - already logged in sendSingleLog
                });
            }
        } finally {
            this.isFlushing = false;
        }
    }

    /**
     * Send a batch of logs to the backend
     */
    private async sendBatchLogs(logs: LogPayload[]): Promise<void> {
        const url = `${this.endpoint}/ingest/logs/batch`;

        try {
            const response = await axios.post(
                url,
                { logs },
                {
                    headers: {
                        'X-HealOps-Key': this.config.apiKey,
                        'Content-Type': 'application/json'
                    },
                    timeout: 5000 // Longer timeout for batches
                }
            );

            if (process.env.HEALOPS_DEBUG) {
                console.log(
                    `HealOps batch sent successfully: ${logs.length} logs`,
                    response.status
                );
            }
        } catch (error: any) {
            const errorMessage =
                error?.response?.data?.detail ||
                error?.message ||
                'Unknown error';

            // Only log in debug mode for batch failures (will retry individually)
            if (process.env.HEALOPS_DEBUG) {
                console.error('HealOps batch send failed:', {
                    message: errorMessage,
                    statusCode: error?.response?.status,
                    logCount: logs.length
                });
            }

            throw error;
        }
    }

    /**
     * Send a single log to the backend (legacy/fallback method)
     */
    private async sendSingleLog(payload: LogPayload): Promise<void> {
        const url = `${this.endpoint}/ingest/logs`;

        try {
            const response = await axios.post(url, payload, {
                headers: {
                    'X-HealOps-Key': this.config.apiKey,
                    'Content-Type': 'application/json'
                },
                timeout: 3000
            });

            if (
                process.env.HEALOPS_DEBUG &&
                (payload.severity === 'ERROR' || payload.severity === 'CRITICAL')
            ) {
                console.log(
                    `HealOps Logger successfully sent ${payload.severity} log:`,
                    response.status
                );
            }
        } catch (error: any) {
            const errorMessage =
                error?.response?.data?.detail ||
                error?.message ||
                'Unknown error';
            const statusCode = error?.response?.status;

            const isBrowser = typeof window !== 'undefined';
            const shouldLog = isBrowser || process.env.HEALOPS_DEBUG;

            if (shouldLog) {
                console.error(
                    `HealOps Logger failed to send ${payload.severity} log:`,
                    {
                        message: errorMessage,
                        statusCode,
                        url,
                        serviceName: this.config.serviceName,
                        severity: payload.severity,
                        error: error
                    }
                );
            }

            throw error;
        }
    }

    /**
     * Manually flush any queued logs (useful before process exit)
     */
    public async flush(): Promise<void> {
        await this.flushBatch();
    }
}
