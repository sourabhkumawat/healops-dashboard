import { SpanExporter, ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { ExportResult, ExportResultCode } from '@opentelemetry/core';
import { SpanStatusCode } from '@opentelemetry/api';
import axios from 'axios';
import { HealOpsConfig, HealOpsSpanPayload, HealOpsSpan } from './types';

export class HealOpsExporter implements SpanExporter {
  private config: HealOpsConfig;
  private endpoint: string;

  constructor(config: HealOpsConfig) {
    this.config = config;
    this.endpoint = config.endpoint || 'https://engine.healops.ai/otel/errors';
  }

  export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void {
    const errorSpans = spans.filter(span => this.isErrorSpan(span));

    if (errorSpans.length === 0) {
      return resultCallback({ code: ExportResultCode.SUCCESS });
    }

    const payload: HealOpsSpanPayload = {
      apiKey: this.config.apiKey,
      serviceName: this.config.serviceName,
      spans: errorSpans.map(span => this.transformSpan(span)),
    };

    this.send(payload)
      .then(() => resultCallback({ code: ExportResultCode.SUCCESS }))
      .catch((error) => {
        console.error('Failed to export spans to HealOps:', error.message);
        resultCallback({ code: ExportResultCode.FAILED, error });
      });
  }

  shutdown(): Promise<void> {
    return Promise.resolve();
  }

  private isErrorSpan(span: ReadableSpan): boolean {
    // Check for Error status code
    if (span.status.code === SpanStatusCode.ERROR) {
      return true;
    }

    // Check for exception events
    const hasExceptionEvent = span.events.some(event => event.name === 'exception');
    if (hasExceptionEvent) {
      return true;
    }

    // Check for exception attributes
    const attributes = span.attributes;
    if (attributes['exception.type'] || attributes['exception.message'] || attributes['exception.stacktrace']) {
      return true;
    }

    return false;
  }

  private transformSpan(span: ReadableSpan): HealOpsSpan {
    return {
      traceId: span.spanContext().traceId,
      spanId: span.spanContext().spanId,
      parentSpanId: span.parentSpanId,
      name: span.name,
      timestamp: Date.now(), // Current time of export, or use span end time? Requirement says timestamp, let's use endTime converted to ms
      startTime: this.hrTimeToMilliseconds(span.startTime),
      endTime: this.hrTimeToMilliseconds(span.endTime),
      attributes: span.attributes,
      events: span.events.map(event => ({
        name: event.name,
        time: this.hrTimeToMilliseconds(event.time),
        attributes: event.attributes,
      })),
      status: {
        code: span.status.code,
        message: span.status.message,
      },
      resource: span.resource.attributes,
    };
  }

  private hrTimeToMilliseconds(hrTime: [number, number]): number {
    return hrTime[0] * 1000 + hrTime[1] / 1e6;
  }

  private async send(payload: HealOpsSpanPayload, attempt = 1): Promise<void> {
    const maxRetries = 3;
    const timeout = 3000; // 3 seconds

    try {
      await axios.post(this.endpoint, payload, {
        headers: {
          'Content-Type': 'application/json',
          'User-Agent': 'HealOps-OTel-SDK/1.0',
        },
        timeout: timeout,
      });
    } catch (error) {
      if (attempt < maxRetries) {
        const delay = Math.pow(2, attempt) * 100; // Exponential backoff
        await new Promise(resolve => setTimeout(resolve, delay));
        return this.send(payload, attempt + 1);
      }
      throw error;
    }
  }
}
