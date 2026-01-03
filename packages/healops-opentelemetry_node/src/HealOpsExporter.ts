import { SpanExporter, ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { ExportResult, ExportResultCode } from '@opentelemetry/core';
import { SpanStatusCode } from '@opentelemetry/api';
import axios from 'axios';
import { HealOpsConfig, HealOpsSpanPayload, HealOpsSpan } from './types';
import { extractFilePathFromStack, resolveFilePath } from './sourceMapResolver';

/**
 * HealOps OpenTelemetry Exporter
 * 
 * Captures spans with full exception stack traces for source file path extraction.
 * The backend will extract source file paths from exception.stacktrace attributes
 * using regex patterns to identify original source files from bundled/minified code.
 * 
 * Note: For better source file resolution in bundled applications (Next.js, webpack, etc.),
 * consider implementing source map support in the future to map bundled paths back to
 * original source files.
 */

export class HealOpsExporter implements SpanExporter {
  private config: HealOpsConfig;
  private endpoint: string;

  constructor(config: HealOpsConfig) {
    this.config = config;
    this.endpoint = config.endpoint || 'https://engine.healops.ai/otel/errors';
  }

  export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void {
    if (spans.length === 0) {
      return resultCallback({ code: ExportResultCode.SUCCESS });
    }

    // Transform spans asynchronously to extract code attributes from stack traces
    Promise.all(spans.map(span => this.transformSpan(span)))
      .then(transformedSpans => {
        const payload: HealOpsSpanPayload = {
          apiKey: this.config.apiKey,
          serviceName: this.config.serviceName,
          spans: transformedSpans,
        };

        return this.send(payload);
      })
      .then(() => resultCallback({ code: ExportResultCode.SUCCESS }))
      .catch((error) => {
        console.error('Failed to export spans to HealOps:', error.message);
        resultCallback({ code: ExportResultCode.FAILED, error });
      });
  }

  shutdown(): Promise<void> {
    return Promise.resolve();
  }

  // isErrorSpan method removed as we now export all spans

  private async transformSpan(span: ReadableSpan): Promise<HealOpsSpan> {
    // Enhance attributes with exception information if available
    let enhancedAttributes = { ...span.attributes };
    
    // Extract stack trace from various sources
    let stackTrace: string | undefined = undefined;
    
    // Check if there's exception information in events that should be in attributes
    for (const event of span.events) {
      if (event.name === 'exception' && event.attributes) {
        // Ensure exception.stacktrace is properly captured
        if (event.attributes['exception.stacktrace']) {
          const exceptionStacktrace = String(event.attributes['exception.stacktrace']);
          enhancedAttributes['exception.stacktrace'] = exceptionStacktrace;
          stackTrace = exceptionStacktrace;
        }
        if (event.attributes['exception.type']) {
          enhancedAttributes['exception.type'] = event.attributes['exception.type'];
        }
        if (event.attributes['exception.message']) {
          enhancedAttributes['exception.message'] = event.attributes['exception.message'];
        }
      }
    }
    
    // If span has error status but no exception event, try to extract from attributes
    if (!stackTrace && span.status.code === SpanStatusCode.ERROR) {
      // OpenTelemetry attributes can be various types, convert to string
      const errorStack = enhancedAttributes['error.stack'];
      const stack = enhancedAttributes['stack'];
      const errorStackAttr = enhancedAttributes['errorStack'];
      
      stackTrace = (typeof errorStack === 'string' ? errorStack : undefined) ||
                   (typeof stack === 'string' ? stack : undefined) ||
                   (typeof errorStackAttr === 'string' ? errorStackAttr : undefined);
      
      if (stackTrace) {
        enhancedAttributes['exception.stacktrace'] = stackTrace;
      }
    }
    
    // Extract OpenTelemetry semantic convention attributes (code.file.path, code.line.number, etc.)
    // from stack traces if they're not already present in attributes
    // This ensures we always have source file information for traceability
    // See: https://opentelemetry.io/docs/specs/semconv/code/
    if (!enhancedAttributes['code.file.path'] && stackTrace) {
      try {
        // Extract file path from stack trace
        const extractedPath = extractFilePathFromStack(stackTrace, true);
        if (extractedPath) {
          // Extract line and column numbers from the first matching stack trace line
          const stackLines = stackTrace.split('\n');
          let lineNum: number | undefined = undefined;
          let columnNum: number | undefined = undefined;
          let functionName: string | undefined = undefined;
          
          for (const line of stackLines) {
            // Skip error message lines
            if (
              line.trim().startsWith('Error:') ||
              line.trim().startsWith('TypeError:') ||
              line.trim().startsWith('ReferenceError:') ||
              line.trim().startsWith('SyntaxError:')
            ) {
              continue;
            }
            
            // Match patterns: "at functionName (file:line:column)" or "at file:line:column"
            const matchWithFunction = line.match(/at\s+([^(]+)\s+\(([^)]+):(\d+):(\d+)\)/);
            if (matchWithFunction) {
              const filePath = matchWithFunction[2]?.trim();
              if (filePath === extractedPath) {
                functionName = matchWithFunction[1]?.trim();
                lineNum = parseInt(matchWithFunction[3], 10);
                columnNum = parseInt(matchWithFunction[4], 10);
                break;
              }
            }
            
            const match = line.match(/\(([^)]+):(\d+):(\d+)\)/) || 
                         line.match(/at\s+([^:]+):(\d+):(\d+)/) ||
                         line.match(/@([^:]+):(\d+):(\d+)/);
            
            if (match) {
              const filePath = match[1]?.trim();
              if (filePath === extractedPath) {
                lineNum = parseInt(match[2], 10);
                columnNum = parseInt(match[3], 10);
                
                // Try to extract function name
                if (!functionName) {
                  const funcMatch = line.match(/at\s+([^(]+)\s+\(/);
                  if (funcMatch) {
                    functionName = funcMatch[1].trim();
                  }
                }
                break;
              }
            }
          }
          
          // Try to resolve bundled files to source files using source maps
          const resolvedPath = await resolveFilePath(
            extractedPath, 
            lineNum, 
            columnNum, 
            true // Always return a path for traceability
          );
          
          if (resolvedPath) {
            enhancedAttributes['code.file.path'] = resolvedPath;
            
            // Only set line/column if we successfully extracted them
            if (lineNum !== undefined && !isNaN(lineNum) && lineNum > 0) {
              enhancedAttributes['code.line.number'] = lineNum;
            }
            if (columnNum !== undefined && !isNaN(columnNum) && columnNum >= 0) {
              enhancedAttributes['code.column.number'] = columnNum;
            }
            if (functionName) {
              enhancedAttributes['code.function.name'] = functionName;
            }
          } else if (extractedPath) {
            // If resolution failed, still use the extracted path (might be a chunk path)
            // This ensures we always have a file path for traceability
            enhancedAttributes['code.file.path'] = extractedPath;
            if (lineNum !== undefined && !isNaN(lineNum) && lineNum > 0) {
              enhancedAttributes['code.line.number'] = lineNum;
            }
            if (columnNum !== undefined && !isNaN(columnNum) && columnNum >= 0) {
              enhancedAttributes['code.column.number'] = columnNum;
            }
            if (functionName) {
              enhancedAttributes['code.function.name'] = functionName;
            }
          }
        }
      } catch (error) {
        // Silent fail - code attribute extraction is best effort
        // Don't break span export if extraction fails
      }
    }
    
    return {
      traceId: span.spanContext().traceId,
      spanId: span.spanContext().spanId,
      parentSpanId: span.parentSpanId,
      name: span.name,
      timestamp: Date.now(), // Current time of export, or use span end time? Requirement says timestamp, let's use endTime converted to ms
      startTime: this.hrTimeToMilliseconds(span.startTime),
      endTime: this.hrTimeToMilliseconds(span.endTime),
      attributes: enhancedAttributes,
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
