import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { IncidentTable } from "@/components/incident-table";
import { LiveLogs } from "@/components/live-logs";
import { mockIncidents } from "@/lib/mock-data";
import { Activity, Cpu, HardDrive, Server } from "lucide-react";

export default async function DashboardPage() {
  const activeIncidents = mockIncidents.filter(i => i.status !== "RESOLVED").length;
  const resolvedIncidents = mockIncidents.filter(i => i.status === "RESOLVED").length;
  
  return (
    <div className="flex-1 space-y-4">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">System Overview</h2>
      </div>
      
      {/* System Health Metrics */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">System Status</CardTitle>
            <Activity className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-500">OPERATIONAL</div>
            <p className="text-xs text-muted-foreground">All systems normal</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">CPU Usage (Avg)</CardTitle>
            <Cpu className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">42%</div>
            <p className="text-xs text-muted-foreground">+2% from last hour</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Memory Usage</CardTitle>
            <HardDrive className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">64%</div>
            <p className="text-xs text-muted-foreground">12GB / 18GB Used</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Services</CardTitle>
            <Server className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">128</div>
            <p className="text-xs text-muted-foreground">12 Unhealthy</p>
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
            <IncidentTable incidents={mockIncidents} />
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
