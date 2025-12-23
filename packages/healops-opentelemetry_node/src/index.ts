import { NodeSDK } from '@opentelemetry/sdk-node';
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { Resource } from '@opentelemetry/resources';
import { SemanticResourceAttributes } from '@opentelemetry/semantic-conventions';
import { HealOpsExporter } from './HealOpsExporter';
import { HealOpsLogger } from './HealOpsLogger';
import { HealOpsConfig } from './types';

// ============================================================================
// EXPORTS - All existing exports remain unchanged for backward compatibility
// ============================================================================
export { HealOpsConfig, HealOpsExporter, HealOpsLogger };

/**
 * Universal configuration interface for the new init() function
 */
export interface UniversalConfig {
  apiKey: string;
  serviceName: string;
  endpoint?: string;

  // Advanced options (optional)
  captureConsole?: boolean;      // Default: true
  captureErrors?: boolean;        // Default: true
  captureTraces?: boolean;        // Default: true (Node.js only)
  debug?: boolean;                // Default: false
}

// ============================================================================
// EXISTING FUNCTIONS - Unchanged for backward compatibility
// ============================================================================

/**
 * Initialize HealOps OpenTelemetry SDK for automatic error span collection
 */
export function initHealOpsOTel(config: HealOpsConfig) {
  const exporter = new HealOpsExporter(config);

  const sdk = new NodeSDK({
    resource: new Resource({
      [SemanticResourceAttributes.SERVICE_NAME]: config.serviceName,
    }),
    traceExporter: exporter,
    spanProcessor: new BatchSpanProcessor(exporter, {
      // 5 second batch interval as required
      scheduledDelayMillis: 5000,
    }),
    instrumentations: [getNodeAutoInstrumentations()],
  });

  sdk.start();

  // Graceful shutdown
  process.on('SIGTERM', () => {
    sdk.shutdown()
      .then(() => console.log('HealOps OTel SDK terminated'))
      .catch((error) => console.error('Error terminating HealOps OTel SDK', error));
  });
}

/**
 * Create a HealOps logger for direct log ingestion
 */
export function createLogger(config: { apiKey: string; serviceName: string; endpoint?: string; source?: string }) {
  return new HealOpsLogger(config);
}

// ============================================================================
// NEW UNIVERSAL INIT FUNCTIONALITY
// ============================================================================

/**
 * Console interceptor for Node.js
 * Automatically captures all console.log, console.error, etc.
 */
class NodeConsoleInterceptor {
  private logger: HealOpsLogger;
  private originalConsole: {
    log: typeof console.log;
    info: typeof console.info;
    warn: typeof console.warn;
    error: typeof console.error;
    debug: typeof console.debug;
  };

  constructor(logger: HealOpsLogger) {
    this.logger = logger;
    this.originalConsole = {
      log: console.log.bind(console),
      info: console.info.bind(console),
      warn: console.warn.bind(console),
      error: console.error.bind(console),
      debug: console.debug.bind(console)
    };
  }

  start(): void {
    const formatArgs = (...args: any[]): string => {
      return args.map(arg => {
        if (typeof arg === 'object') {
          try {
            return JSON.stringify(arg, null, 2);
          } catch {
            return String(arg);
          }
        }
        return String(arg);
      }).join(' ');
    };

    const extractErrorMetadata = (...args: any[]): Record<string, any> => {
      const metadata: Record<string, any> = {};
      args.forEach((arg, index) => {
        if (arg instanceof Error) {
          metadata.errorName = arg.name;
          metadata.errorMessage = arg.message;
          metadata.errorStack = arg.stack;
        } else if (typeof arg === 'object' && arg !== null) {
          metadata[`arg${index}`] = arg;
        }
      });
      return metadata;
    };

    console.log = (...args: any[]) => {
      this.originalConsole.log(...args);
      this.logger.info(formatArgs(...args));
    };

    console.info = (...args: any[]) => {
      this.originalConsole.info(...args);
      this.logger.info(formatArgs(...args));
    };

    console.warn = (...args: any[]) => {
      this.originalConsole.warn(...args);
      this.logger.warn(formatArgs(...args));
    };

    console.error = (...args: any[]) => {
      this.originalConsole.error(...args);
      this.logger.error(formatArgs(...args), extractErrorMetadata(...args));
    };

    console.debug = (...args: any[]) => {
      this.originalConsole.debug(...args);
      this.logger.info(formatArgs(...args));
    };
  }

  stop(): void {
    console.log = this.originalConsole.log;
    console.info = this.originalConsole.info;
    console.warn = this.originalConsole.warn;
    console.error = this.originalConsole.error;
    console.debug = this.originalConsole.debug;
  }
}

/**
 * Setup global error handlers for Node.js
 */
function setupNodeErrorHandlers(logger: HealOpsLogger): void {
  process.on('uncaughtException', (error: Error) => {
    logger.critical(`Uncaught Exception: ${error.message}`, {
      errorName: error.name,
      errorMessage: error.message,
      errorStack: error.stack,
      type: 'uncaught_exception'
    });
  });

  process.on('unhandledRejection', (reason: any) => {
    const errorMessage = reason instanceof Error ? reason.message : String(reason);
    logger.critical(`Unhandled Promise Rejection: ${errorMessage}`, {
      errorName: reason?.name,
      errorMessage: reason instanceof Error ? reason.message : String(reason),
      errorStack: reason instanceof Error ? reason.stack : undefined,
      type: 'unhandled_promise_rejection',
      reason: reason
    });
  });
}

/**
 * UNIVERSAL INIT FUNCTION - ONE LINE SETUP FOR ALL ENVIRONMENTS
 *
 * Automatically detects environment (Node.js vs Browser) and configures accordingly.
 * Captures console logs, errors, traces, and integrates with popular frameworks.
 *
 * @example
 * // Single line setup - works in Fastify, Express, NestJS, Next.js, React, etc.
 * const healops = init({
 *   apiKey: process.env.HEALOPS_API_KEY,
 *   serviceName: 'my-app'
 * });
 *
 * // Advanced usage with options
 * const healops = init({
 *   apiKey: process.env.HEALOPS_API_KEY,
 *   serviceName: 'my-app',
 *   captureConsole: true,   // Capture all console.log, console.error, etc.
 *   captureErrors: true,    // Capture unhandled errors and rejections
 *   captureTraces: true,    // Capture HTTP/DB traces (Node.js only)
 *   debug: false            // Enable debug logging
 * });
 *
 * @param config - Universal configuration object
 * @returns HealOpsLogger instance for manual logging
 */
export function init(config: UniversalConfig): HealOpsLogger {
  const captureConsole = config.captureConsole !== false;  // Default: true
  const captureErrors = config.captureErrors !== false;    // Default: true
  const captureTraces = config.captureTraces !== false;    // Default: true

  // Detect environment
  const isBrowser = typeof window !== 'undefined' && typeof document !== 'undefined';

  if (isBrowser) {
    // BROWSER: Use existing browser initialization
    try {
      const { initHealOpsLogger } = require('./browser');
      return initHealOpsLogger(
        {
          apiKey: config.apiKey,
          serviceName: config.serviceName,
          endpoint: config.endpoint,
          source: 'browser'
        },
        captureConsole
      );
    } catch (e) {
      console.error('Failed to initialize HealOps for browser:', e);
      throw e;
    }
  } else {
    // NODE.JS: Enhanced initialization with auto-detection
    const logger = new HealOpsLogger({
      apiKey: config.apiKey,
      serviceName: config.serviceName,
      endpoint: config.endpoint,
      source: 'node'
    });

    // 1. Initialize OpenTelemetry (for HTTP/DB traces)
    if (captureTraces) {
      try {
        initHealOpsOTel({
          apiKey: config.apiKey,
          serviceName: config.serviceName,
          endpoint: config.endpoint
        });
        if (config.debug) {
          console.log('✓ HealOps OpenTelemetry initialized');
        }
      } catch (e) {
        console.error('Failed to initialize OpenTelemetry:', e);
      }
    }

    // 2. Setup console interception
    if (captureConsole) {
      const interceptor = new NodeConsoleInterceptor(logger);
      interceptor.start();
      (logger as any)._consoleInterceptor = interceptor;
      if (config.debug) {
        console.log('✓ HealOps console interception enabled');
      }
    }

    // 3. Setup global error handlers
    if (captureErrors) {
      setupNodeErrorHandlers(logger);
      if (config.debug) {
        console.log('✓ HealOps error handlers initialized');
      }
    }

    // Success message (only if not in debug mode)
    if (!config.debug) {
      console.log('✓ HealOps initialized', {
        environment: 'Node.js',
        serviceName: config.serviceName,
        features: {
          console: captureConsole,
          errors: captureErrors,
          traces: captureTraces
        }
      });
    }

    return logger;
  }
}
