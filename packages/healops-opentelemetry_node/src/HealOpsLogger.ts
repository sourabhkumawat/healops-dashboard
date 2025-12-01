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
    this.endpoint = config.endpoint || 'http://localhost:8000';
  }

  /**
   * Extract caller information from stack trace
   * This works in browsers (Chrome, Firefox, Safari, Edge)
   */
  private getCallerInfo(): { filePath?: string; line?: number; column?: number } {
    try {
      const stack = new Error().stack;
      if (!stack) return {};

      const stackLines = stack.split('\n');
      // Skip: "Error", "getCallerInfo", "sendLog", and the public method (info/warn/error/critical)
      // Line 4 is the actual caller
      const callerLine = stackLines[4] || stackLines[3];
      if (!callerLine) return {};

      // Chrome/Edge format: "at functionName (file:line:column)" or "at file:line:column"
      const chromeMatch = callerLine.match(/\(([^)]+):(\d+):(\d+)\)/) || 
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
    this.sendLog('INFO', message, metadata);
  }

  /**
   * Send a WARNING level log
   */
  warn(message: string, metadata?: Record<string, any>): void {
    this.sendLog('WARNING', message, metadata);
  }

  /**
   * Send an ERROR level log (will be persisted and may create incident)
   */
  error(message: string, metadata?: Record<string, any>): void {
    this.sendLog('ERROR', message, metadata);
  }

  /**
   * Send a CRITICAL level log (will be persisted and may create incident)
   */
  critical(message: string, metadata?: Record<string, any>): void {
    this.sendLog('CRITICAL', message, metadata);
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
      metadata: enrichedMetadata,
    };

    try {
      await axios.post(`${this.endpoint}/ingest/logs`, payload, {
        headers: {
          'X-HealOps-Key': this.config.apiKey,
          'Content-Type': 'application/json',
        },
        timeout: 3000,
      });
    } catch (error) {
      // Silent fail - don't break application if logging fails
      if (process.env.HEALOPS_DEBUG) {
        console.error('HealOps Logger failed to send log:', error);
      }
    }
  }
}
