import { Span } from '@opentelemetry/api';

export interface HealOpsConfig {
  apiKey: string;
  serviceName: string;
  endpoint?: string; // Default: https://engine.healops.ai/otel/errors
}

export interface HealOpsSpanPayload {
  apiKey: string;
  serviceName: string;
  spans: HealOpsSpan[];
}

export interface HealOpsSpan {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  timestamp: number;
  startTime: number;
  endTime: number;
  attributes: Record<string, any>;
  events: HealOpsSpanEvent[];
  status: {
    code: number;
    message?: string;
  };
  resource: Record<string, any>;
}

export interface HealOpsSpanEvent {
  name: string;
  time: number;
  attributes?: Record<string, any>;
}
