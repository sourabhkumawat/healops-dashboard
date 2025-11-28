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
    const payload: LogPayload = {
      service_name: this.config.serviceName,
      severity,
      message,
      source: this.config.source || 'healops-sdk',
      timestamp: new Date().toISOString(),
      metadata,
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
