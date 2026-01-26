/* eslint-disable @typescript-eslint/no-explicit-any */
'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
    AreaChart,
    Area,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer
} from 'recharts';

interface IncidentTrendsChartProps {
    dailyIncidents: Array<{
        date: string;
        count: number;
    }>;
}

// Custom tooltip for better UX
const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
        return (
            <div className="bg-background border border-border rounded-lg shadow-lg p-3">
                <p className="text-sm font-medium">
                    {payload[0].payload.displayDate}
                </p>
                <p className="text-sm text-muted-foreground">
                    <span className="font-semibold text-purple-500">
                        {payload[0].value}
                    </span>{' '}
                    incident{payload[0].value !== 1 ? 's' : ''}
                </p>
            </div>
        );
    }
    return null;
};

export function IncidentTrendsChart({
    dailyIncidents
}: IncidentTrendsChartProps) {
    // Format dates for display
    const formattedData = dailyIncidents.map((item) => ({
        ...item,
        displayDate: new Date(item.date).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric'
        })
    }));

    return (
        <Card className="bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-white">
                    <span className="text-lg">Incident Trends</span>
                    <span className="text-xs text-zinc-500 font-normal">
                        (Last 7 Days)
                    </span>
                </CardTitle>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={300}>
                    <AreaChart
                        data={formattedData}
                        margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
                    >
                        <defs>
                            <linearGradient
                                id="colorIncidents"
                                x1="0"
                                y1="0"
                                x2="0"
                                y2="1"
                            >
                                <stop
                                    offset="5%"
                                    stopColor="#8b5cf6"
                                    stopOpacity={0.3}
                                />
                                <stop
                                    offset="95%"
                                    stopColor="#8b5cf6"
                                    stopOpacity={0}
                                />
                            </linearGradient>
                        </defs>
                        <CartesianGrid
                            strokeDasharray="3 3"
                            stroke="hsl(var(--border))"
                            opacity={0.3}
                        />
                        <XAxis
                            dataKey="displayDate"
                            stroke="#ffffff"
                            fontSize={12}
                            tickLine={false}
                            axisLine={false}
                        />
                        <YAxis
                            stroke="#ffffff"
                            fontSize={12}
                            tickLine={false}
                            axisLine={false}
                            allowDecimals={false}
                        />
                        <Tooltip
                            content={<CustomTooltip />}
                            cursor={{
                                stroke: '#8b5cf6',
                                strokeWidth: 1,
                                strokeDasharray: '5 5'
                            }}
                        />
                        <Area
                            type="monotone"
                            dataKey="count"
                            stroke="#8b5cf6"
                            strokeWidth={3}
                            fill="url(#colorIncidents)"
                            name="Incidents"
                            animationDuration={1000}
                            animationEasing="ease-in-out"
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </CardContent>
        </Card>
    );
}
