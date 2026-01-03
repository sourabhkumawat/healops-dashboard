/**
 * Source Map Resolver
 *
 * Resolves stack traces from bundled/minified files back to original source files
 * using source maps. This is especially useful for Next.js and other bundled applications.
 */

import { SourceMapConsumer } from 'source-map';

// Cache for source map URLs (string -> string | null)
const sourceMapUrlCache = new Map<string, string | null>();
// Cache for source map consumers (string -> SourceMapConsumer | null)
const sourceMapCache = new Map<string, SourceMapConsumer | null>();

/**
 * Extract source map URL from a JavaScript file URL
 * Source maps are typically referenced via a comment: //# sourceMappingURL=file.js.map
 */
async function getSourceMapUrl(fileUrl: string): Promise<string | null> {
    try {
        // Check cache first
        if (sourceMapUrlCache.has(fileUrl)) {
            return sourceMapUrlCache.get(fileUrl) || null;
        }

        // Fetch the JavaScript file to find source map reference
        const response = await fetch(fileUrl);
        if (!response.ok) return null;

        const content = await response.text();

        // Look for source map comment: //# sourceMappingURL=... or //@ sourceMappingURL=...
        const sourceMapMatch = content.match(
            /\/\/[#@]\s*sourceMappingURL\s*=\s*([^\s]+)/
        );

        if (!sourceMapMatch) {
            sourceMapUrlCache.set(fileUrl, null);
            return null;
        }

        let sourceMapUrl = sourceMapMatch[1];

        // Resolve relative URLs
        if (sourceMapUrl.startsWith('/')) {
            const urlObj = new URL(fileUrl);
            sourceMapUrl = `${urlObj.origin}${sourceMapUrl}`;
        } else if (!sourceMapUrl.startsWith('http')) {
            const urlObj = new URL(fileUrl);
            const basePath = urlObj.pathname.substring(
                0,
                urlObj.pathname.lastIndexOf('/')
            );
            sourceMapUrl = `${urlObj.origin}${basePath}/${sourceMapUrl}`;
        }

        sourceMapUrlCache.set(fileUrl, sourceMapUrl);
        return sourceMapUrl;
    } catch (error) {
        // Silent fail - source map resolution is best effort
        return null;
    }
}

/**
 * Fetch and parse a source map
 */
async function getSourceMapConsumer(
    sourceMapUrl: string
): Promise<SourceMapConsumer | null> {
    try {
        // Check cache
        if (sourceMapCache.has(sourceMapUrl)) {
            return sourceMapCache.get(sourceMapUrl) || null;
        }

        const response = await fetch(sourceMapUrl);
        if (!response.ok) {
            sourceMapCache.set(sourceMapUrl, null);
            return null;
        }

        const sourceMapJson = await response.json();
        const consumer = await new SourceMapConsumer(sourceMapJson);
        sourceMapCache.set(sourceMapUrl, consumer);
        return consumer;
    } catch (error) {
        sourceMapCache.set(sourceMapUrl, null);
        return null;
    }
}

/**
 * Resolve a position in a bundled file to the original source file position
 * using source maps
 */
async function resolvePosition(
    fileUrl: string,
    line: number,
    column: number
): Promise<{ source: string; line: number; column: number } | null> {
    try {
        // Get source map URL
        const sourceMapUrl = await getSourceMapUrl(fileUrl);
        if (!sourceMapUrl) return null;

        // Get source map consumer
        const consumer = await getSourceMapConsumer(sourceMapUrl);
        if (!consumer) return null;

        // Resolve position using source map
        // Note: Source map line numbers are 1-based, but the API expects 0-based for originalPositionFor
        const originalPosition = consumer.originalPositionFor({
            line: line,
            column: column,
            bias: SourceMapConsumer.LEAST_UPPER_BOUND
        });

        if (!originalPosition.source) {
            return null;
        }

        // Clean up webpack:// prefix if present
        let sourceFile = originalPosition.source;
        if (sourceFile.startsWith('webpack://')) {
            sourceFile = sourceFile
                .replace(/^webpack:\/\/\.\//, '')
                .replace(/^webpack:\/\//, '');
        }

        // Return resolved position
        return {
            source: sourceFile,
            line: originalPosition.line || line,
            column: originalPosition.column || column
        };
    } catch (error) {
        // Silent fail
        return null;
    }
}

/**
 * Resolve a stack trace line from bundled file to original source
 * Format: "at functionName (file:line:column)" or "at file:line:column"
 */
async function resolveStackTraceLine(line: string): Promise<string> {
    // Extract file URL, line, and column from stack trace line
    const patterns = [
        // Chrome/Edge: "at functionName (file:line:column)"
        /at\s+(?:[^(]+)?\(([^:)]+):(\d+):(\d+)\)/,
        // Chrome/Edge: "at file:line:column"
        /at\s+([^:]+):(\d+):(\d+)/,
        // Firefox: "functionName@file:line:column"
        /@([^:]+):(\d+):(\d+)/
    ];

    for (const pattern of patterns) {
        const match = line.match(pattern);
        if (match) {
            const fileUrl = match[1].trim();
            const lineNum = parseInt(match[2]);
            const columnNum = parseInt(match[3]);

            // Only resolve if it looks like a bundled file
            if (
                fileUrl.includes('/_next/static/chunks/') ||
                fileUrl.includes('/_next/static/') ||
                fileUrl.includes('.min.js') ||
                fileUrl.match(/chunk-[a-f0-9]+\.js/)
            ) {
                const resolved = await resolvePosition(
                    fileUrl,
                    lineNum,
                    columnNum
                );
                if (resolved) {
                    // Replace the file URL in the stack trace line
                    return line.replace(fileUrl, resolved.source);
                }
            }
        }
    }

    return line;
}

/**
 * Resolve an entire stack trace from bundled files to original source files
 * This is an async operation but we'll process it in the background
 */
export async function resolveStackTrace(
    stack: string | undefined
): Promise<string | undefined> {
    if (!stack) return undefined;

    const lines = stack.split('\n');
    const resolvedLines: string[] = [];

    // Process lines in parallel for better performance
    const resolutionPromises = lines.map(async (line) => {
        // Keep error message lines as-is
        if (
            line.trim().startsWith('Error:') ||
            line.trim().startsWith('TypeError:') ||
            line.trim().startsWith('ReferenceError:') ||
            line.trim().startsWith('SyntaxError:')
        ) {
            return line;
        }

        // Try to resolve bundled file references
        return await resolveStackTraceLine(line);
    });

    const results = await Promise.all(resolutionPromises);
    return results.join('\n');
}

/**
 * Synchronous version that returns immediately with original stack
 * but triggers async resolution in the background
 * This is useful when we can't wait for async resolution
 */
export function resolveStackTraceAsync(
    stack: string | undefined,
    callback?: (resolved: string | undefined) => void
): string | undefined {
    if (!stack) return undefined;

    // Start async resolution in background
    if (callback) {
        resolveStackTrace(stack)
            .then(callback)
            .catch(() => {
                // Silent fail - return original stack
                callback(stack);
            });
    }

    // Return original stack immediately
    return stack;
}
