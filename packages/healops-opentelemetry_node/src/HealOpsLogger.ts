import axios from 'axios';
import {
    resolveStackTrace,
    resolveFilePath,
    extractFilePathFromStack,
    isSourceFile
} from './sourceMapResolver';

/**
 * Filter out SDK internal frames from stack traces
 * This is a synchronous operation for immediate filtering
 */
function filterSdkFrames(stack: string | undefined): string | undefined {
    if (!stack) return undefined;

    const lines = stack.split('\n');
    const filtered: string[] = [];
    const sdkPatterns = [
        /HealOpsLogger/,
        /healops-opentelemetry/,
        /getCallerInfo/,
        /sendLog/,
        /\.error\(/,
        /\.warn\(/,
        /\.info\(/,
        /\.critical\(/,
        /ConsoleInterceptor/,
        /initHealOpsLogger/
    ];

    // Filter out SDK internal frames
    for (const line of lines) {
        // Always keep error message lines (first line usually)
        if (
            line.trim().startsWith('Error:') ||
            line.trim().startsWith('TypeError:') ||
            line.trim().startsWith('ReferenceError:') ||
            line.trim().startsWith('SyntaxError:')
        ) {
            filtered.push(line);
            continue;
        }

        // Skip SDK internal frames
        const isSdkFrame = sdkPatterns.some((pattern) => pattern.test(line));
        if (isSdkFrame) continue;

        filtered.push(line);
    }

    // If we filtered everything except the error message, return original stack
    if (filtered.length <= 1) {
        return stack;
    }

    return filtered.join('\n');
}

/**
 * Clean stack trace by filtering SDK frames and resolving source maps
 * This resolves bundled/minified file paths to original source file paths
 *
 * @param stack - The stack trace string
 * @param timeoutMs - Maximum time to wait for source map resolution (default: 1000ms)
 * @returns Cleaned stack trace with resolved source file paths
 */
export async function cleanStackTrace(
    stack: string | undefined,
    timeoutMs: number = 1000
): Promise<string | undefined> {
    if (!stack) return undefined;

    // First, filter out SDK internal frames synchronously
    const filteredStack = filterSdkFrames(stack);
    if (!filteredStack) return undefined;

    // Check if we're in a browser environment (source map resolution only works in browser)
    const isBrowser =
        typeof window !== 'undefined' && typeof fetch !== 'undefined';
    if (!isBrowser) {
        // In Node.js, just return filtered stack
        return filteredStack;
    }

    // Try to resolve source maps with a timeout
    try {
        const resolvedStack = await Promise.race([
            resolveStackTrace(filteredStack),
            new Promise<string | undefined>((resolve) =>
                setTimeout(() => resolve(filteredStack), timeoutMs)
            )
        ]);
        return resolvedStack;
    } catch (error) {
        // If resolution fails, return filtered stack
        return filteredStack;
    }
}

/**
 * Synchronous version that filters SDK frames but doesn't resolve source maps
 * Use this when you need immediate results and can't wait for async resolution
 */
export function cleanStackTraceSync(
    stack: string | undefined
): string | undefined {
    return filterSdkFrames(stack);
}

export interface HealOpsLoggerConfig {
    apiKey: string;
    serviceName: string;
    endpoint?: string;
    source?: string;
    // Release tracking for source map resolution
    release?: string; // Git SHA, version, or build ID
    environment?: string; // production, staging, development
    // Batching configuration
    enableBatching?: boolean; // Default: true
    batchSize?: number; // Default: 50
    batchIntervalMs?: number; // Default: 1000ms
}

export interface LogPayload {
    service_name: string;
    severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
    message: string;
    source: string;
    timestamp?: string;
    release?: string; // Release identifier for source map resolution
    environment?: string; // Environment name
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

    // Guard flag to prevent recursive calls to getCallerInfo
    private isGettingCallerInfo: boolean = false;

    // Shutdown handlers (stored for cleanup)
    private shutdownHandlers: {
        beforeExit?: () => void;
        sigint?: () => void;
        sigterm?: () => void;
    } = {};

    constructor(config: HealOpsLoggerConfig) {
        this.config = config;
        this.endpoint = config.endpoint || 'https://engine.healops.ai';

        // Auto-detect release from meta tag if not provided (browser only)
        if (!this.config.release && typeof document !== 'undefined') {
            const metaRelease = document.querySelector(
                'meta[name="healops-release"]'
            );
            if (metaRelease) {
                this.config.release =
                    metaRelease.getAttribute('content') || undefined;
            }
        }

        // Auto-detect environment from meta tag if not provided (browser only)
        if (!this.config.environment && typeof document !== 'undefined') {
            const metaEnv = document.querySelector(
                'meta[name="healops-environment"]'
            );
            if (metaEnv) {
                this.config.environment =
                    metaEnv.getAttribute('content') || undefined;
            }
        }

        // Validate batching configuration
        this.BATCHING_ENABLED = config.enableBatching !== false; // Default: true
        this.BATCH_SIZE = Math.max(1, Math.min(config.batchSize || 50, 1000)); // Clamp between 1-1000
        this.BATCH_INTERVAL_MS = Math.max(
            100,
            Math.min(config.batchIntervalMs || 1000, 60000)
        ); // Clamp between 100ms-60s

        // Setup graceful shutdown handlers (with cleanup tracking)
        if (typeof process !== 'undefined' && process.on) {
            this.shutdownHandlers.beforeExit = () => {
                this.flushBatch().catch((err) => {
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
                process.removeListener(
                    'beforeExit',
                    this.shutdownHandlers.beforeExit
                );
            }
            if (this.shutdownHandlers.sigint) {
                process.removeListener('SIGINT', this.shutdownHandlers.sigint);
            }
            if (this.shutdownHandlers.sigterm) {
                process.removeListener(
                    'SIGTERM',
                    this.shutdownHandlers.sigterm
                );
            }
        }
    }

    /**
     * Extract caller information from stack trace
     * This works in browsers (Chrome, Firefox, Safari, Edge) and Node.js
     * Also extracts function name for OpenTelemetry semantic conventions
     *
     * This method intelligently skips SDK internal frames and known interceptors
     * (like Sentry) to find the actual caller code.
     */
    private getCallerInfo(): {
        filePath?: string;
        line?: number;
        column?: number;
        functionName?: string;
        fullStack?: string;
    } {
        // Prevent recursive calls - if we're already getting caller info, return empty
        if (this.isGettingCallerInfo) {
            return {};
        }

        this.isGettingCallerInfo = true;

        try {
            const stack = new Error().stack;
            if (!stack) return {};

            const stackLines = stack.split('\n');

            // Patterns to identify SDK/internal frames that should be skipped
            const skipPatterns = [
                /HealOpsLogger/,
                /healops-opentelemetry/,
                /getCallerInfo/,
                /sendLog/,
                /\.error\(/,
                /\.warn\(/,
                /\.info\(/,
                /\.critical\(/,
                /ConsoleInterceptor/,
                /NodeConsoleInterceptor/,
                /initHealOpsLogger/,
                /@sentry/,
                /sentry/,
                /instrument\.console/,
                /Error\s*$/ // Just "Error" line
            ];

            // Find the first stack line that's not from our SDK or known interceptors
            let callerLine: string | undefined = undefined;

            for (let i = 0; i < stackLines.length; i++) {
                const line = stackLines[i];
                if (!line || line.trim().length === 0) continue;

                // Skip error message lines
                if (
                    line.trim().startsWith('Error:') ||
                    line.trim().startsWith('TypeError:') ||
                    line.trim().startsWith('ReferenceError:') ||
                    line.trim().startsWith('SyntaxError:')
                ) {
                    continue;
                }

                // Check if this line should be skipped
                const shouldSkip = skipPatterns.some((pattern) =>
                    pattern.test(line)
                );
                if (shouldSkip) {
                    continue;
                }

                // Found a potential caller line - check if it has file path info
                if (
                    line.includes(':') &&
                    (line.match(/:\d+:\d+/) || line.match(/:\d+\)/))
                ) {
                    callerLine = line;
                    break;
                }
            }

            // Fallback: if we didn't find a caller, try the old approach with fixed indices
            if (!callerLine) {
                callerLine = stackLines[4] || stackLines[3] || stackLines[2];
            }

            if (!callerLine) return { fullStack: stack };

            // Chrome/Edge format: "at functionName (file:line:column)" or "at file:line:column"
            const chromeMatchWithFunction = callerLine.match(
                /at\s+([^(]+)\s+\(([^)]+):(\d+):(\d+)\)/
            );
            if (chromeMatchWithFunction) {
                const filePath = chromeMatchWithFunction[2].trim();
                const lineNum = parseInt(chromeMatchWithFunction[3], 10);
                const colNum = parseInt(chromeMatchWithFunction[4], 10);

                // Validate parsed values
                if (filePath && !isNaN(lineNum) && !isNaN(colNum)) {
                    return {
                        functionName: chromeMatchWithFunction[1].trim(),
                        filePath,
                        line: lineNum,
                        column: colNum,
                        fullStack: stack
                    };
                }
            }

            const chromeMatch =
                callerLine.match(/\(([^)]+):(\d+):(\d+)\)/) ||
                callerLine.match(/at\s+([^:]+):(\d+):(\d+)/);
            if (chromeMatch) {
                const filePath = chromeMatch[1].trim();
                const lineNum = parseInt(chromeMatch[2], 10);
                const colNum = parseInt(chromeMatch[3], 10);

                // Validate parsed values
                if (filePath && !isNaN(lineNum) && !isNaN(colNum)) {
                    return {
                        filePath,
                        line: lineNum,
                        column: colNum,
                        fullStack: stack
                    };
                }
            }

            // Firefox format: "functionName@file:line:column"
            const firefoxMatch = callerLine.match(
                /([^@]+)@([^:]+):(\d+):(\d+)/
            );
            if (firefoxMatch) {
                const filePath = firefoxMatch[2].trim();
                const lineNum = parseInt(firefoxMatch[3], 10);
                const colNum = parseInt(firefoxMatch[4], 10);

                // Validate parsed values
                if (filePath && !isNaN(lineNum) && !isNaN(colNum)) {
                    return {
                        functionName: firefoxMatch[1].trim(),
                        filePath,
                        line: lineNum,
                        column: colNum,
                        fullStack: stack
                    };
                }
            }

            // Node.js format: "at functionName (file:line:column)" or "at file:line:column"
            const nodeMatchWithFunction = callerLine.match(
                /at\s+([^(]+)\s+\(([^:)]+):(\d+):(\d+)\)/
            );
            if (nodeMatchWithFunction) {
                const filePath = nodeMatchWithFunction[2].trim();
                const lineNum = parseInt(nodeMatchWithFunction[3], 10);
                const colNum = parseInt(nodeMatchWithFunction[4], 10);

                // Validate parsed values
                if (filePath && !isNaN(lineNum) && !isNaN(colNum)) {
                    return {
                        functionName: nodeMatchWithFunction[1].trim(),
                        filePath,
                        line: lineNum,
                        column: colNum,
                        fullStack: stack
                    };
                }
            }

            const nodeMatch = callerLine.match(
                /at\s+(?:[^(]+)?\(?([^:)]+):(\d+):(\d+)\)?/
            );
            if (nodeMatch) {
                const filePath = nodeMatch[1].trim();
                const lineNum = parseInt(nodeMatch[2], 10);
                const colNum = parseInt(nodeMatch[3], 10);

                // Validate parsed values
                if (filePath && !isNaN(lineNum) && !isNaN(colNum)) {
                    return {
                        filePath,
                        line: lineNum,
                        column: colNum,
                        fullStack: stack
                    };
                }
            }

            return { fullStack: stack };
        } catch (e) {
            // Silent fail - don't break logging if stack parsing fails
            // Use process.stderr.write directly to avoid console interception recursion
            if (typeof process !== 'undefined' && process.env.HEALOPS_DEBUG) {
                try {
                    // Use process.stderr.write directly to bypass console interception
                    process.stderr.write(
                        `HealOps getCallerInfo error: ${
                            e instanceof Error ? e.message : String(e)
                        }\n`
                    );
                } catch {
                    // Ignore errors in error handling - completely silent
                }
            }
            return {};
        } finally {
            // Always reset the flag, even if an error occurred
            this.isGettingCallerInfo = false;
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

        // Prioritize original error stack over caller stack
        // Original error stacks are more useful than logger-internal stacks
        const rawStackTrace =
            metadata?.errorStack || // Original error stack (most reliable)
            metadata?.stack || // Generic stack
            metadata?.exception?.stacktrace || // Exception stacktrace
            callerInfo.fullStack; // Fallback to caller stack

        // Clean the stack trace to remove SDK internal frames and resolve source maps
        // This resolves bundled/minified file paths to original source file paths
        const cleanedStackTrace = await cleanStackTrace(rawStackTrace);

        // Resolve filePath from various sources
        // Priority: metadata.filePath > extracted from error stack trace > extracted from cleaned stack > callerInfo.filePath
        // IMPORTANT: Always prioritize the actual error location over SDK interceptor locations
        let resolvedFilePath: string | undefined = undefined;
        let fallbackFilePath: string | undefined = undefined;

        if (metadata?.filePath) {
            // Resolve filePath from metadata if it's a bundled file
            // Try to get source file path, but fallback to original if resolution fails
            resolvedFilePath = await resolveFilePath(
                metadata.filePath,
                metadata.line,
                metadata.column,
                true // Always return a path for traceability
            );
            // If metadata.filePath is already a source file (not bundled), use it
            if (
                !resolvedFilePath &&
                metadata.filePath &&
                isSourceFile(metadata.filePath)
            ) {
                resolvedFilePath = metadata.filePath;
            }
            // Keep original as fallback
            if (!resolvedFilePath) {
                fallbackFilePath = metadata.filePath;
            }
        }

        // PRIORITY: Extract from error stack trace first (most reliable - contains actual error location)
        // This is better than callerInfo which might point to our SDK's fetch interceptor
        if (
            !resolvedFilePath &&
            rawStackTrace &&
            (metadata?.errorStack ||
                metadata?.stack ||
                metadata?.exception?.stacktrace)
        ) {
            // Extract the first meaningful file path (skips SDK frames including window.fetch)
            const extractedPath = extractFilePathFromStack(rawStackTrace, true);
            if (extractedPath) {
                // Extract line and column from the stack trace for better resolution
                const stackLines = rawStackTrace.split('\n');
                let extractedLine: number | undefined = undefined;
                let extractedColumn: number | undefined = undefined;

                for (const line of stackLines) {
                    // Skip SDK frames and window.fetch interceptor
                    if (
                        line.includes('HealOpsLogger') ||
                        line.includes('window.fetch') ||
                        line.includes('healops-opentelemetry')
                    ) {
                        continue;
                    }

                    // Match patterns to find line/column for this file
                    const match =
                        line.match(/\(([^)]+):(\d+):(\d+)\)/) ||
                        line.match(/at\s+([^:]+):(\d+):(\d+)/) ||
                        line.match(/@([^:]+):(\d+):(\d+)/);

                    if (match) {
                        const filePath = match[1]?.trim();
                        if (filePath === extractedPath) {
                            extractedLine = parseInt(match[2], 10);
                            extractedColumn = parseInt(match[3], 10);
                            break;
                        }
                    }
                }

                // Try to resolve it if it's a bundled file (with line/column for better accuracy)
                resolvedFilePath = await resolveFilePath(
                    extractedPath,
                    extractedLine,
                    extractedColumn,
                    true
                );
                // If extracted path is already a source file, use it
                if (
                    !resolvedFilePath &&
                    extractedPath &&
                    isSourceFile(extractedPath)
                ) {
                    resolvedFilePath = extractedPath;
                }
                // Keep extracted path as fallback
                if (!resolvedFilePath && !fallbackFilePath) {
                    fallbackFilePath = extractedPath;
                }
            }
        }

        // If we don't have a resolved path yet, try extracting from cleaned stack trace
        // This has resolved source maps but might miss some frames
        if (!resolvedFilePath && cleanedStackTrace) {
            // Extract the first meaningful file path (skips SDK frames)
            const extractedPath = extractFilePathFromStack(
                cleanedStackTrace,
                true
            ); // Allow bundled files as fallback
            if (extractedPath) {
                // Extract line and column from the stack trace for better resolution
                const stackLines = cleanedStackTrace.split('\n');
                let extractedLine: number | undefined = undefined;
                let extractedColumn: number | undefined = undefined;

                for (const line of stackLines) {
                    // Match patterns to find line/column for this file
                    const match =
                        line.match(/\(([^)]+):(\d+):(\d+)\)/) ||
                        line.match(/at\s+([^:]+):(\d+):(\d+)/) ||
                        line.match(/@([^:]+):(\d+):(\d+)/);

                    if (match) {
                        const filePath = match[1]?.trim();
                        if (filePath === extractedPath) {
                            extractedLine = parseInt(match[2], 10);
                            extractedColumn = parseInt(match[3], 10);
                            break;
                        }
                    }
                }

                // Try to resolve it if it's a bundled file (with line/column for better accuracy)
                resolvedFilePath = await resolveFilePath(
                    extractedPath,
                    extractedLine,
                    extractedColumn,
                    true
                );
                // If extracted path is already a source file, use it
                if (
                    !resolvedFilePath &&
                    extractedPath &&
                    isSourceFile(extractedPath)
                ) {
                    resolvedFilePath = extractedPath;
                }
                // Keep extracted path as fallback
                if (!resolvedFilePath && !fallbackFilePath) {
                    fallbackFilePath = extractedPath;
                }
            }
        }

        // If still no path, try callerInfo (but only if we don't have error stack)
        // NOTE: callerInfo might point to our SDK's fetch interceptor, so we prioritize stack trace extraction
        // Only use callerInfo as a last resort
        if (
            !resolvedFilePath &&
            !fallbackFilePath &&
            callerInfo.filePath &&
            !metadata?.errorStack
        ) {
            // Skip callerInfo if it's from our SDK's fetch interceptor
            const isFetchInterceptor =
                callerInfo.filePath.includes('46bf396f3323cd2c.js') ||
                callerInfo.functionName === 'window.fetch';

            if (!isFetchInterceptor) {
                resolvedFilePath = await resolveFilePath(
                    callerInfo.filePath,
                    callerInfo.line,
                    callerInfo.column,
                    true // Always return a path for traceability
                );
                // If callerInfo.filePath is already a source file, use it
                if (
                    !resolvedFilePath &&
                    callerInfo.filePath &&
                    isSourceFile(callerInfo.filePath)
                ) {
                    resolvedFilePath = callerInfo.filePath;
                }
                // Keep callerInfo path as fallback
                if (!resolvedFilePath && !fallbackFilePath) {
                    fallbackFilePath = callerInfo.filePath;
                }
            }
        }

        // Last resort: extract from raw stack trace if available
        if (!resolvedFilePath && rawStackTrace) {
            const extractedPath = extractFilePathFromStack(rawStackTrace, true); // Allow bundled files as fallback
            if (extractedPath) {
                resolvedFilePath = await resolveFilePath(
                    extractedPath,
                    undefined,
                    undefined,
                    true
                );
                // If extracted path is already a source file, use it
                if (
                    !resolvedFilePath &&
                    extractedPath &&
                    isSourceFile(extractedPath)
                ) {
                    resolvedFilePath = extractedPath;
                }
                // Keep extracted path as fallback
                if (!resolvedFilePath && !fallbackFilePath) {
                    fallbackFilePath = extractedPath;
                }
            }
        }

        // Always use resolved source file path if available, otherwise use fallback
        // This ensures logs always have a file path for traceability
        const finalFilePath = resolvedFilePath || fallbackFilePath;

        // Extract line, column, and function name from caller info or metadata
        const finalLine = metadata?.line || callerInfo.line;
        const finalColumn = metadata?.column || callerInfo.column;
        const finalFunctionName =
            metadata?.functionName || callerInfo.functionName;

        const enrichedMetadata = {
            ...metadata,
            // Only include caller info if we don't have original error info
            ...(metadata?.errorStack ? {} : callerInfo),
            // Always include filePath if we have one (resolved or fallback)
            ...(finalFilePath ? { filePath: finalFilePath } : {}),
            // Include OpenTelemetry semantic convention attributes for source code information
            // See: https://opentelemetry.io/docs/specs/semconv/code/
            // These attributes follow OpenTelemetry standards and can be used by any OTel-compatible tool
            ...(finalFilePath
                ? {
                      'code.file.path': finalFilePath,
                      ...(finalLine ? { 'code.line.number': finalLine } : {}),
                      ...(finalColumn
                          ? { 'code.column.number': finalColumn }
                          : {}),
                      ...(finalFunctionName
                          ? { 'code.function.name': finalFunctionName }
                          : {})
                  }
                : {}),
            // Use cleaned stack trace
            stack: cleanedStackTrace,
            // Preserve original errorStack for reference
            ...(metadata?.errorStack
                ? { errorStack: metadata.errorStack }
                : {}),
            // Also include exception format for backend compatibility
            ...(cleanedStackTrace &&
            (severity === 'ERROR' || severity === 'CRITICAL')
                ? {
                      exception: {
                          type:
                              metadata?.errorName ||
                              metadata?.exception?.type ||
                              'Error',
                          message:
                              metadata?.errorMessage ||
                              metadata?.exception?.message ||
                              message,
                          stacktrace: cleanedStackTrace
                      }
                  }
                : {})
        };

        const payload: LogPayload = {
            service_name: this.config.serviceName,
            severity,
            message,
            source: this.config.source || 'healops-sdk',
            timestamp: new Date().toISOString(),
            // Include release and environment for server-side source map resolution
            ...(this.config.release ? { release: this.config.release } : {}),
            ...(this.config.environment
                ? { environment: this.config.environment }
                : {}),
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
            this.flushBatch().catch((err) => {
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
            this.flushBatch().catch((err) => {
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
                console.warn(
                    'Batch send failed, falling back to individual sends:',
                    error.message
                );
            }

            // Try sending individually (without awaiting to avoid blocking)
            for (const log of batch) {
                this.sendSingleLog(log).catch((err) => {
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
                (payload.severity === 'ERROR' ||
                    payload.severity === 'CRITICAL')
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
