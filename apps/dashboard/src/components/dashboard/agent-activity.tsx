'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Users, CheckCircle2 } from 'lucide-react';

interface AgentActivityProps {
    agentStats: {
        total_agents: number;
        available: number;
        working: number;
        idle: number;
        current_tasks: Array<{
            agent_name: string;
            task: string;
        }>;
        total_completed_tasks: number;
    };
}

export function AgentActivity({ agentStats }: AgentActivityProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Users className="h-5 w-5" />
                    Agent Activity
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Agent Status Summary */}
                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <p className="text-sm text-muted-foreground">
                            Total Agents
                        </p>
                        <p className="text-2xl font-bold">
                            {agentStats.total_agents}
                        </p>
                    </div>
                    <div>
                        <p className="text-sm text-muted-foreground">
                            Completed Tasks
                        </p>
                        <p className="text-2xl font-bold flex items-center gap-1">
                            <CheckCircle2 className="h-5 w-5 text-green-500" />
                            {agentStats.total_completed_tasks}
                        </p>
                    </div>
                </div>

                {/* Status Breakdown */}
                <div className="space-y-2">
                    <div className="flex items-center justify-between">
                        <span className="text-sm">Available</span>
                        <Badge
                            variant="outline"
                            className="bg-green-500/10 text-green-500 border-green-500/20"
                        >
                            {agentStats.available}
                        </Badge>
                    </div>
                    <div className="flex items-center justify-between">
                        <span className="text-sm">Working</span>
                        <Badge
                            variant="outline"
                            className="bg-blue-500/10 text-blue-500 border-blue-500/20"
                        >
                            {agentStats.working}
                        </Badge>
                    </div>
                    <div className="flex items-center justify-between">
                        <span className="text-sm">Idle</span>
                        <Badge
                            variant="outline"
                            className="bg-gray-500/10 text-gray-500 border-gray-500/20"
                        >
                            {agentStats.idle}
                        </Badge>
                    </div>
                </div>

                {/* Current Tasks */}
                {agentStats.current_tasks.length > 0 && (
                    <div className="space-y-2 pt-2 border-t">
                        <p className="text-sm font-medium">Current Tasks</p>
                        <div className="space-y-2 max-h-[200px] overflow-y-auto">
                            {agentStats.current_tasks.map((task, idx) => (
                                <div
                                    key={idx}
                                    className="text-xs bg-muted p-2 rounded"
                                >
                                    <p className="font-medium">
                                        {task.agent_name}
                                    </p>
                                    <p className="text-muted-foreground truncate">
                                        {task.task}
                                    </p>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
