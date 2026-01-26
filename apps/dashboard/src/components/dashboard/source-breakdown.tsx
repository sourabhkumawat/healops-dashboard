'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer
} from 'recharts';
import { AlertCircle } from 'lucide-react';

interface SourceBreakdownProps {
    incidentsBySource: Record<string, number>;
    mostAffectedServices: Array<{
        service: string;
        incident_count: number;
    }>;
    errorDistributionByService: Array<{
        service: string;
        error_count: number;
    }>;
}

export function SourceBreakdown({
    incidentsBySource,
    mostAffectedServices,
    errorDistributionByService
}: SourceBreakdownProps) {
    // Format source data for chart
    const sourceData = Object.entries(incidentsBySource).map(
        ([source, count]) => ({
            source: source.charAt(0).toUpperCase() + source.slice(1),
            count
        })
    );

    return (
        <div className="space-y-4">
            {/* Incidents by Source */}
            <Card>
                <CardHeader>
                    <CardTitle>Incidents by Source</CardTitle>
                </CardHeader>
                <CardContent>
                    {sourceData.length > 0 ? (
                        <ResponsiveContainer width="100%" height={200}>
                            <BarChart data={sourceData}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="source" />
                                <YAxis />
                                <Tooltip />
                                <Bar dataKey="count" fill="#8b5cf6" />
                            </BarChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="flex items-center justify-center h-[200px] text-muted-foreground">
                            No data available
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Most Affected Services */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <AlertCircle className="h-5 w-5" />
                        Most Affected Services
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {mostAffectedServices.length > 0 ? (
                        <div className="space-y-2">
                            {mostAffectedServices.map((service, idx) => (
                                <div
                                    key={idx}
                                    className="flex items-center justify-between p-2 bg-muted rounded"
                                >
                                    <span className="text-sm font-medium truncate">
                                        {service.service}
                                    </span>
                                    <span className="text-sm text-muted-foreground">
                                        {service.incident_count} incident
                                        {service.incident_count !== 1
                                            ? 's'
                                            : ''}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-sm text-muted-foreground text-center py-4">
                            No incidents recorded
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Error Distribution */}
            <Card>
                <CardHeader>
                    <CardTitle>Error Distribution by Service</CardTitle>
                </CardHeader>
                <CardContent>
                    {errorDistributionByService.length > 0 ? (
                        <div className="space-y-2">
                            {errorDistributionByService.map((service, idx) => (
                                <div
                                    key={idx}
                                    className="flex items-center justify-between p-2 bg-muted rounded"
                                >
                                    <span className="text-sm font-medium truncate">
                                        {service.service}
                                    </span>
                                    <span className="text-sm text-red-500 font-medium">
                                        {service.error_count} error
                                        {service.error_count !== 1 ? 's' : ''}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-sm text-muted-foreground text-center py-4">
                            No errors recorded
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
