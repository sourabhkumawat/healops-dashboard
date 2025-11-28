import { NodeSDK } from '@opentelemetry/sdk-node';
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { Resource } from '@opentelemetry/resources';
import { SemanticResourceAttributes } from '@opentelemetry/semantic-conventions';
import { HealOpsExporter } from './HealOpsExporter';
import { HealOpsLogger } from './HealOpsLogger';
import { HealOpsConfig } from './types';

export { HealOpsConfig, HealOpsExporter, HealOpsLogger };

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
