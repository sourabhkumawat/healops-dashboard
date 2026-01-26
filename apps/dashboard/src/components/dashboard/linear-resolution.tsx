'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { GitPullRequest, TrendingUp } from 'lucide-react';

interface LinearResolutionProps {
    linearStats: {
        total_attempts: number;
        claimed: number;
        analyzing: number;
        implementing: number;
        testing: number;
        completed: number;
        failed: number;
        success_rate: number;
        avg_resolution_time_seconds: number;
        avg_confidence_score: number;
    };
}

function formatDuration(seconds: number): string {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
}

export function LinearResolution({ linearStats }: LinearResolutionProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <GitPullRequest className="h-5 w-5" />
                    Linear Ticket Resolution
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Key Metrics */}
                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <p className="text-sm text-muted-foreground">
                            Success Rate
                        </p>
                        <p className="text-2xl font-bold flex items-center gap-1">
                            <TrendingUp className="h-5 w-5 text-green-500" />
                            {linearStats.success_rate.toFixed(1)}%
                        </p>
                    </div>
                    <div>
                        <p className="text-sm text-muted-foreground">
                            Avg Resolution Time
                        </p>
                        <p className="text-2xl font-bold">
                            {formatDuration(
                                linearStats.avg_resolution_time_seconds
                            )}
                        </p>
                    </div>
                </div>

                {/* Status Breakdown */}
                <div className="space-y-2">
                    <p className="text-sm font-medium">Active Attempts</p>
                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <span className="text-sm">Claimed</span>
                            <Badge variant="outline">
                                {linearStats.claimed}
                            </Badge>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-sm">Analyzing</span>
                            <Badge
                                variant="outline"
                                className="bg-blue-500/10 text-blue-500 border-blue-500/20"
                            >
                                {linearStats.analyzing}
                            </Badge>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-sm">Implementing</span>
                            <Badge
                                variant="outline"
                                className="bg-purple-500/10 text-purple-500 border-purple-500/20"
                            >
                                {linearStats.implementing}
                            </Badge>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-sm">Testing</span>
                            <Badge
                                variant="outline"
                                className="bg-yellow-500/10 text-yellow-500 border-yellow-500/20"
                            >
                                {linearStats.testing}
                            </Badge>
                        </div>
                    </div>
                </div>

                {/* Completion Stats */}
                <div className="space-y-2 pt-2 border-t">
                    <div className="flex items-center justify-between">
                        <span className="text-sm">Completed</span>
                        <Badge
                            variant="outline"
                            className="bg-green-500/10 text-green-500 border-green-500/20"
                        >
                            {linearStats.completed}
                        </Badge>
                    </div>
                    <div className="flex items-center justify-between">
                        <span className="text-sm">Failed</span>
                        <Badge
                            variant="outline"
                            className="bg-red-500/10 text-red-500 border-red-500/20"
                        >
                            {linearStats.failed}
                        </Badge>
                    </div>
                    {linearStats.avg_confidence_score > 0 && (
                        <div className="flex items-center justify-between">
                            <span className="text-sm">Avg Confidence</span>
                            <span className="text-sm font-medium">
                                {(
                                    linearStats.avg_confidence_score * 100
                                ).toFixed(0)}
                                %
                            </span>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
