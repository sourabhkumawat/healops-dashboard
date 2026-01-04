'use client';

import { useEffect, useState, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@healops/ui';
import { LogEntry } from '@/actions/logs';
import { getWebSocketUrl } from '@/lib/config';

interface LiveLogsProps {
    initialLogs?: LogEntry[];
}

// Helper function to get log level styling
function getLogLevelStyle(
    severity: string | undefined,
    level: string | undefined
) {
    const logLevel = (severity || level || 'INFO').toUpperCase();

    switch (logLevel) {
        case 'CRITICAL':
            return 'bg-red-500/20 text-red-400 border border-red-500/30';
        case 'ERROR':
            return 'bg-red-500/15 text-red-400 border border-red-500/25';
        case 'WARNING':
        case 'WARN':
            return 'bg-yellow-500/15 text-yellow-400 border border-yellow-500/25';
        case 'INFO':
            return 'bg-blue-500/15 text-blue-400 border border-blue-500/25';
        case 'DEBUG':
            return 'bg-purple-500/15 text-purple-400 border border-purple-500/25';
        case 'TRACE':
            return 'bg-gray-500/10 text-gray-400 border border-gray-500/20';
        default:
            return 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20';
    }
}

// Helper function to get log level indicator color
function getLogLevelIndicatorColor(
    severity: string | undefined,
    level: string | undefined
) {
    const logLevel = (severity || level || 'INFO').toUpperCase();

    switch (logLevel) {
        case 'CRITICAL':
            return 'bg-red-600';
        case 'ERROR':
            return 'bg-red-500';
        case 'WARNING':
        case 'WARN':
            return 'bg-yellow-500';
        case 'INFO':
            return 'bg-blue-500';
        case 'DEBUG':
            return 'bg-purple-500';
        case 'TRACE':
            return 'bg-gray-500';
        default:
            return 'bg-zinc-500';
    }
}

export function LiveLogs({ initialLogs = [] }: LiveLogsProps) {
    const [logs, setLogs] = useState<LogEntry[]>(initialLogs);
    const [connectionStatus, setConnectionStatus] = useState<
        'connecting' | 'connected' | 'disconnected'
    >('connecting');
    const scrollRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        // Connect to WebSocket
        const ws = new WebSocket(getWebSocketUrl());
        wsRef.current = ws;

        ws.onopen = () => {
            console.log('Connected to Live Logs WebSocket (Redis Pub/Sub)');
            setConnectionStatus('connected');
        };

        ws.onmessage = (event) => {
            try {
                const logData = JSON.parse(event.data);
                // Add ID if missing (for ephemeral logs)
                if (!logData.id) {
                    logData.id = Date.now() + Math.random();
                }

                // Ensure timestamp is set
                if (!logData.timestamp) {
                    logData.timestamp = new Date().toISOString();
                }

                setLogs((prev) => {
                    const newLogs = [...prev, logData];
                    // Keep last 500 logs (increased for all log levels)
                    if (newLogs.length > 500) {
                        return newLogs.slice(newLogs.length - 500);
                    }
                    return newLogs;
                });
            } catch (e) {
                console.error('Failed to parse log message', e);
            }
        };

        ws.onerror = (error) => {
            const wsUrl = getWebSocketUrl();
            const readyState = ws.readyState;
            const readyStateMap: Record<number, string> = {
                0: 'CONNECTING',
                1: 'OPEN',
                2: 'CLOSING',
                3: 'CLOSED'
            };
            
            // WebSocket error events don't contain much info, so we log what we can
            const errorInfo: Record<string, any> = {
                url: wsUrl,
                readyState: readyStateMap[readyState] || readyState,
                timestamp: new Date().toISOString()
            };
            
            // Try to extract any available error information
            if (error.type) {
                errorInfo.type = error.type;
            }
            if (error.target) {
                errorInfo.target = 'WebSocket';
            }
            
            console.error('WebSocket connection error:', errorInfo);
            setConnectionStatus('disconnected');
        };

        ws.onclose = () => {
            console.log('Disconnected from Live Logs WebSocket');
            setConnectionStatus('disconnected');
        };

        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, []);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs]);

    return (
        <Card className="col-span-4 bg-zinc-950 border-zinc-800">
            <CardHeader className="pb-3 flex flex-row items-center justify-between border-b border-zinc-800">
                <div className="flex items-center gap-3">
                    <div
                        className={`w-2.5 h-2.5 rounded-full ${
                            connectionStatus === 'connected'
                                ? 'bg-green-500 animate-pulse shadow-lg shadow-green-500/50'
                                : connectionStatus === 'connecting'
                                ? 'bg-yellow-500 animate-pulse'
                                : 'bg-red-500'
                        }`}
                    ></div>
                    <CardTitle className="text-sm font-semibold text-zinc-200">
                        Live System Logs
                    </CardTitle>
                    <span className="text-xs text-zinc-500 font-normal">
                        {logs.length} {logs.length === 1 ? 'log' : 'logs'}
                    </span>
                </div>
                <div className="flex gap-3 text-xs flex-wrap">
                    <span className="flex items-center gap-1.5 text-zinc-400">
                        <div className="w-2 h-2 rounded-full bg-red-600"></div>
                        <span className="text-[10px]">CRITICAL</span>
                    </span>
                    <span className="flex items-center gap-1.5 text-zinc-400">
                        <div className="w-2 h-2 rounded-full bg-red-500"></div>
                        <span className="text-[10px]">ERROR</span>
                    </span>
                    <span className="flex items-center gap-1.5 text-zinc-400">
                        <div className="w-2 h-2 rounded-full bg-yellow-500"></div>
                        <span className="text-[10px]">WARN</span>
                    </span>
                    <span className="flex items-center gap-1.5 text-zinc-400">
                        <div className="w-2 h-2 rounded-full bg-blue-500"></div>
                        <span className="text-[10px]">INFO</span>
                    </span>
                    <span className="flex items-center gap-1.5 text-zinc-400">
                        <div className="w-2 h-2 rounded-full bg-purple-500"></div>
                        <span className="text-[10px]">DEBUG</span>
                    </span>
                </div>
            </CardHeader>
            <CardContent>
                <div
                    className="h-[400px] overflow-y-auto font-mono text-xs bg-black rounded-md border border-zinc-900"
                    ref={scrollRef}
                >
                    {logs.map((log) => (
                        <div
                            key={log.id}
                            className="border-b border-zinc-900/50 hover:bg-zinc-900/30 transition-colors group"
                        >
                            <div className="flex items-start gap-3 px-3 py-2.5">
                                {/* Log Level Indicator Bar */}
                                <div
                                    className={`w-1 h-full min-h-[20px] rounded-full shrink-0 ${getLogLevelIndicatorColor(
                                        log.severity,
                                        log.level
                                    )}`}
                                ></div>

                                {/* Main Content */}
                                <div className="flex-1 min-w-0">
                                    {/* First Line: Timestamp, Level, Service, Source */}
                                    <div className="flex items-center gap-2.5 flex-wrap mb-1.5">
                                        {/* Timestamp */}
                                        <span className="text-zinc-400 font-medium select-none shrink-0">
                                            {log.timestamp
                                                ? (() => {
                                                      const date = new Date(
                                                          log.timestamp
                                                      );
                                                      const timeStr =
                                                          date.toLocaleTimeString(
                                                              [],
                                                              {
                                                                  hour12: false,
                                                                  hour: '2-digit',
                                                                  minute: '2-digit',
                                                                  second: '2-digit'
                                                              }
                                                          );
                                                      const ms = date
                                                          .getMilliseconds()
                                                          .toString()
                                                          .padStart(3, '0');
                                                      return `${timeStr}.${ms}`;
                                                  })()
                                                : '--:--:--'}
                                        </span>

                                        {/* Log Level Badge */}
                                        <span
                                            className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide shrink-0 ${getLogLevelStyle(
                                                log.severity,
                                                log.level
                                            )}`}
                                        >
                                            {log.severity ||
                                                log.level ||
                                                'INFO'}
                                        </span>

                                        {/* Service Name */}
                                        {log.service_name && (
                                            <span
                                                className="text-zinc-300 font-medium truncate max-w-[150px]"
                                                title={log.service_name}
                                            >
                                                {log.service_name}
                                            </span>
                                        )}

                                        {/* Source Badge */}
                                        {log.source && (
                                            <span className="text-zinc-500 text-[10px] uppercase border border-zinc-700/50 px-1.5 py-0.5 rounded bg-zinc-900/50 shrink-0">
                                                {log.source}
                                            </span>
                                        )}
                                    </div>

                                    {/* Message */}
                                    <div className="text-zinc-200 leading-relaxed break-words">
                                        {log.message
                                            .split('\n')
                                            .map((line, lineIndex) => (
                                                <div
                                                    key={lineIndex}
                                                    className={
                                                        lineIndex > 0
                                                            ? 'mt-1 pl-4 border-l-2 border-zinc-800/50'
                                                            : ''
                                                    }
                                                >
                                                    {line || (
                                                        <span className="text-zinc-500 italic">
                                                            (empty line)
                                                        </span>
                                                    )}
                                                </div>
                                            ))}
                                    </div>

                                    {/* Metadata (on hover) */}
                                    {log.metadata && (
                                        <div className="hidden group-hover:block mt-2 pt-2 border-t border-zinc-800/50 space-y-1 text-zinc-500 text-[10px]">
                                            {log.metadata.traceId && (
                                                <div className="flex items-center gap-2">
                                                    <span className="text-zinc-600">
                                                        Trace:
                                                    </span>
                                                    <span className="font-mono text-zinc-400">
                                                        {log.metadata.traceId}
                                                    </span>
                                                </div>
                                            )}
                                            {log.metadata.spanId && (
                                                <div className="flex items-center gap-2">
                                                    <span className="text-zinc-600">
                                                        Span:
                                                    </span>
                                                    <span className="font-mono text-zinc-400">
                                                        {log.metadata.spanId}
                                                    </span>
                                                </div>
                                            )}
                                            {log.metadata.attributes &&
                                                Object.keys(
                                                    log.metadata.attributes
                                                ).length > 0 && (
                                                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                                                        {Object.entries(
                                                            log.metadata
                                                                .attributes
                                                        )
                                                            .slice(0, 5)
                                                            .map(([k, v]) => (
                                                                <span
                                                                    key={k}
                                                                    className="flex items-center gap-1"
                                                                >
                                                                    <span className="text-zinc-600">
                                                                        {k}:
                                                                    </span>
                                                                    <span className="text-zinc-400 font-mono">
                                                                        {String(
                                                                            v
                                                                        ).slice(
                                                                            0,
                                                                            50
                                                                        )}
                                                                        {String(
                                                                            v
                                                                        )
                                                                            .length >
                                                                            50 &&
                                                                            '...'}
                                                                    </span>
                                                                </span>
                                                            ))}
                                                    </div>
                                                )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                    {logs.length === 0 && (
                        <div className="flex flex-col items-center justify-center h-full text-zinc-500">
                            <div className="text-center">
                                <p className="text-sm mb-1">
                                    No logs received yet
                                </p>
                                <p className="text-xs text-zinc-600">
                                    Waiting for incoming telemetry...
                                </p>
                            </div>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
