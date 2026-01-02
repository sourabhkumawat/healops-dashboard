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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Loader2,
    ArrowLeft,
    CheckCircle2,
    GitPullRequest,
    ExternalLink,
    AlertTriangle,
    FileText,
    Activity,
    Code
} from 'lucide-react';
import { Incident } from '@/components/incident-table';
import {
    getIncident,
    triggerIncidentAnalysis,
    updateIncidentStatus
} from '@/actions/incidents';
import CodeDiffViewer from '@/components/CodeDiffViewer';

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
    const [selectedFileDiff, setSelectedFileDiff] = useState<{
        file: string;
        oldCode: string;
        newCode: string;
    } | null>(null);

    const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
    const fetchingIncidentRef = useRef(false);

    const triggerAnalysis = async () => {
        try {
            setAnalyzing(true);
            await triggerIncidentAnalysis(Number(params.id));
            // Start polling after triggering analysis
            if (!pollIntervalRef.current) {
                pollIntervalRef.current = setInterval(async () => {
                    if (fetchingIncidentRef.current) return; // Prevent duplicate calls
                    fetchingIncidentRef.current = true;
                    try {
                        const result = await getIncident(Number(params.id));
                        if (result) {
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
                    } finally {
                        fetchingIncidentRef.current = false;
                    }
                }, 3000);
            }
        } catch (error) {
            console.error('Failed to trigger analysis:', error);
            setAnalyzing(false);
        }
    };

    useEffect(() => {
        let pollCount = 0;
        const MAX_POLL_ATTEMPTS = 40; // 2 minutes max (40 * 3 seconds)

        const fetchIncident = async (): Promise<boolean> => {
            if (fetchingIncidentRef.current) return false; // Prevent duplicate calls
            fetchingIncidentRef.current = true;
            try {
                const result = await getIncident(Number(params.id));
                if (result) {
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
            } finally {
                fetchingIncidentRef.current = false;
            }
            return false;
        };

        // Initial fetch and trigger analysis if needed
        fetchIncident().then((shouldPoll) => {
            // Always start polling if root_cause is not available
            if (shouldPoll && !pollIntervalRef.current) {
                console.log('Starting polling for AI analysis...');
                pollIntervalRef.current = setInterval(async () => {
                    if (fetchingIncidentRef.current) return; // Prevent duplicate calls
                    fetchingIncidentRef.current = true;
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
                        fetchingIncidentRef.current = false;
                        return;
                    }

                    try {
                        const result = await getIncident(Number(params.id));
                        if (result) {
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
                    } catch (error) {
                        console.error('Failed to fetch incident:', error);
                    } finally {
                        fetchingIncidentRef.current = false;
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
            const updatedIncident = await updateIncidentStatus(
                Number(params.id),
                'RESOLVED'
            );

            if (updatedIncident) {
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
        <div className="flex h-[calc(100vh-65px)] overflow-hidden">
             {/* Left Pane - Incident Details */}
             <div className={`${selectedFileDiff ? 'w-1/2 border-r' : 'w-full'} flex flex-col h-full overflow-hidden bg-background`}>
                <div className="border-b p-4">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-4">
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => router.push('/incidents')}
                            >
                                <ArrowLeft className="h-4 w-4" />
                            </Button>
                            <div>
                                <h2 className="text-xl font-bold tracking-tight">
                                    {incident.title}
                                </h2>
                                <div className="flex items-center gap-2 mt-1">
                                    <Badge variant="outline" className="text-xs">
                                        {incident.service_name}
                                    </Badge>
                                    <span className="text-xs text-muted-foreground">
                                        {new Date(incident.created_at).toLocaleString()}
                                    </span>
                                </div>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                             <Badge
                                variant={
                                    incident.severity === 'CRITICAL'
                                        ? 'destructive'
                                        : 'outline'
                                }
                                className="text-xs"
                            >
                                {incident.severity}
                            </Badge>
                            {incident.status !== 'RESOLVED' && (
                                <Button size="sm" onClick={handleResolve} disabled={resolving}>
                                    {resolving && (
                                        <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                                    )}
                                    Resolve
                                </Button>
                            )}
                        </div>
                    </div>
                </div>

                <div className="flex-1 overflow-hidden p-4">
                    <Tabs defaultValue="analysis" className="h-full flex flex-col">
                        <TabsList className="grid w-full grid-cols-3 mb-4">
                            <TabsTrigger value="analysis" className="flex items-center gap-2">
                                <Activity className="h-4 w-4" />
                                AI Analysis
                            </TabsTrigger>
                            <TabsTrigger value="logs" className="flex items-center gap-2">
                                <FileText className="h-4 w-4" />
                                Logs
                            </TabsTrigger>
                            <TabsTrigger value="metadata" className="flex items-center gap-2">
                                <Code className="h-4 w-4" />
                                Raw Data
                            </TabsTrigger>
                        </TabsList>

                        <div className="flex-1 overflow-auto">
                            <TabsContent value="analysis" className="space-y-4 m-0 h-full">
                                <Card className="border-none shadow-none bg-transparent">
                                    <CardContent className="p-0 space-y-4">
                                        {/* AI Analysis Content - Prioritized */}
                                        {incident.root_cause ? (
                                            <div className="rounded-lg p-4 border bg-zinc-900/50 border-zinc-800">
                                                <h3 className="text-sm font-semibold mb-2 text-zinc-100">Root Cause</h3>
                                                <p className="text-sm text-zinc-300 whitespace-pre-wrap">
                                                    {incident.root_cause}
                                                </p>
                                            </div>
                                        ) : (
                                            <div className="flex flex-col items-center justify-center p-8 text-muted-foreground border rounded-lg border-dashed">
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin mb-2" />
                                                <p>Analyzing incident...</p>
                                            </div>
                                        )}

                                        {incident.action_taken && (
                                            <div className="rounded-lg p-4 border bg-green-900/10 border-green-900/30">
                                                <div className="flex items-center mb-2">
                                                    <CheckCircle2 className="h-4 w-4 text-green-500 mr-2" />
                                                    <h3 className="text-sm font-semibold text-green-500">Action Taken</h3>
                                                </div>
                                                <p className="text-sm text-zinc-300">
                                                    {incident.action_taken}
                                                </p>
                                            </div>
                                        )}

                                        {incident.action_result && (
                                            <div className="rounded-lg p-4 border bg-blue-900/10 border-blue-900/30">
                                                 <div className="flex items-center mb-3">
                                                    <GitPullRequest className="h-4 w-4 text-blue-400 mr-2" />
                                                    <h3 className="text-sm font-semibold text-blue-400">
                                                        Code Fix
                                                    </h3>
                                                </div>

                                                <div className="space-y-3">
                                                     {incident.action_result.pr_files_changed && incident.action_result.pr_files_changed.length > 0 && (
                                                        <div className="space-y-2">
                                                            <p className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                                                                Changed Files
                                                            </p>
                                                            <div className="grid gap-1">
                                                                {incident.action_result.pr_files_changed.map((file, i) => (
                                                                    <div
                                                                        key={i}
                                                                        className={`text-xs font-mono p-2 rounded flex items-center justify-between cursor-pointer transition-colors border ${selectedFileDiff?.file === file ? 'bg-blue-500/20 border-blue-500/50 text-blue-200' : 'bg-zinc-900/50 border-zinc-800 text-zinc-300 hover:bg-zinc-800'}`}
                                                                        onClick={() => {
                                                                            setSelectedFileDiff({
                                                                                file: file,
                                                                                oldCode: `// Original content of ${file}\nfunction example() {\n  return "error";\n}`,
                                                                                newCode: `// Fixed content of ${file}\nfunction example() {\n  return "success";\n}`
                                                                            });
                                                                        }}
                                                                    >
                                                                        <span className="flex items-center truncate">
                                                                             <FileText className="h-3 w-3 mr-2 opacity-70" />
                                                                             {file}
                                                                        </span>
                                                                        <span className="text-[10px] bg-blue-500/20 text-blue-300 px-1.5 py-0.5 rounded">
                                                                            Diff
                                                                        </span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    )}

                                                    {incident.action_result.pr_url && (
                                                        <Button
                                                            size="sm"
                                                            variant="secondary"
                                                            className="w-full mt-2"
                                                            onClick={() => window.open(incident.action_result?.pr_url, '_blank')}
                                                        >
                                                            <ExternalLink className="mr-2 h-3 w-3" />
                                                            View Pull Request #{incident.action_result.pr_number}
                                                        </Button>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                    </CardContent>
                                </Card>
                            </TabsContent>

                            <TabsContent value="logs" className="m-0 h-full">
                                <Card className="border-none shadow-none bg-transparent h-full">
                                    <CardContent className="p-0 h-full">
                                        <ScrollArea className="h-full rounded-md border bg-zinc-950">
                                            <div className="divide-y divide-zinc-800">
                                                {logs.map((log) => (
                                                    <div key={log.id} className="p-3 text-sm">
                                                        <div className="flex items-center justify-between mb-1">
                                                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                                                                log.level === 'CRITICAL' ? 'bg-red-900/30 text-red-400' :
                                                                log.level === 'ERROR' ? 'bg-red-900/20 text-red-300' :
                                                                'bg-zinc-800 text-zinc-400'
                                                            }`}>
                                                                {log.level}
                                                            </span>
                                                            <span className="text-[10px] text-zinc-500 font-mono">
                                                                {new Date(log.timestamp).toLocaleTimeString()}
                                                            </span>
                                                        </div>
                                                        <p className="text-zinc-300 font-mono text-xs break-all">
                                                            {log.message}
                                                        </p>
                                                    </div>
                                                ))}
                                                {logs.length === 0 && (
                                                    <div className="p-8 text-center text-muted-foreground text-sm">
                                                        No logs found.
                                                    </div>
                                                )}
                                            </div>
                                        </ScrollArea>
                                    </CardContent>
                                </Card>
                            </TabsContent>

                            <TabsContent value="metadata" className="m-0 h-full">
                                <Card className="border-none shadow-none bg-transparent h-full">
                                    <CardContent className="p-0 h-full space-y-4">
                                         {incident.metadata_json !== null && (
                                            <div className="space-y-2">
                                                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Incident Metadata</h4>
                                                <div className="rounded-md bg-zinc-950 p-3 border border-zinc-800 overflow-auto max-h-[200px]">
                                                    <pre className="text-xs font-mono text-zinc-300">
                                                        {JSON.stringify(incident.metadata_json as object, null, 2)}
                                                    </pre>
                                                </div>
                                            </div>
                                         )}

                                         <div className="space-y-2">
                                            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Trace Data</h4>
                                            <div className="rounded-md bg-zinc-950 p-3 border border-zinc-800 overflow-auto max-h-[300px]">
                                                 {logs.filter(l => l.metadata_json).length > 0 ? (
                                                     logs.map(log => (
                                                         <div key={log.id} className="mb-4 last:mb-0">
                                                             <div className="text-[10px] text-zinc-500 mb-1">Log #{log.id}</div>
                                                             <pre className="text-xs font-mono text-zinc-300 whitespace-pre-wrap">
                                                                 {JSON.stringify(log.metadata_json, null, 2)}
                                                             </pre>
                                                         </div>
                                                     ))
                                                 ) : (
                                                     <p className="text-xs text-muted-foreground">No trace data available.</p>
                                                 )}
                                            </div>
                                         </div>
                                    </CardContent>
                                </Card>
                            </TabsContent>
                        </div>
                    </Tabs>
                </div>
            </div>

            {selectedFileDiff && (
                <div className="w-1/2 flex flex-col border-l h-full overflow-hidden bg-background">
                    <div className="flex items-center justify-between p-4 border-b">
                        <h3 className="font-semibold">{selectedFileDiff.file}</h3>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setSelectedFileDiff(null)}
                        >
                            Close
                        </Button>
                    </div>
                    <div className="flex-1 overflow-auto">
                        <CodeDiffViewer
                            oldCode={selectedFileDiff.oldCode}
                            newCode={selectedFileDiff.newCode}
                            language={selectedFileDiff.file.split('.').pop() || 'javascript'}
                        />
                    </div>
                </div>
            )}
        </div>
    );
}
