import {
    HealOpsLogger,
    type HealOpsLoggerConfig,
    cleanStackTrace
} from './HealOpsLogger';

/**
 * Console interceptor that automatically sends console logs to HealOps
 */
export class ConsoleInterceptor {
    private logger: HealOpsLogger;
    private originalConsole: {
        log: typeof console.log;
        warn: typeof console.warn;
        error: typeof console.error;
        info: typeof console.info;
    };

    constructor(logger: HealOpsLogger) {
        this.logger = logger;

        // Store original console methods
        this.originalConsole = {
            log: console.log.bind(console),
            warn: console.warn.bind(console),
            error: console.error.bind(console),
            info: console.info.bind(console)
        };
    }

    /**
     * Start intercepting console methods
     */
    start(): void {
        // Intercept console.log
        console.log = (...args: any[]) => {
            this.originalConsole.log(...args);
            this.logger.info(this.formatMessage(args));
        };

        // Intercept console.info
        console.info = (...args: any[]) => {
            this.originalConsole.info(...args);
            this.logger.info(this.formatMessage(args));
        };

        // Intercept console.warn
        console.warn = (...args: any[]) => {
            this.originalConsole.warn(...args);
            this.logger.warn(this.formatMessage(args));
        };

        // Intercept console.error
        console.error = (...args: any[]) => {
            this.originalConsole.error(...args);
            this.logger.error(
                this.formatMessage(args),
                this.extractErrorMetadata(args)
            );
        };
    }

    /**
     * Stop intercepting and restore original console methods
     */
    stop(): void {
        console.log = this.originalConsole.log;
        console.warn = this.originalConsole.warn;
        console.error = this.originalConsole.error;
        console.info = this.originalConsole.info;
    }

    /**
     * Format console arguments into a single message string
     */
    private formatMessage(args: any[]): string {
        return args
            .map((arg) => {
                if (typeof arg === 'object') {
                    try {
                        return JSON.stringify(arg);
                    } catch {
                        return String(arg);
                    }
                }
                return String(arg);
            })
            .join(' ');
    }

    /**
     * Extract error metadata from console.error arguments
     */
    private extractErrorMetadata(args: any[]): Record<string, any> {
        const metadata: Record<string, any> = {};

        args.forEach((arg, index) => {
            if (arg instanceof Error) {
                metadata.errorName = arg.name;
                metadata.errorMessage = arg.message;
                metadata.errorStack = arg.stack;
                metadata.stack = arg.stack; // Also include as 'stack' for backend extraction
                // Include exception format for backend compatibility
                metadata.exception = {
                    type: arg.name,
                    message: arg.message,
                    stacktrace: arg.stack || ''
                };
            } else if (typeof arg === 'object' && arg !== null) {
                metadata[`arg${index}`] = arg;
            }
        });

        return metadata;
    }
}

/**
 * Initialize HealOps logger with automatic console interception and global error handlers
 */
export function initHealOpsLogger(
    config: HealOpsLoggerConfig,
    interceptConsole = true
): HealOpsLogger {
    const logger = new HealOpsLogger(config);

    if (interceptConsole) {
        const interceptor = new ConsoleInterceptor(logger);
        interceptor.start();

        // Store interceptor on logger for potential cleanup
        (logger as any)._interceptor = interceptor;
    }

    // Set up global error handlers to catch unhandled errors
    let errorHandlersSetup = false;
    if (typeof window !== 'undefined') {
        // Catch unhandled JavaScript errors
        window.addEventListener('error', (event) => {
            // Capture full stack trace - prefer error.stack, fallback to constructing from event
            let fullStack = event.error?.stack;
            if (!fullStack && event.filename) {
                // Construct a basic stack trace if error.stack is not available
                fullStack = `Error: ${event.message}\n    at ${event.filename}:${event.lineno}:${event.colno}`;
            }

            logger.error(`Unhandled Error: ${event.message}`, {
                errorName: event.error?.name || 'Error',
                errorMessage: event.error?.message || event.message,
                errorStack: fullStack,
                stack: fullStack, // Also include as 'stack' for backend extraction
                exception: {
                    type: event.error?.name || 'Error',
                    message: event.error?.message || event.message,
                    stacktrace: fullStack || ''
                },
                filename: event.filename,
                lineno: event.lineno,
                colno: event.colno,
                type: 'unhandled_error'
            });
        });

        // Catch unhandled promise rejections
        window.addEventListener('unhandledrejection', (event) => {
            const reason = event.reason;
            const errorMessage =
                reason instanceof Error ? reason.message : String(reason);
            const errorStack =
                reason instanceof Error ? reason.stack : undefined;

            logger.error(`Unhandled Promise Rejection: ${errorMessage}`, {
                errorName: reason?.name || 'UnhandledPromiseRejection',
                errorMessage: errorMessage,
                errorStack: errorStack,
                stack: errorStack, // Also include as 'stack' for backend extraction
                exception: {
                    type: reason?.name || 'UnhandledPromiseRejection',
                    message: errorMessage,
                    stacktrace: errorStack || String(reason)
                },
                type: 'unhandled_promise_rejection',
                reason: reason
            });
        });

        // Catch fetch/network errors by intercepting fetch
        const originalFetch = window.fetch;
        window.fetch = async (...args) => {
            // Extract URL and method from fetch arguments
            const getUrl = (input: RequestInfo | URL): string => {
                if (typeof input === 'string') return input;
                if (input instanceof URL) return input.toString();
                if (input instanceof Request) return input.url;
                return String(input);
            };

            const getMethod = (input: RequestInfo | URL): string => {
                if (typeof input === 'string') return 'GET';
                if (input instanceof URL) return 'GET';
                if (input instanceof Request) return input.method || 'GET';
                return 'GET';
            };

            const url = getUrl(args[0]);
            const method = getMethod(args[0]);

            try {
                const response = await originalFetch(...args);

                // Log failed HTTP requests (4xx, 5xx)
                if (!response.ok && response.status >= 400) {
                    // Capture stack trace from the calling context (where fetch was called)
                    // Use Error.captureStackTrace if available to exclude our interceptor
                    let stackTrace: string | undefined;
                    try {
                        const error = new Error();
                        // In V8 (Chrome/Node), we can use captureStackTrace to exclude frames
                        if (Error.captureStackTrace) {
                            Error.captureStackTrace(error, window.fetch);
                        }
                        stackTrace = error.stack;
                    } catch (e) {
                        // Fallback: create error normally
                        stackTrace = new Error().stack;
                    }

                    // Clean the stack trace to remove SDK internal frames
                    const cleanedStack = cleanStackTrace(stackTrace);

                    logger.error(
                        `HTTP Error: ${response.status} ${response.statusText}`,
                        {
                            url,
                            status: response.status,
                            statusText: response.statusText,
                            method,
                            type: 'http_error',
                            stack: cleanedStack, // Include cleaned stack trace
                            exception: {
                                type: 'HTTPError',
                                message: `${response.status} ${response.statusText}`,
                                stacktrace: cleanedStack || stackTrace || ''
                            }
                        }
                    );
                }

                return response;
            } catch (error) {
                // Log network errors (connection failures, timeouts, etc.)
                const errorMessage =
                    error instanceof Error ? error.message : String(error);
                const errorStack =
                    error instanceof Error ? error.stack : undefined;

                logger.error(`Network Error: ${errorMessage}`, {
                    url,
                    errorName:
                        error instanceof Error ? error.name : 'NetworkError',
                    errorMessage: errorMessage,
                    errorStack: errorStack,
                    stack: errorStack, // Also include as 'stack' for backend extraction
                    exception: {
                        type:
                            error instanceof Error
                                ? error.name
                                : 'NetworkError',
                        message: errorMessage,
                        stacktrace: errorStack || String(error)
                    },
                    type: 'network_error'
                });
                throw error;
            }
        };

        // Store original fetch for cleanup
        (logger as any)._originalFetch = originalFetch;
        errorHandlersSetup = true;
    }

    // Log successful initialization
    console.log('✓ HealOps client-side error tracking initialized', {
        serviceName: config.serviceName,
        endpoint: config.endpoint || 'https://engine.healops.ai',
        interceptConsole,
        errorHandlersSetup: typeof window !== 'undefined'
    });

    return logger;
}

// Re-export from HealOpsLogger
export { HealOpsLogger } from './HealOpsLogger';
export type { HealOpsLoggerConfig, LogPayload } from './HealOpsLogger';
