/**
 * Source Map Resolver
 *
 * Resolves stack traces from bundled/minified files back to original source files
 * using source maps. This is especially useful for Next.js and other bundled applications.
 */

import { SourceMapConsumer } from 'source-map';

// Cache for source map URLs (string -> string | null)
// Limited to prevent memory leaks in long-running applications
const sourceMapUrlCache = new Map<string, string | null>();
const MAX_CACHE_SIZE = 1000; // Limit cache size

// Cache for source map consumers (string -> SourceMapConsumer | null)
const sourceMapCache = new Map<string, SourceMapConsumer | null>();

/**
 * Trim cache if it exceeds maximum size (FIFO eviction)
 */
function trimCache<K, V>(cache: Map<K, V>, maxSize: number): void {
    if (cache.size > maxSize) {
        const entriesToRemove = cache.size - maxSize;
        const keysToRemove = Array.from(cache.keys()).slice(0, entriesToRemove);
        for (const key of keysToRemove) {
            cache.delete(key);
        }
    }
}

/**
 * Check if a file path is a bundled/chunk file
 */
function isBundledFile(filePath: string): boolean {
    return (
        filePath.includes('/_next/static/chunks/') ||
        filePath.includes('/_next/static/') ||
        filePath.includes('.min.js') ||
        !!filePath.match(/chunk-[a-f0-9]+\.js/) ||
        (filePath.startsWith('http') && filePath.includes('/_next/'))
    );
}

/**
 * Check if a file path is a source file (not bundled)
 */
export function isSourceFile(filePath: string): boolean {
    return !isBundledFile(filePath);
}

/**
 * Extract source map URL from a JavaScript file URL
 * Source maps are typically referenced via a comment: //# sourceMappingURL=file.js.map
 */
async function getSourceMapUrl(fileUrl: string): Promise<string | null> {
    const DEBUG = typeof process !== 'undefined' && process.env.HEALOPS_DEBUG_SOURCEMAPS === 'true';

    try {
        // Validate input
        if (!fileUrl || typeof fileUrl !== 'string' || fileUrl.length === 0) {
            if (DEBUG) console.log('[SourceMap] Invalid fileUrl:', fileUrl);
            return null;
        }

        // Check cache first
        if (sourceMapUrlCache.has(fileUrl)) {
            const cached = sourceMapUrlCache.get(fileUrl) || null;
            if (DEBUG) console.log('[SourceMap] Cache hit for', fileUrl, ':', cached ? 'found' : 'null (failed previously)');
            return cached;
        }

        if (DEBUG) console.log('[SourceMap] Fetching JS file to find source map reference:', fileUrl);

        // Try to fetch the JavaScript file to find source map reference
        // Use a timeout to avoid hanging
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);

        try {
            const response = await fetch(fileUrl, {
                signal: controller.signal,
                // Add credentials for same-origin requests
                credentials: 'omit'
            });
            clearTimeout(timeoutId);

            if (!response.ok) {
                if (DEBUG) console.warn('[SourceMap] Failed to fetch JS file:', fileUrl, 'Status:', response.status);
                trimCache(sourceMapUrlCache, MAX_CACHE_SIZE);
                sourceMapUrlCache.set(fileUrl, null);
                return null;
            }

            const content = await response.text();

            // Look for source map comment: //# sourceMappingURL=... or //@ sourceMappingURL=...
            const sourceMapMatch = content.match(
                /\/\/[#@]\s*sourceMappingURL\s*=\s*([^\s]+)/
            );

            if (!sourceMapMatch) {
                // No source map reference found in the file
                // Don't try to construct URLs - this causes 404s
                // Source maps are likely not available in this production build
                if (DEBUG) {
                    console.warn('[SourceMap] ❌ No sourceMappingURL comment found in file:', fileUrl);
                    console.warn('[SourceMap] This usually means source maps are not deployed to production.');
                    console.warn('[SourceMap] To fix: Enable source maps in your Next.js config (see docs)');
                }
                trimCache(sourceMapUrlCache, MAX_CACHE_SIZE);
                sourceMapUrlCache.set(fileUrl, null);
                return null;
            }

            let sourceMapUrl = sourceMapMatch[1];
            if (DEBUG) console.log('[SourceMap] Found sourceMappingURL:', sourceMapUrl);

            // Handle data URLs (inline source maps)
            if (sourceMapUrl.startsWith('data:')) {
                // Inline source maps - decode base64 data URL
                try {
                    const base64Match = sourceMapUrl.match(
                        /data:application\/json;base64,(.+)/
                    );
                    if (base64Match) {
                        // Store the inline source map data directly
                        const decoded = atob(base64Match[1]);
                        const sourceMapJson = JSON.parse(decoded);
                        // Create a special cache entry for inline maps
                        sourceMapUrlCache.set(
                            fileUrl,
                            `inline:${sourceMapUrl}`
                        );
                        return `inline:${sourceMapUrl}`;
                    }
                } catch (e) {
                    // Failed to decode inline source map
                }
                trimCache(sourceMapUrlCache, MAX_CACHE_SIZE);
                sourceMapUrlCache.set(fileUrl, null);
                return null;
            }

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

            // Cache the source map URL - we'll verify it exists when we actually need it
            // This avoids making unnecessary HEAD requests that show up as 404s
            if (DEBUG) console.log('[SourceMap] ✓ Resolved source map URL:', sourceMapUrl);
            trimCache(sourceMapUrlCache, MAX_CACHE_SIZE);
            sourceMapUrlCache.set(fileUrl, sourceMapUrl);
            return sourceMapUrl;
        } catch (fetchError: any) {
            clearTimeout(timeoutId);
            // If fetch fails, don't try to construct URLs - this causes 404s
            // Source maps are likely not available
            if (DEBUG) console.warn('[SourceMap] Fetch error for JS file:', fileUrl, fetchError.message);
            trimCache(sourceMapUrlCache, MAX_CACHE_SIZE);
            sourceMapUrlCache.set(fileUrl, null);
            return null;
        }
    } catch (error: any) {
        // Silent fail - source map resolution is best effort
        if (DEBUG) console.error('[SourceMap] Unexpected error:', error?.message);
        trimCache(sourceMapUrlCache, MAX_CACHE_SIZE);
        sourceMapUrlCache.set(fileUrl, null);
        return null;
    }
}

/**
 * Fetch and parse a source map
 */
async function getSourceMapConsumer(
    sourceMapUrl: string
): Promise<SourceMapConsumer | null> {
    const DEBUG = typeof process !== 'undefined' && process.env.HEALOPS_DEBUG_SOURCEMAPS === 'true';

    try {
        // Validate input
        if (
            !sourceMapUrl ||
            typeof sourceMapUrl !== 'string' ||
            sourceMapUrl.length === 0
        ) {
            if (DEBUG) console.log('[SourceMap] Invalid sourceMapUrl');
            return null;
        }

        // Check cache
        if (sourceMapCache.has(sourceMapUrl)) {
            const cached = sourceMapCache.get(sourceMapUrl) || null;
            if (DEBUG) console.log('[SourceMap] Consumer cache hit:', cached ? 'found' : 'null');
            return cached;
        }

        if (DEBUG) console.log('[SourceMap] Fetching source map:', sourceMapUrl);

        // Handle inline source maps (data URLs)
        if (sourceMapUrl.startsWith('inline:data:')) {
            try {
                const dataUrl = sourceMapUrl.replace('inline:', '');
                const base64Match = dataUrl.match(
                    /data:application\/json;base64,(.+)/
                );
                if (base64Match) {
                    const decoded = atob(base64Match[1]);
                    const sourceMapJson = JSON.parse(decoded);
                    const consumer = await new SourceMapConsumer(sourceMapJson);
                    trimCache(sourceMapCache, MAX_CACHE_SIZE);
                    sourceMapCache.set(sourceMapUrl, consumer);
                    return consumer;
                }
            } catch (e) {
                trimCache(sourceMapCache, MAX_CACHE_SIZE);
                sourceMapCache.set(sourceMapUrl, null);
                return null;
            }
        }

        // Use a timeout to avoid hanging
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);

        try {
            const response = await fetch(sourceMapUrl, {
                signal: controller.signal,
                credentials: 'omit'
            });
            clearTimeout(timeoutId);

            if (!response.ok) {
                // If 404, mark as failed and don't retry
                // This prevents repeated 404 errors
                if (response.status === 404) {
                    if (DEBUG) {
                        console.warn('[SourceMap] ❌ Source map file not found (404):', sourceMapUrl);
                        console.warn('[SourceMap] The sourceMappingURL points to a file that was not deployed.');
                        console.warn('[SourceMap] Make sure to deploy .map files to production or use inline source maps.');
                    }
                    trimCache(sourceMapCache, MAX_CACHE_SIZE);
                    sourceMapCache.set(sourceMapUrl, null);
                    // Also invalidate the URL cache to prevent future attempts
                    // Find and remove the entry that points to this invalid URL
                    for (const [key, value] of sourceMapUrlCache.entries()) {
                        if (value === sourceMapUrl) {
                            sourceMapUrlCache.set(key, null);
                            break;
                        }
                    }
                    return null;
                }

                // For other errors, also cache as failed
                if (DEBUG) console.warn('[SourceMap] Failed to fetch source map:', response.status);
                trimCache(sourceMapCache, MAX_CACHE_SIZE);
                sourceMapCache.set(sourceMapUrl, null);
                return null;
            }

            const sourceMapJson = await response.json();
            const consumer = await new SourceMapConsumer(sourceMapJson);
            if (DEBUG) console.log('[SourceMap] ✓ Successfully parsed source map');
            trimCache(sourceMapCache, MAX_CACHE_SIZE);
            sourceMapCache.set(sourceMapUrl, consumer);
            return consumer;
        } catch (fetchError: any) {
            clearTimeout(timeoutId);
            trimCache(sourceMapCache, MAX_CACHE_SIZE);
            sourceMapCache.set(sourceMapUrl, null);
            return null;
        }
    } catch (error) {
        trimCache(sourceMapCache, MAX_CACHE_SIZE);
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
        // Validate inputs
        if (!fileUrl || typeof fileUrl !== 'string') {
            return null;
        }
        if (
            typeof line !== 'number' ||
            typeof column !== 'number' ||
            isNaN(line) ||
            isNaN(column)
        ) {
            return null;
        }

        // Get source map URL
        const sourceMapUrl = await getSourceMapUrl(fileUrl);
        if (!sourceMapUrl) return null;

        // Get source map consumer
        const consumer = await getSourceMapConsumer(sourceMapUrl);
        if (!consumer) return null;

        // Resolve position using source map
        // Note: Source map line numbers are 1-based, but the API expects 0-based for originalPositionFor
        // Validate line and column numbers to prevent errors
        const validLine = Math.max(1, Math.min(line, 1000000)); // Reasonable bounds
        const validColumn = Math.max(0, Math.min(column, 1000000)); // Reasonable bounds

        const originalPosition = consumer.originalPositionFor({
            line: validLine,
            column: validColumn,
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
            const fileUrl = match[1]?.trim();
            if (!fileUrl) continue;

            const lineNum = parseInt(match[2], 10);
            const columnNum = parseInt(match[3], 10);

            // Validate parsed numbers
            if (
                isNaN(lineNum) ||
                isNaN(columnNum) ||
                lineNum < 1 ||
                columnNum < 0
            ) {
                continue;
            }

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
 * Resolve a single file path from bundled file to original source file
 * This is useful for resolving filePath fields in metadata
 *
 * @param filePath - The file path to resolve
 * @param line - Line number (optional, helps with resolution)
 * @param column - Column number (optional, helps with resolution)
 * @param returnBundledIfFailed - If true, returns bundled path if resolution fails (default: true for production)
 * @returns Resolved source file path, or original path if resolution fails
 */
export async function resolveFilePath(
    filePath: string | undefined,
    line?: number,
    column?: number,
    returnBundledIfFailed: boolean = true // Default to true - always return a path for traceability
): Promise<string | undefined> {
    const DEBUG = typeof process !== 'undefined' && process.env.HEALOPS_DEBUG_SOURCEMAPS === 'true';

    if (!filePath) return undefined;

    // Only resolve if it looks like a bundled file
    if (!isBundledFile(filePath)) {
        if (DEBUG) console.log('[SourceMap] File is already a source file, no resolution needed:', filePath);
        return filePath;
    }

    if (DEBUG) console.log('[SourceMap] Attempting to resolve bundled file:', filePath, 'at', line || 1, ':', column || 0);

    // Try to resolve using source maps
    // If line/column not provided, use line 1, column 0 as default
    const resolved = await resolvePosition(filePath, line || 1, column || 0);

    // Return resolved source if available
    if (resolved?.source) {
        if (DEBUG) console.log('[SourceMap] ✓ Successfully resolved to source file:', resolved.source);
        return resolved.source;
    }

    // Always return the original path if resolution fails
    // This ensures logs always have a file path for traceability
    // Even if it's a chunk path, it's better than no path at all
    if (DEBUG) console.warn('[SourceMap] ❌ Failed to resolve source file, returning', returnBundledIfFailed ? 'bundled path' : 'undefined');
    return returnBundledIfFailed ? filePath : undefined;
}

/**
 * Extract the first meaningful file path from a stack trace
 * This is useful for setting filePath when it's not explicitly provided
 *
 * @param stack - The stack trace string
 * @param allowBundledFiles - If true, will return bundled file paths if no source files are found (default: true)
 */
export function extractFilePathFromStack(
    stack: string | undefined,
    allowBundledFiles: boolean = true
): string | undefined {
    if (!stack) return undefined;

    const lines = stack.split('\n');
    const patterns = [
        // Chrome/Edge: "at functionName (file:line:column)"
        /at\s+(?:[^(]+)?\(([^:)]+):(\d+):(\d+)\)/,
        // Chrome/Edge: "at file:line:column"
        /at\s+([^:]+):(\d+):(\d+)/,
        // Firefox: "functionName@file:line:column"
        /@([^:]+):(\d+):(\d+)/
    ];

    let firstBundledPath: string | undefined = undefined;

    for (const line of lines) {
        // Skip error message lines
        if (
            line.trim().startsWith('Error:') ||
            line.trim().startsWith('TypeError:') ||
            line.trim().startsWith('ReferenceError:') ||
            line.trim().startsWith('SyntaxError:')
        ) {
            continue;
        }

        for (const pattern of patterns) {
            const match = line.match(pattern);
            if (match) {
                const filePath = match[1].trim();

                // Skip SDK internal files, node_modules, and browser APIs that are part of our interception
                // Also skip the SDK's fetch interceptor - we want the actual error location, not our interceptor
                const isSdkFile =
                    filePath.includes('HealOpsLogger') ||
                    filePath.includes('healops-opentelemetry') ||
                    filePath.includes('node_modules');

                // Check if this is the SDK's fetch interceptor (window.fetch)
                // We identify it by checking if the line contains "window.fetch" in the function name
                // This is our interceptor, not the actual error location
                const isFetchInterceptor =
                    line.includes('window.fetch') ||
                    line.includes('at window.fetch') ||
                    line.includes('at async window.fetch');

                if (isSdkFile || isFetchInterceptor) {
                    continue;
                }

                // Prefer source files over bundled files
                if (!isBundledFile(filePath)) {
                    return filePath;
                }

                // Store first bundled file path as fallback
                if (allowBundledFiles && !firstBundledPath) {
                    firstBundledPath = filePath;
                }
            }
        }
    }

    // Return bundled file path if no source files found and allowed
    return allowBundledFiles ? firstBundledPath : undefined;
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
