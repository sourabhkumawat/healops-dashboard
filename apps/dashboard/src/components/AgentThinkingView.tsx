'use client';

import { useEffect, useState, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { getWebSocketUrl } from '@/lib/config';

interface AgentEvent {
    type: string;
    timestamp: string;
    agent?: string;
    step_number?: number;
    step_description?: string;
    data: {
        message?: string;
        details?: any;
        file_path?: string;
        success?: boolean;
        error?: string;
        content?: string;
        relevance?: number;
        source?: string;
    };
}

interface AgentThinkingViewProps {
    incidentId: number;
    isAnalyzing?: boolean;
}

function getEventTypeStyle(type: string) {
    switch (type) {
        case 'plan_created':
        case 'plan_updated':
            return 'bg-blue-500/20 text-blue-400 border border-blue-500/30';
        case 'plan_step_started':
            return 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30';
        case 'plan_step_completed':
            return 'bg-green-500/20 text-green-400 border border-green-500/30';
        case 'plan_step_failed':
            return 'bg-red-500/20 text-red-400 border border-red-500/30';
        case 'agent_action':
            return 'bg-purple-500/20 text-purple-400 border border-purple-500/30';
        case 'observation':
            return 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30';
        case 'error':
            return 'bg-red-500/20 text-red-400 border border-red-500/30';
        case 'knowledge_retrieved':
            return 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30';
        case 'file_operation':
            return 'bg-orange-500/20 text-orange-400 border border-orange-500/30';
        default:
            return 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20';
    }
}

function getEventIcon(type: string) {
    switch (type) {
        case 'plan_created':
        case 'plan_updated':
            return '📋';
        case 'plan_step_started':
            return '🔄';
        case 'plan_step_completed':
            return '✅';
        case 'plan_step_failed':
            return '❌';
        case 'agent_action':
            return '🤖';
        case 'observation':
            return '👁️';
        case 'error':
            return '⚠️';
        case 'knowledge_retrieved':
            return '🧠';
        case 'file_operation':
            return '📁';
        default:
            return '📌';
    }
}

export function AgentThinkingView({
    incidentId,
    isAnalyzing = false
}: AgentThinkingViewProps) {
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [connectionStatus, setConnectionStatus] = useState<
        'connecting' | 'connected' | 'disconnected'
    >('connecting');
    const scrollRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        if (!isAnalyzing) {
            setConnectionStatus('disconnected');
            return;
        }

        // Connect to WebSocket
        const wsUrl = getWebSocketUrl().replace(
            '/ws/logs',
            `/ws/agent-events/${incidentId}`
        );
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
            console.log(
                `Connected to Agent Events WebSocket for incident ${incidentId}`
            );
            setConnectionStatus('connected');
        };

        ws.onmessage = (event) => {
            try {
                const eventData: AgentEvent = JSON.parse(event.data);

                // Ensure timestamp is set
                if (!eventData.timestamp) {
                    eventData.timestamp = new Date().toISOString();
                }

                setEvents((prev) => {
                    const newEvents = [...prev, eventData];
                    // Keep last 200 events
                    if (newEvents.length > 200) {
                        return newEvents.slice(newEvents.length - 200);
                    }
                    return newEvents;
                });
            } catch (error) {
                console.error('Error parsing agent event:', error);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            setConnectionStatus('disconnected');
        };

        ws.onclose = () => {
            console.log('Disconnected from Agent Events WebSocket');
            setConnectionStatus('disconnected');
        };

        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [incidentId, isAnalyzing]);

    // Auto-scroll to bottom when new events arrive
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [events]);

    const formatTimestamp = (timestamp: string) => {
        try {
            const date = new Date(timestamp);
            return date.toLocaleTimeString();
        } catch {
            return timestamp;
        }
    };

    return (
        <Card className="h-full flex flex-col">
            <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">
                        Agent Thinking Process
                    </CardTitle>
                    <Badge
                        variant={
                            connectionStatus === 'connected'
                                ? 'default'
                                : connectionStatus === 'connecting'
                                ? 'secondary'
                                : 'destructive'
                        }
                    >
                        {connectionStatus === 'connected' && '🟢 Live'}
                        {connectionStatus === 'connecting' && '🟡 Connecting'}
                        {connectionStatus === 'disconnected' &&
                            '🔴 Disconnected'}
                    </Badge>
                </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-hidden">
                <div
                    ref={scrollRef}
                    className="h-full overflow-y-auto space-y-2 pr-2"
                >
                    {events.length === 0 ? (
                        <div className="text-center text-muted-foreground py-8">
                            {isAnalyzing
                                ? 'Waiting for agent events...'
                                : 'Agent analysis not in progress'}
                        </div>
                    ) : (
                        events.map((event, index) => (
                            <div
                                key={index}
                                className={`p-3 rounded-lg border ${getEventTypeStyle(
                                    event.type
                                )}`}
                            >
                                <div className="flex items-start gap-2">
                                    <span className="text-lg">
                                        {getEventIcon(event.type)}
                                    </span>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 mb-1">
                                            <span className="font-medium text-sm">
                                                {event.type
                                                    .replace(/_/g, ' ')
                                                    .toUpperCase()}
                                            </span>
                                            {event.step_number && (
                                                <Badge
                                                    variant="outline"
                                                    className="text-xs"
                                                >
                                                    Step {event.step_number}
                                                </Badge>
                                            )}
                                            {event.agent && (
                                                <Badge
                                                    variant="outline"
                                                    className="text-xs"
                                                >
                                                    {event.agent}
                                                </Badge>
                                            )}
                                            <span className="text-xs text-muted-foreground ml-auto">
                                                {formatTimestamp(
                                                    event.timestamp
                                                )}
                                            </span>
                                        </div>

                                        {event.step_description && (
                                            <div className="text-sm mb-2 font-medium">
                                                {event.step_description}
                                            </div>
                                        )}

                                        {event.data.message && (
                                            <div className="text-sm">
                                                {event.data.message}
                                            </div>
                                        )}

                                        {event.data.file_path && (
                                            <div className="text-xs mt-1 text-muted-foreground">
                                                📁 {event.data.file_path}
                                            </div>
                                        )}

                                        {event.data.relevance && (
                                            <div className="text-xs mt-1">
                                                Relevance:{' '}
                                                {(
                                                    event.data.relevance * 100
                                                ).toFixed(0)}
                                                %
                                            </div>
                                        )}

                                        {event.data.error && (
                                            <div className="text-sm text-red-400 mt-1">
                                                ❌ {event.data.error}
                                            </div>
                                        )}

                                        {event.data.success !== undefined && (
                                            <div className="text-xs mt-1">
                                                {event.data.success
                                                    ? '✅ Success'
                                                    : '❌ Failed'}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
