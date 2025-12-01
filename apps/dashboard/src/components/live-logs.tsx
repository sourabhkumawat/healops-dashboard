'use client';

import { useEffect, useState, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { LogEntry } from '@/actions/logs';
import { getWebSocketUrl } from '@/lib/config';

interface LiveLogsProps {
    initialLogs?: LogEntry[];
}

export function LiveLogs({ initialLogs = [] }: LiveLogsProps) {
    const [logs, setLogs] = useState<LogEntry[]>(initialLogs);
    const scrollRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        // Connect to WebSocket
        const ws = new WebSocket(getWebSocketUrl());
        wsRef.current = ws;

        ws.onopen = () => {
            console.log('Connected to Live Logs WebSocket');
        };

        ws.onmessage = (event) => {
            try {
                const logData = JSON.parse(event.data);
                // Add ID if missing (for ephemeral logs)
                if (!logData.id) {
                    logData.id = Date.now() + Math.random();
                }

                setLogs((prev) => {
                    const newLogs = [...prev, logData];
                    // Keep last 100 logs
                    if (newLogs.length > 100) {
                        return newLogs.slice(newLogs.length - 100);
                    }
                    return newLogs;
                });
            } catch (e) {
                console.error('Failed to parse log message', e);
            }
        };

        ws.onclose = () => {
            console.log('Disconnected from Live Logs WebSocket');
        };

        return () => {
            ws.close();
        };
    }, []);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs]);

    return (
        <Card className="col-span-4 bg-zinc-950 border-zinc-800">
            <CardHeader className="pb-2 flex flex-row items-center justify-between">
                <CardTitle className="text-sm font-mono text-zinc-400 flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                    LIVE_SYSTEM_LOGS
                </CardTitle>
                <div className="flex gap-2 text-xs">
                    <span className="flex items-center">
                        <div className="w-2 h-2 rounded-full bg-red-500 mr-1"></div>
                        ERROR
                    </span>
                    <span className="flex items-center">
                        <div className="w-2 h-2 rounded-full bg-yellow-500 mr-1"></div>
                        WARN
                    </span>
                    <span className="flex items-center">
                        <div className="w-2 h-2 rounded-full bg-blue-500 mr-1"></div>
                        INFO
                    </span>
                </div>
            </CardHeader>
            <CardContent>
                <div
                    className="h-[400px] overflow-y-auto font-mono text-xs p-2 bg-black rounded-md border border-zinc-900"
                    ref={scrollRef}
                >
                    {logs.map((log) => (
                        <div
                            key={log.id}
                            className="mb-2 p-1 hover:bg-zinc-900 rounded transition-colors group"
                        >
                            <div className="flex items-start">
                                <span className="text-zinc-600 min-w-[80px] select-none">
                                    {new Date(log.timestamp).toLocaleTimeString(
                                        [],
                                        {
                                            hour12: false,
                                            hour: '2-digit',
                                            minute: '2-digit',
                                            second: '2-digit'
                                        }
                                    )}
                                </span>

                                <span
                                    className={`mx-2 px-1.5 rounded text-[10px] font-bold min-w-[50px] text-center ${
                                        log.severity === 'ERROR' ||
                                        log.severity === 'CRITICAL'
                                            ? 'bg-red-950 text-red-500 border border-red-900'
                                            : log.severity === 'WARNING'
                                            ? 'bg-yellow-950 text-yellow-500 border border-yellow-900'
                                            : 'bg-blue-950 text-blue-500 border border-blue-900'
                                    }`}
                                >
                                    {log.severity || log.level || 'INFO'}
                                </span>

                                <span
                                    className="text-zinc-400 mr-2 min-w-[100px] truncate"
                                    title={log.service_name}
                                >
                                    {log.service_name}
                                </span>

                                <span className="text-zinc-500 mr-2 text-[10px] uppercase border border-zinc-800 px-1 rounded">
                                    {log.source || 'UNKNOWN'}
                                </span>

                                <div className="flex-1 break-all">
                                    <span className="text-zinc-300">
                                        {log.message.split('\n')[0]}
                                    </span>
                                    {log.metadata && (
                                        <div className="hidden group-hover:block mt-1 pl-2 border-l-2 border-zinc-800 text-zinc-500 text-[10px]">
                                            {log.metadata.traceId && (
                                                <div>
                                                    Trace ID:{' '}
                                                    {log.metadata.traceId}
                                                </div>
                                            )}
                                            {log.metadata.spanId && (
                                                <div>
                                                    Span ID:{' '}
                                                    {log.metadata.spanId}
                                                </div>
                                            )}
                                            {log.metadata.attributes && (
                                                <div className="mt-0.5">
                                                    {Object.entries(
                                                        log.metadata.attributes
                                                    )
                                                        .slice(0, 3)
                                                        .map(([k, v]) => (
                                                            <span
                                                                key={k}
                                                                className="mr-2"
                                                            >
                                                                {k}: {String(v)}
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
                        <div className="flex flex-col items-center justify-center h-full text-zinc-600">
                            <p>No logs received yet</p>
                            <p className="text-[10px] mt-1">
                                Waiting for incoming telemetry...
                            </p>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
