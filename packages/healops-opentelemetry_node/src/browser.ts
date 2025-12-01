import { HealOpsLogger, type HealOpsLoggerConfig } from './HealOpsLogger';

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
      info: console.info.bind(console),
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
      this.logger.error(this.formatMessage(args), this.extractErrorMetadata(args));
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
      .map(arg => {
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
      } else if (typeof arg === 'object' && arg !== null) {
        metadata[`arg${index}`] = arg;
      }
    });
    
    return metadata;
  }
}

/**
 * Initialize HealOps logger with automatic console interception
 */
export function initHealOpsLogger(config: HealOpsLoggerConfig, interceptConsole = true): HealOpsLogger {
  const logger = new HealOpsLogger(config);
  
  if (interceptConsole) {
    const interceptor = new ConsoleInterceptor(logger);
    interceptor.start();
    
    // Store interceptor on logger for potential cleanup
    (logger as any)._interceptor = interceptor;
  }
  
  return logger;
}

// Re-export from HealOpsLogger
export { HealOpsLogger } from './HealOpsLogger';
export type { HealOpsLoggerConfig, LogPayload } from './HealOpsLogger';
