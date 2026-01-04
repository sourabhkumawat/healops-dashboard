import { Card, CardContent, CardHeader, CardTitle } from '@healops/ui';
import { IncidentTable } from '@/features/incidents/components/IncidentTable';
import { LiveLogs } from '@/components/live-logs';
import { getRecentIncidents } from '@/actions/incidents';
import { getSystemStats } from '@/actions/stats';
import { Activity, HardDrive, Server, AlertTriangle } from 'lucide-react';

export default async function DashboardPage() {
    const recentIncidents = await getRecentIncidents(10);
    const stats = await getSystemStats();

    // Fallback values if stats are not available
    const systemStatus = stats?.system_status || 'OPERATIONAL';
    const activeIncidents = stats?.active_incidents || 0;
    const totalIncidents = stats?.total_incidents || 0;
    const resolvedIncidents = stats?.resolved_incidents || 0;
    const totalServices = stats?.total_services || 0;
    const unhealthyServices = stats?.unhealthy_services || 0;
    const errorLogsCount = stats?.error_logs_count || 0;

    // Map system status to color classes (ensures Tailwind detects them during build)
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
        <div className="flex-1 space-y-4">
            <div className="flex items-center justify-between space-y-2">
                <h2 className="text-3xl font-bold tracking-tight">
                    System Overview
                </h2>
            </div>

            {/* System Health Metrics */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">
                            System Status
                        </CardTitle>
                        <Activity className={`h-4 w-4 ${systemStatusColor}`} />
                    </CardHeader>
                    <CardContent>
                        <div
                            className={`text-2xl font-bold ${systemStatusColor}`}
                        >
                            {systemStatus}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {systemStatus === 'OPERATIONAL'
                                ? 'All systems normal'
                                : systemStatus === 'CRITICAL'
                                ? `${activeIncidents} active incident${
                                      activeIncidents !== 1 ? 's' : ''
                                  }`
                                : `${activeIncidents} active incident${
                                      activeIncidents !== 1 ? 's' : ''
                                  }`}
                        </p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">
                            Active Incidents
                        </CardTitle>
                        <AlertTriangle className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">
                            {activeIncidents}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {totalIncidents > 0
                                ? `${resolvedIncidents} resolved of ${totalIncidents} total`
                                : 'No incidents recorded'}
                        </p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">
                            Error Logs
                        </CardTitle>
                        <HardDrive className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">
                            {errorLogsCount}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {errorLogsCount > 0
                                ? 'Errors and critical logs tracked'
                                : 'No error logs recorded'}
                        </p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">
                            Active Services
                        </CardTitle>
                        <Server className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">
                            {totalServices}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {unhealthyServices > 0
                                ? `${unhealthyServices} Unhealthy`
                                : totalServices > 0
                                ? 'All services healthy'
                                : 'No services registered'}
                        </p>
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-4 md:grid-cols-1 lg:grid-cols-7">
                {/* Main Incident Feed */}
                <Card className="col-span-4 lg:col-span-4">
                    <CardHeader>
                        <CardTitle>Recent Incidents</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <IncidentTable incidents={recentIncidents} />
                    </CardContent>
                </Card>

                {/* Live Logs Console */}
                <div className="col-span-3 lg:col-span-3">
                    <LiveLogs />
                </div>
            </div>
        </div>
    );
}
