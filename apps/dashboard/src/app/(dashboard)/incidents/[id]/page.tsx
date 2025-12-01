'use client';

import { useEffect, useState, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Loader2, ArrowLeft, CheckCircle2 } from 'lucide-react';
import { Incident } from '@/components/incident-table';
import { getApiBaseUrl } from '@/lib/config';

interface LogEntry {
    id: number;
    timestamp: string;
    level: string;
    message: string;
    service_name: string;
    source: string;
    metadata_json: unknown;
}

export default function IncidentDetailsPage() {
    const params = useParams();
    const router = useRouter();
    const [data, setData] = useState<{
        incident: Incident;
        logs: LogEntry[];
    } | null>(null);
    const [loading, setLoading] = useState(true);
    const [resolving, setResolving] = useState(false);
    const [analyzing, setAnalyzing] = useState(false);
    const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

    const triggerAnalysis = async () => {
        try {
            const apiBase = getApiBaseUrl();
            setAnalyzing(true);
            await fetch(`${apiBase}/incidents/${params.id}/analyze`, {
                method: 'POST'
            });
            // Start polling after triggering analysis
            if (!pollIntervalRef.current) {
                pollIntervalRef.current = setInterval(async () => {
                    const apiBase = getApiBaseUrl();
                    try {
                        const response = await fetch(
                            `${apiBase}/incidents/${params.id}`
                        );
                        if (response.ok) {
                            const result = await response.json();
                            setData(result);
                            if (result.incident?.root_cause) {
                                if (pollIntervalRef.current) {
                                    clearInterval(pollIntervalRef.current);
                                    pollIntervalRef.current = null;
                                }
                                setAnalyzing(false);
                            }
                        }
                    } catch (error) {
                        console.error(
                            'Failed to fetch incident during polling:',
                            error
                        );
                    }
                }, 3000);
            }
        } catch (error) {
            console.error('Failed to trigger analysis:', error);
            setAnalyzing(false);
        }
    };

    useEffect(() => {
        const apiBase = getApiBaseUrl();
        let pollCount = 0;
        const MAX_POLL_ATTEMPTS = 40; // 2 minutes max (40 * 3 seconds)

        const fetchIncident = async () => {
            try {
                const response = await fetch(
                    `${apiBase}/incidents/${params.id}`
                );
                if (response.ok) {
                    const result = await response.json();
                    setData(result);
                    setLoading(false);

                    // Check if analysis is available
                    const hasRootCause = !!result.incident?.root_cause;

                    if (hasRootCause) {
                        // Analysis complete - stop polling
                        if (pollIntervalRef.current) {
                            clearInterval(pollIntervalRef.current);
                            pollIntervalRef.current = null;
                        }
                        setAnalyzing(false);
                        return false; // Don't poll
                    } else {
                        // Still analyzing
                        setAnalyzing(true);
                        return true; // Continue polling
                    }
                }
            } catch (error) {
                console.error('Failed to fetch incident:', error);
                setLoading(false);
            }
            return false;
        };

        // Initial fetch and trigger analysis if needed
        fetchIncident().then((shouldPoll) => {
            // Always start polling if root_cause is not available
            if (shouldPoll && !pollIntervalRef.current) {
                console.log('Starting polling for AI analysis...');
                pollIntervalRef.current = setInterval(async () => {
                    pollCount++;

                    // Stop polling after max attempts
                    if (pollCount >= MAX_POLL_ATTEMPTS) {
                        console.warn(
                            'Max polling attempts reached. Stopping polling.'
                        );
                        if (pollIntervalRef.current) {
                            clearInterval(pollIntervalRef.current);
                            pollIntervalRef.current = null;
                        }
                        setAnalyzing(false);
                        return;
                    }

                    const response = await fetch(
                        `${apiBase}/incidents/${params.id}`
                    );
                    if (response.ok) {
                        const result = await response.json();
                        setData(result);

                        if (result.incident?.root_cause) {
                            // Analysis complete!
                            console.log('AI analysis completed!');
                            if (pollIntervalRef.current) {
                                clearInterval(pollIntervalRef.current);
                                pollIntervalRef.current = null;
                            }
                            setAnalyzing(false);
                        }
                    }
                }, 3000);
            }
        });

        return () => {
            if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
                pollIntervalRef.current = null;
            }
        };
    }, [params.id]);

    const handleResolve = async () => {
        setResolving(true);
        try {
            const apiBase = getApiBaseUrl();
            const response = await fetch(`${apiBase}/incidents/${params.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'RESOLVED' })
            });

            if (response.ok) {
                // Refresh data
                const updatedIncident = await response.json();
                setData((prev) =>
                    prev ? { ...prev, incident: updatedIncident } : null
                );
            }
        } catch (error) {
            console.error('Failed to resolve incident:', error);
        } finally {
            setResolving(false);
        }
    };

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (!data) {
        return (
            <div className="flex h-full flex-col items-center justify-center space-y-4">
                <h2 className="text-xl font-bold">Incident not found</h2>
                <Button onClick={() => router.push('/incidents')}>
                    Back to Incidents
                </Button>
            </div>
        );
    }

    const { incident, logs } = data;

    return (
        <div className="flex-1 space-y-4 p-8 pt-6">
            <div className="flex items-center justify-between">
                <div className="flex items-center space-x-4">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => router.push('/incidents')}
                    >
                        <ArrowLeft className="h-4 w-4" />
                    </Button>
                    <div>
                        <h2 className="text-2xl font-bold tracking-tight">
                            {incident.title}
                        </h2>
                        <div className="flex items-center space-x-2 mt-1">
                            <Badge variant="outline">
                                {incident.service_name}
                            </Badge>
                            <span className="text-sm text-muted-foreground">
                                First seen{' '}
                                {new Date(incident.created_at).toLocaleString()}
                            </span>
                        </div>
                    </div>
                </div>
                <div className="flex items-center space-x-2">
                    <Badge
                        variant={
                            incident.severity === 'CRITICAL'
                                ? 'destructive'
                                : 'outline'
                        }
                        className="text-sm px-3 py-1"
                    >
                        {incident.severity}
                    </Badge>
                    <Badge
                        variant={
                            incident.status === 'RESOLVED'
                                ? 'default'
                                : 'secondary'
                        }
                        className="text-sm px-3 py-1"
                    >
                        {incident.status}
                    </Badge>
                    {incident.status !== 'RESOLVED' && (
                        <Button onClick={handleResolve} disabled={resolving}>
                            {resolving && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Mark as Resolved
                        </Button>
                    )}
                </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
                <div className="col-span-4 space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Related Logs</CardTitle>
                            <CardDescription>
                                Recent logs associated with this incident
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <ScrollArea className="h-[400px] w-full rounded-md border p-4">
                                <div className="space-y-4">
                                    {logs.map((log) => (
                                        <div
                                            key={log.id}
                                            className="flex flex-col space-y-1 border-b pb-2 last:border-0"
                                        >
                                            <div className="flex items-center justify-between">
                                                <span
                                                    className={`text-xs font-bold ${
                                                        log.level === 'CRITICAL'
                                                            ? 'text-red-500'
                                                            : log.level ===
                                                              'ERROR'
                                                            ? 'text-red-400'
                                                            : 'text-zinc-400'
                                                    }`}
                                                >
                                                    {log.level}
                                                </span>
                                                <span className="text-xs text-muted-foreground font-mono">
                                                    {new Date(
                                                        log.timestamp
                                                    ).toLocaleTimeString()}
                                                </span>
                                            </div>
                                            <p className="text-sm font-mono text-zinc-300 break-all">
                                                {log.message}
                                            </p>
                                        </div>
                                    ))}
                                </div>
                            </ScrollArea>
                        </CardContent>
                    </Card>
                </div>

                <div className="col-span-3 space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>AI Analysis</CardTitle>
                            <CardDescription>
                                Automated root cause analysis
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {incident.root_cause ? (
                                <div
                                    className={`rounded-lg p-4 border ${
                                        incident.root_cause.includes(
                                            'failed'
                                        ) ||
                                        incident.root_cause.includes('error') ||
                                        incident.root_cause.includes(
                                            'not configured'
                                        ) ||
                                        incident.root_cause.includes(
                                            'insufficient'
                                        ) ||
                                        incident.root_cause.includes('credits')
                                            ? 'bg-red-900/20 border-red-900/50'
                                            : 'bg-zinc-900 border-zinc-800'
                                    }`}
                                >
                                    <p
                                        className={`text-sm whitespace-pre-wrap ${
                                            incident.root_cause.includes(
                                                'failed'
                                            ) ||
                                            incident.root_cause.includes(
                                                'error'
                                            ) ||
                                            incident.root_cause.includes(
                                                'not configured'
                                            ) ||
                                            incident.root_cause.includes(
                                                'insufficient'
                                            ) ||
                                            incident.root_cause.includes(
                                                'credits'
                                            )
                                                ? 'text-red-300'
                                                : 'text-zinc-300'
                                        }`}
                                    >
                                        {incident.root_cause}
                                    </p>
                                    {(incident.root_cause.includes('failed') ||
                                        incident.root_cause.includes('error') ||
                                        incident.root_cause.includes(
                                            'not configured'
                                        ) ||
                                        incident.root_cause.includes(
                                            'insufficient'
                                        ) ||
                                        incident.root_cause.includes(
                                            'credits'
                                        )) && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={triggerAnalysis}
                                            className="mt-4"
                                        >
                                            Retry Analysis
                                        </Button>
                                    )}
                                </div>
                            ) : (
                                <div className="flex flex-col items-center justify-center p-8 text-muted-foreground">
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin mb-2" />
                                    <p>Analyzing incident...</p>
                                    {!analyzing && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={triggerAnalysis}
                                            className="mt-4"
                                        >
                                            Retry Analysis
                                        </Button>
                                    )}
                                </div>
                            )}

                            {incident.action_taken && (
                                <div className="rounded-lg bg-green-900/20 p-4 border border-green-900/50">
                                    <div className="flex items-center mb-2">
                                        <CheckCircle2 className="h-4 w-4 text-green-500 mr-2" />
                                        <h4 className="font-semibold text-green-500">
                                            Action Taken
                                        </h4>
                                    </div>
                                    <p className="text-sm text-zinc-300">
                                        {incident.action_taken}
                                    </p>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
}
