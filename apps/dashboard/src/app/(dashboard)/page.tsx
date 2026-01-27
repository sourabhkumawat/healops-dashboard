'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { IncidentTable, Incident } from '@/components/incident-table';
import { LiveLogs } from '@/components/live-logs';
import type { SystemStats } from '@/lib/types/stats';
import { fetchClient } from '@/lib/client-api';
import {
    Activity,
    HardDrive,
    Server,
    AlertTriangle,
    Clock,
    GitPullRequest,
    TrendingUp,
    Zap,
    Loader2
} from 'lucide-react';
import { SeverityChart } from '@/components/dashboard/severity-chart';
import { IncidentTrendsChart } from '@/components/dashboard/incident-trends-chart';

// Format number: small numbers with commas, big numbers in k/m format
function formatNumber(num: number): string {
    if (num < 1000) {
        return num.toLocaleString();
    } else if (num < 1000000) {
        const k = num / 1000;
        return k % 1 === 0 ? `${k}k` : `${k.toFixed(1)}k`;
    } else {
        const m = num / 1000000;
        return m % 1 === 0 ? `${m}m` : `${m.toFixed(1)}m`;
    }
}

// Format duration from seconds to human-readable format
function formatDuration(seconds: number): string {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
    return `${(seconds / 86400).toFixed(1)}d`;
}

export default function DashboardPage() {
    const [recentIncidents, setRecentIncidents] = useState<Incident[]>([]);
    const [stats, setStats] = useState<SystemStats | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function fetchData() {
            try {
                // Fetch stats
                const statsPromise = fetchClient('/stats')
                    .then((res) => (res.ok ? res.json() : null))
                    .catch((err) => {
                        console.warn('Failed to fetch stats:', err);
                        return null;
                    });

                // Fetch incidents (page_size=10 matches the previous getRecentIncidents(10) call)
                const incidentsPromise = fetchClient('/incidents?page_size=10')
                    .then((res) => (res.ok ? res.json() : []))
                    .then((data) => {
                        // Handle both array and paginated response
                        if (Array.isArray(data)) return data;
                        // If paginated response { data: [...], pagination: {...} }
                        if (data && Array.isArray(data.data)) return data.data;
                        return [];
                    })
                    .catch((err) => {
                        console.warn('Failed to fetch incidents:', err);
                        return [];
                    });

                const [statsData, incidentsData] = await Promise.all([
                    statsPromise,
                    incidentsPromise
                ]);

                setStats(statsData);
                setRecentIncidents(incidentsData);
            } catch (error) {
                console.error('Error fetching dashboard data:', error);
            } finally {
                setLoading(false);
            }
        }

        fetchData();
    }, []);

    if (loading) {
        return (
            <div className="flex-1 h-screen flex items-center justify-center p-6 bg-black">
                <Loader2 className="h-8 w-8 text-green-500 animate-spin" />
            </div>
        );
    }

    // Fallback values if stats are not available
    const systemStatus = stats?.system_status || 'OPERATIONAL';
    const activeIncidents = stats?.active_incidents || 0;
    const totalIncidents = stats?.total_incidents || 0;
    const resolvedIncidents = stats?.resolved_incidents || 0;
    const totalServices = stats?.total_services || 0;
    const unhealthyServices = stats?.unhealthy_services || 0;
    const errorLogsCount = stats?.error_logs_count || 0;

    // New metrics
    const mttrSeconds = stats?.mttr_seconds || 0;
    const autoFixSuccessRate = stats?.auto_fix_success_rate || 0;
    const prStats = stats?.pr_stats || {
        total: 0,
        draft: 0,
        ready: 0,
        pending_qa: 0,
        avg_review_time_seconds: 0
    };
    const trends = stats?.trends || {
        incidents_7d: 0,
        incidents_30d: 0,
        errors_7d: 0,
        errors_30d: 0,
        daily_incidents: []
    };

    // Map system status to color classes
    const getSystemStatusColor = (status: string) => {
        switch (status) {
            case 'CRITICAL':
                return 'text-red-500';
            case 'DEGRADED':
                return 'text-yellow-500';
            case 'OPERATIONAL':
            default:
                return 'text-green-500';
        }
    };

    const systemStatusColor = getSystemStatusColor(systemStatus);

    return (
        <div className="flex-1 p-6 h-[calc(100vh-4rem)] overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight text-white">
                        System Overview
                    </h2>
                    <p className="text-sm text-muted-foreground mt-1">
                        Real-time monitoring and incident management
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-12 gap-6 h-full pb-20">
                {/* Left Column: Metrics List (Span 3) - Single "Accounts" style card */}
                <div className="col-span-12 lg:col-span-3 h-full overflow-hidden">
                    <Card className="h-full bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 flex flex-col">
                        <CardHeader>
                            <CardTitle className="text-xl text-white flex items-center justify-between">
                                <span>Metrics</span>
                                <Activity
                                    className={`h-5 w-5 ${systemStatusColor}`}
                                />
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="flex-1 overflow-y-auto space-y-6 pr-6">
                            {/* Group 1: System Health */}
                            <div className="space-y-3">
                                <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                                    System Health
                                </h4>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div
                                            className={`p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800`}
                                        >
                                            <Activity
                                                className={`h-4 w-4 ${systemStatusColor}`}
                                            />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                Status
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Overall Health
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p
                                            className={`text-sm font-bold ${systemStatusColor}`}
                                        >
                                            {systemStatus}
                                        </p>
                                    </div>
                                </div>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <AlertTriangle className="h-4 w-4 text-orange-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                Incidents
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Active
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-white">
                                            {formatNumber(activeIncidents)}
                                        </p>
                                        <p className="text-[10px] text-zinc-500">
                                            {formatNumber(resolvedIncidents)}{' '}
                                            resolved
                                        </p>
                                    </div>
                                </div>
                            </div>

                            <div className="h-px bg-zinc-800/50" />

                            {/* Group 2: Performance */}
                            <div className="space-y-3">
                                <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                                    Performance
                                </h4>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <HardDrive className="h-4 w-4 text-red-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                Error Logs
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Critical
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-white">
                                            {formatNumber(errorLogsCount)}
                                        </p>
                                    </div>
                                </div>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <Server className="h-4 w-4 text-blue-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                Services
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Active
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-white">
                                            {formatNumber(totalServices)}
                                        </p>
                                        <p className="text-[10px] text-zinc-500">
                                            {unhealthyServices > 0
                                                ? `${unhealthyServices} unhealthy`
                                                : 'All healthy'}
                                        </p>
                                    </div>
                                </div>
                            </div>

                            <div className="h-px bg-zinc-800/50" />

                            {/* Group 3: Efficiency */}
                            <div className="space-y-3">
                                <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                                    Efficiency
                                </h4>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <Clock className="h-4 w-4 text-purple-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                MTTR
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Avg Resolution
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-white">
                                            {formatDuration(mttrSeconds)}
                                        </p>
                                    </div>
                                </div>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <Zap className="h-4 w-4 text-green-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                Auto-Fix
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Success Rate
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-green-500">
                                            {autoFixSuccessRate.toFixed(1)}%
                                        </p>
                                        <p className="text-[10px] text-zinc-500">
                                            {stats?.successful_prs || 0} fixed
                                        </p>
                                    </div>
                                </div>
                            </div>

                            <div className="h-px bg-zinc-800/50" />

                            {/* Group 4: Developement */}
                            <div className="space-y-3">
                                <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                                    Development
                                </h4>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <GitPullRequest className="h-4 w-4 text-blue-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                PRs
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                Total Created
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-white">
                                            {formatNumber(prStats.total)}
                                        </p>
                                        <p className="text-[10px] text-zinc-500">
                                            {prStats.ready} ready
                                        </p>
                                    </div>
                                </div>

                                <div className="flex items-center justify-between group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-md bg-zinc-900/50 group-hover:bg-zinc-800 transition-colors border border-zinc-800">
                                            <TrendingUp className="h-4 w-4 text-yellow-500" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium text-zinc-200">
                                                Pending QA
                                            </p>
                                            <p className="text-xs text-zinc-500">
                                                In Review
                                            </p>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-sm font-bold text-white">
                                            {formatNumber(prStats.pending_qa)}
                                        </p>
                                        <p className="text-[10px] text-zinc-500">
                                            {formatDuration(
                                                prStats.avg_review_time_seconds
                                            )}{' '}
                                            wait
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>

                {/* Right Column: Charts / Main Content (Span 9) */}
                <div className="col-span-12 lg:col-span-9 space-y-6 overflow-y-auto pr-2 pb-6">
                    {/* Top Row: Visualizations - Side by Side */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div className="h-full">
                            <h3 className="text-lg font-semibold text-zinc-100 mb-2">
                                Severity Distribution
                            </h3>
                            <SeverityChart
                                critical={stats?.critical_incidents || 0}
                                high={stats?.high_incidents || 0}
                                medium={stats?.medium_incidents || 0}
                                low={stats?.low_incidents || 0}
                            />
                        </div>

                        <div className="h-full">
                            <h3 className="text-lg font-semibold text-zinc-100 mb-2">
                                Incident Trends
                            </h3>
                            <IncidentTrendsChart
                                dailyIncidents={trends.daily_incidents}
                            />
                        </div>
                    </div>

                    {/* Bottom Row: Recent Incidents */}
                    <div className="grid grid-cols-1 h-[500px]">
                        <div className="flex flex-col h-full">
                            <h3 className="text-lg font-semibold text-zinc-100 mb-2">
                                Recent Incidents
                            </h3>
                            <Card className="flex-1 bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800 overflow-hidden flex flex-col">
                                <CardHeader className="p-4 border-b border-zinc-800/50">
                                    <CardTitle className="text-white text-base">
                                        Latest Activity
                                    </CardTitle>
                                </CardHeader>
                                <CardContent className="p-0 flex-1 overflow-auto">
                                    <IncidentTable
                                        incidents={recentIncidents}
                                    />
                                </CardContent>
                            </Card>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
