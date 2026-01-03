'use client';

import { useEffect, useState, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Card, CardContent } from '@/components/ui/card';
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
    FileText,
    Activity,
    Code
} from 'lucide-react';
import { Incident } from '@/components/incident-table';
import { getIncident, updateIncidentStatus } from '@/actions/incidents';
import FileDiffCard from '@/components/FileDiffCard';
import { trackIncidentAnalysis, trackIncidentFetchError, analytics } from '@/lib/analytics';

interface LogEntry {
    id: number;
    timestamp: string;
    level: string;
    message: string;
    service_name: string;
    source: string;
    metadata_json: unknown;
}

interface ActionResult {
    pr_url?: string;
    pr_number?: number;
    pr_files_changed?: string[];
    changes?: Record<string, string>; // Filename -> New Content
    original_contents?: Record<string, string>; // Filename -> Original Content
}

interface IncidentWithAction extends Incident {
    action_result?: ActionResult;
}

export default function IncidentDetailsPage() {
    const params = useParams();
    const router = useRouter();
    const [data, setData] = useState<{
        incident: IncidentWithAction;
        logs: LogEntry[];
    } | null>(null);
    const [loading, setLoading] = useState(true);
    const [resolving, setResolving] = useState(false);
    const [analyzing, setAnalyzing] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const pollTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const fetchingIncidentRef = useRef(false);
    
    // Validate incident ID
    // params.id can be string or string[] in Next.js, ensure we handle both
    const incidentIdParam = Array.isArray(params.id) ? params.id[0] : params.id;
    const incidentId = incidentIdParam ? Number(incidentIdParam) : NaN;
    
    // Early return for invalid ID (after hooks, which is correct React pattern)
    if (!incidentIdParam || isNaN(incidentId) || incidentId <= 0) {
        return (
            <div className="flex h-full flex-col items-center justify-center space-y-4">
                <h2 className="text-xl font-bold">Invalid Incident ID</h2>
                <Button onClick={() => router.push('/incidents')}>
                    Back to Incidents
                </Button>
            </div>
        );
    }

    // Polling logic with exponential backoff
    useEffect(() => {
        let pollCount = 0;
        const MAX_POLL_ATTEMPTS = 50; // Increased for longer analysis times
        const INITIAL_POLL_INTERVAL = 2000; // Start with 2 seconds
        const MAX_POLL_INTERVAL = 10000; // Max 10 seconds between polls
        let currentPollInterval = INITIAL_POLL_INTERVAL;
        let isMounted = true; // Track if component is still mounted

        const fetchIncident = async (): Promise<boolean> => {
            if (fetchingIncidentRef.current) return false; // Prevent duplicate calls
            fetchingIncidentRef.current = true;
            try {
                const result = await getIncident(incidentId);
                
                // Check if component is still mounted before setting state
                if (!isMounted) {
                    return false;
                }
                
                if (result) {
                    setData(result);
                    setLoading(false);
                    setError(null); // Clear any previous errors

                    // Check if analysis is available
                    const rootCause = result.incident?.root_cause;
                    const hasRootCause = !!rootCause;
                    const hasAnalysisError = hasRootCause && rootCause && (
                        rootCause.includes("Analysis failed") ||
                        rootCause.includes("Analysis error") ||
                        rootCause.includes("not configured")
                    );

                    if (hasRootCause) {
                        // Analysis complete - stop polling
                        if (pollTimeoutRef.current) {
                            clearTimeout(pollTimeoutRef.current);
                            pollTimeoutRef.current = null;
                        }
                        setAnalyzing(false);
                        
                        // Set error state if analysis failed
                        if (hasAnalysisError && rootCause) {
                            setError(rootCause);
                        }
                        
                        return false; // Don't poll
                    } else {
                        // Still analyzing
                        setAnalyzing(true);
                        return true; // Continue polling
                    }
                } else {
                    setError("Incident not found");
                    setLoading(false);
                }
            } catch (error) {
                // Check if mounted before setting state
                if (!isMounted) {
                    return false;
                }
                
                console.error('Failed to fetch incident:', error);
                setError("Failed to load incident. Please try again.");
                setLoading(false);
                
                // Track error analytics
                const errorObj = error instanceof Error ? error : new Error(String(error));
                trackIncidentFetchError(incidentId, errorObj);
                analytics.error(errorObj, { incident_id: incidentId, context: 'initial_fetch' });
            } finally {
                fetchingIncidentRef.current = false;
            }
            return false;
        };

        // Recursive polling function with exponential backoff
        const scheduleNextPoll = (shouldPoll: boolean) => {
            // Check if component is still mounted
            if (!isMounted) {
                return;
            }
            
            if (!shouldPoll || pollCount >= MAX_POLL_ATTEMPTS) {
                if (pollCount >= MAX_POLL_ATTEMPTS && isMounted) {
                    console.warn('Max polling attempts reached. Stopping polling.');
                    setAnalyzing(false);
                    setError("Analysis is taking longer than expected. Please refresh the page.");
                }
                return;
            }

            // Exponential backoff: increase interval gradually, but cap at max
            currentPollInterval = Math.min(
                INITIAL_POLL_INTERVAL * Math.pow(1.2, pollCount),
                MAX_POLL_INTERVAL
            );

            pollTimeoutRef.current = setTimeout(async () => {
                // Check if component is still mounted before proceeding
                if (!isMounted) {
                    return;
                }
                
                if (fetchingIncidentRef.current) {
                    // If still fetching, reschedule
                    scheduleNextPoll(true);
                    return;
                }
                
                fetchingIncidentRef.current = true;
                pollCount++;

                try {
                    const result = await getIncident(incidentId);
                    
                    // Check again after async operation
                    if (!isMounted) {
                        return;
                    }
                    
                    if (result) {
                        setData(result);
                        setError(null);

                        const rootCause = result.incident?.root_cause;
                        const hasRootCause = !!rootCause;
                        const hasAnalysisError = hasRootCause && rootCause && (
                            rootCause.includes("Analysis failed") ||
                            rootCause.includes("Analysis error") ||
                            rootCause.includes("not configured")
                        );

                        if (hasRootCause) {
                            // Analysis complete!
                            // Log only in development
                            if (typeof process !== 'undefined' && process.env?.NODE_ENV === 'development') {
                                console.log(`AI analysis completed after ${pollCount} polls`);
                            }
                            setAnalyzing(false);
                            
                            if (hasAnalysisError && rootCause) {
                                setError(rootCause);
                            }
                            
                            // Track analytics
                            trackIncidentAnalysis(
                                incidentId,
                                !hasAnalysisError,
                                pollCount,
                                pollCount * currentPollInterval
                            );
                            
                            return; // Stop polling
                        } else {
                            // Still analyzing, continue polling
                            scheduleNextPoll(true);
                        }
                    } else {
                        // No result, but continue polling
                        scheduleNextPoll(true);
                    }
                } catch (error) {
                    // Check if mounted before setting state
                    if (!isMounted) {
                        return;
                    }
                    
                    console.error('Failed to fetch incident:', error);
                    // On error, continue polling but with longer interval
                    currentPollInterval = Math.min(currentPollInterval * 1.5, MAX_POLL_INTERVAL);
                    scheduleNextPoll(true);
                } finally {
                    fetchingIncidentRef.current = false;
                }
            }, currentPollInterval);
        };

        // Initial fetch and trigger analysis if needed
        fetchIncident().then((shouldPoll) => {
            if (shouldPoll) {
                // Log only in development
                if (typeof process !== 'undefined' && process.env?.NODE_ENV === 'development') {
                    console.log('Starting adaptive polling for AI analysis...');
                }
                scheduleNextPoll(true);
            }
        });

        return () => {
            // Mark component as unmounted
            isMounted = false;
            
            // Clear any pending timeouts
            if (pollTimeoutRef.current) {
                clearTimeout(pollTimeoutRef.current);
                pollTimeoutRef.current = null;
            }
            
            // Reset fetching flag
            fetchingIncidentRef.current = false;
        };
    }, [incidentId]);

    const handleResolve = async () => {
        setResolving(true);
        try {
            const updatedIncident = await updateIncidentStatus(
                incidentId,
                'RESOLVED'
            );

            if (updatedIncident) {
                setData((prev) =>
                    prev ? { ...prev, incident: updatedIncident } : null
                );
            } else {
                setError("Failed to resolve incident. Please try again.");
            }
        } catch (error) {
            console.error('Failed to resolve incident:', error);
            setError("Failed to resolve incident. Please try again.");
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
    const hasFilesChanged =
        incident.action_result?.pr_files_changed &&
        incident.action_result.pr_files_changed.length > 0;
    const hasChanges =
        incident.action_result?.changes &&
        Object.keys(incident.action_result.changes).length > 0 &&
        Object.values(incident.action_result.changes).some(v => v && v.trim().length > 0); // Ensure not empty
    const showRightPane = analyzing || hasFilesChanged || hasChanges;

    return (
        <div className="flex h-[calc(100vh-65px)] overflow-hidden">
            {/* Left Pane - Incident Details */}
            <div
                className={`${
                    showRightPane ? 'w-1/2 border-r' : 'w-full'
                } flex flex-col h-full overflow-hidden bg-background`}
            >
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
                                    <Badge
                                        variant="outline"
                                        className="text-xs"
                                    >
                                        {incident.service_name}
                                    </Badge>
                                    <span className="text-xs text-muted-foreground">
                                        {new Date(
                                            incident.created_at
                                        ).toLocaleString()}
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
                                <Button
                                    size="sm"
                                    onClick={handleResolve}
                                    disabled={resolving}
                                >
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
                    <Tabs
                        defaultValue="analysis"
                        className="h-full flex flex-col"
                    >
                        <TabsList className="grid w-full grid-cols-3 mb-4">
                            <TabsTrigger
                                value="analysis"
                                className="flex items-center gap-2"
                            >
                                <Activity className="h-4 w-4" />
                                AI Analysis
                            </TabsTrigger>
                            <TabsTrigger
                                value="logs"
                                className="flex items-center gap-2"
                            >
                                <FileText className="h-4 w-4" />
                                Logs
                            </TabsTrigger>
                            <TabsTrigger
                                value="metadata"
                                className="flex items-center gap-2"
                            >
                                <Code className="h-4 w-4" />
                                Raw Data
                            </TabsTrigger>
                        </TabsList>

                        <div className="flex-1 overflow-auto">
                            <TabsContent
                                value="analysis"
                                className="space-y-4 m-0 h-full"
                            >
                                <Card className="border-none shadow-none bg-transparent">
                                    <CardContent className="p-0 space-y-4">
                                        {/* AI Analysis Content - Prioritized */}
                                        {error && (
                                            <div className="rounded-lg p-4 border bg-red-900/20 border-red-900/50">
                                                <h3 className="text-sm font-semibold mb-2 text-red-400">
                                                    Analysis Error
                                                </h3>
                                                <p className="text-sm text-red-300 whitespace-pre-wrap">
                                                    {error}
                                                </p>
                                            </div>
                                        )}
                                        {incident.root_cause && !error ? (
                                            <div className="rounded-lg p-4 border bg-zinc-900/50 border-zinc-800">
                                                <h3 className="text-sm font-semibold mb-2 text-zinc-100">
                                                    Root Cause
                                                </h3>
                                                <p className="text-sm text-zinc-300 whitespace-pre-wrap">
                                                    {incident.root_cause}
                                                </p>
                                            </div>
                                        ) : !error ? (
                                            <div className="flex flex-col items-center justify-center p-8 text-muted-foreground border rounded-lg border-dashed">
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin mb-2" />
                                                <p>Analyzing incident...</p>
                                            </div>
                                        ) : null}

                                        {incident.action_taken && (
                                            <div className="rounded-lg p-4 border bg-green-900/10 border-green-900/30">
                                                <div className="flex items-center mb-2">
                                                    <CheckCircle2 className="h-4 w-4 text-green-500 mr-2" />
                                                    <h3 className="text-sm font-semibold text-green-500">
                                                        Action Taken
                                                    </h3>
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
                                                    {/* We removed the individual file list here because it's now in the right panel */}

                                                    {incident.action_result
                                                        .pr_url && (
                                                        <Button
                                                            size="sm"
                                                            variant="secondary"
                                                            className="w-full mt-2"
                                                            onClick={() =>
                                                                window.open(
                                                                    incident
                                                                        .action_result
                                                                        ?.pr_url,
                                                                    '_blank'
                                                                )
                                                            }
                                                        >
                                                            <ExternalLink className="mr-2 h-3 w-3" />
                                                            View Pull Request #
                                                            {
                                                                incident
                                                                    .action_result
                                                                    .pr_number
                                                            }
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
                                                    <div
                                                        key={log.id}
                                                        className="p-3 text-sm"
                                                    >
                                                        <div className="flex items-center justify-between mb-1">
                                                            <span
                                                                className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                                                                    log.level ===
                                                                    'CRITICAL'
                                                                        ? 'bg-red-900/30 text-red-400'
                                                                        : log.level ===
                                                                          'ERROR'
                                                                        ? 'bg-red-900/20 text-red-300'
                                                                        : 'bg-zinc-800 text-zinc-400'
                                                                }`}
                                                            >
                                                                {log.level}
                                                            </span>
                                                            <span className="text-[10px] text-zinc-500 font-mono">
                                                                {new Date(
                                                                    log.timestamp
                                                                ).toLocaleTimeString()}
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

                            <TabsContent
                                value="metadata"
                                className="m-0 h-full"
                            >
                                <Card className="border-none shadow-none bg-transparent h-full">
                                    <CardContent className="p-0 h-full space-y-4">
                                        {incident.metadata_json !== null && (
                                            <div className="space-y-2">
                                                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                                    Incident Metadata
                                                </h4>
                                                <div className="rounded-md bg-zinc-950 p-3 border border-zinc-800 overflow-auto max-h-[200px]">
                                                    <pre className="text-xs font-mono text-zinc-300">
                                                        {JSON.stringify(
                                                            incident.metadata_json as object,
                                                            null,
                                                            2
                                                        )}
                                                    </pre>
                                                </div>
                                            </div>
                                        )}

                                        <div className="space-y-2">
                                            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                                Trace Data
                                            </h4>
                                            <div className="rounded-md bg-zinc-950 p-3 border border-zinc-800 overflow-auto max-h-[300px]">
                                                {logs.filter(
                                                    (l) => l.metadata_json
                                                ).length > 0 ? (
                                                    logs.map((log) => (
                                                        <div
                                                            key={log.id}
                                                            className="mb-4 last:mb-0"
                                                        >
                                                            <div className="text-[10px] text-zinc-500 mb-1">
                                                                Log #{log.id}
                                                            </div>
                                                            <pre className="text-xs font-mono text-zinc-300 whitespace-pre-wrap">
                                                                {JSON.stringify(
                                                                    log.metadata_json,
                                                                    null,
                                                                    2
                                                                )}
                                                            </pre>
                                                        </div>
                                                    ))
                                                ) : (
                                                    <p className="text-xs text-muted-foreground">
                                                        No trace data available.
                                                    </p>
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

            {/* Right Pane - Loading or File Diffs */}
            {showRightPane && (
                <div className="w-1/2 flex flex-col border-l h-full overflow-hidden bg-background">
                    <div className="flex items-center justify-between p-4 border-b">
                        <div className="flex items-center gap-2">
                            <GitPullRequest className="h-4 w-4 text-muted-foreground" />
                            <h3 className="font-semibold">Files Changed</h3>
                            {hasFilesChanged && (
                                <Badge variant="secondary" className="text-xs">
                                    {incident.action_result?.pr_files_changed
                                        ?.length || 0}
                                </Badge>
                            )}
                            {analyzing && !hasFilesChanged && (
                                <Badge
                                    variant="outline"
                                    className="text-xs animate-pulse"
                                >
                                    Generating...
                                </Badge>
                            )}
                        </div>
                    </div>

                    {analyzing && !hasFilesChanged && !hasChanges ? (
                        // Loading state while agents are working
                        <div className="flex-1 flex flex-col items-center justify-center p-8 bg-zinc-950/30">
                            <div className="flex flex-col items-center gap-4 text-center">
                                <Loader2 className="h-8 w-8 animate-spin text-blue-400" />
                                <div className="space-y-2">
                                    <h3 className="text-sm font-semibold text-zinc-200">
                                        Agents are analyzing and fixing...
                                    </h3>
                                    <p className="text-xs text-zinc-400 max-w-sm">
                                        Our AI agents are exploring the
                                        codebase, generating fixes, and
                                        validating changes. Code differences
                                        will appear here once ready.
                                    </p>
                                </div>
                                <div className="flex flex-col gap-2 mt-4 text-xs text-zinc-500">
                                    <div className="flex items-center gap-2">
                                        <div className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
                                        <span>
                                            Exploring codebase structure
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <div
                                            className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse"
                                            style={{ animationDelay: '0.2s' }}
                                        />
                                        <span>Analyzing dependencies</span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <div
                                            className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse"
                                            style={{ animationDelay: '0.4s' }}
                                        />
                                        <span>Generating code fixes</span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <div
                                            className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse"
                                            style={{ animationDelay: '0.6s' }}
                                        />
                                        <span>Validating changes</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : (
                        // Show file diffs when available
                        <ScrollArea className="flex-1 p-4 bg-zinc-950/30">
                            <div className="space-y-4 pb-10">
                                {hasFilesChanged &&
                                incident.action_result?.pr_files_changed ? (
                                    // Use pr_files_changed if available (ordered list)
                                    incident.action_result.pr_files_changed.map(
                                        (file, i) => {
                                            const newCode =
                                                incident.action_result
                                                    ?.changes?.[file] ||
                                                `// No content available for ${file}`;
                                            const oldCode =
                                                incident.action_result
                                                    ?.original_contents?.[file];
                                            return (
                                                <FileDiffCard
                                                    key={i}
                                                    filename={file}
                                                    newCode={newCode}
                                                    oldCode={oldCode}
                                                />
                                            );
                                        }
                                    )
                                ) : hasChanges ? (
                                    // Fallback to changes keys if pr_files_changed not available
                                    Object.keys(
                                        incident.action_result?.changes || {}
                                    ).map((file, i) => {
                                        const newCode =
                                            incident.action_result?.changes?.[
                                                file
                                            ] ||
                                            `// No content available for ${file}`;
                                        const oldCode =
                                            incident.action_result
                                                ?.original_contents?.[file];
                                        return (
                                            <FileDiffCard
                                                key={i}
                                                filename={file}
                                                newCode={newCode}
                                                oldCode={oldCode}
                                            />
                                        );
                                    })
                                ) : (
                                    <div className="flex flex-col items-center justify-center p-8 text-center text-muted-foreground">
                                        <FileText className="h-8 w-8 mb-2 opacity-50" />
                                        <p className="text-sm">
                                            No file changes available
                                        </p>
                                    </div>
                                )}
                            </div>
                        </ScrollArea>
                    )}
                </div>
            )}
        </div>
    );
}
