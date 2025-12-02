import axios from 'axios';

export interface HealOpsLoggerConfig {
    apiKey: string;
    serviceName: string;
    endpoint?: string;
    source?: string;
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

    constructor(config: HealOpsLoggerConfig) {
        this.config = config;
        this.endpoint = config.endpoint || 'https://engine.healops.ai';
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

        const url = `${this.endpoint}/ingest/logs`;

        try {
            // Always attempt to send the log - this should never be conditional
            const response = await axios.post(url, payload, {
                headers: {
                    'X-HealOps-Key': this.config.apiKey,
                    'Content-Type': 'application/json'
                },
                timeout: 3000
            });

            // Optional: Log success for ERROR and CRITICAL logs if debug mode is enabled
            if (
                process.env.HEALOPS_DEBUG &&
                (severity === 'ERROR' || severity === 'CRITICAL')
            ) {
                console.log(
                    `HealOps Logger successfully sent ${severity} log:`,
                    response.status
                );
            }
        } catch (error: any) {
            // Always log failures to help with debugging - logs should always be sent
            const errorMessage =
                error?.response?.data?.detail ||
                error?.message ||
                'Unknown error';
            const statusCode = error?.response?.status;

            // Always log errors in browser, or if HEALOPS_DEBUG is set in Node.js
            const isBrowser = typeof window !== 'undefined';
            const shouldLog = isBrowser || process.env.HEALOPS_DEBUG;

            if (shouldLog) {
                console.error(
                    `HealOps Logger failed to send ${severity} log:`,
                    {
                        message: errorMessage,
                        statusCode,
                        url,
                        serviceName: this.config.serviceName,
                        severity,
                        error: error
                    }
                );
            }

            // Re-throw to be caught by the caller's catch handler
            // This ensures the promise rejection is properly handled
            throw error;
        }
    }
}
