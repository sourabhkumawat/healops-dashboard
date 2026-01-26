/* eslint-disable @typescript-eslint/no-explicit-any */
'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
    PieChart,
    Pie,
    Cell,
    ResponsiveContainer,
    Tooltip,
    Legend
} from 'recharts';

interface SeverityChartProps {
    critical: number;
    high: number;
    medium: number;
    low: number;
}

const COLORS = {
    CRITICAL: '#ef4444',
    HIGH: '#f97316',
    MEDIUM: '#eab308',
    LOW: '#22c55e'
};

// Custom tooltip
const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
        const data = payload[0];
        return (
            <div className="bg-background border border-border rounded-lg shadow-lg p-3">
                <p className="text-sm font-medium">{data.name}</p>
                <p className="text-sm">
                    <span
                        className="font-semibold"
                        style={{ color: data.payload.color }}
                    >
                        {data.value}
                    </span>{' '}
                    incident{data.value !== 1 ? 's' : ''}
                    <span className="text-muted-foreground ml-1">
                        ({((data.value / data.payload.total) * 100).toFixed(1)}
                        %)
                    </span>
                </p>
            </div>
        );
    }
    return null;
};

// Custom label with better styling
const renderCustomLabel = (props: any) => {
    const { cx, cy, midAngle, innerRadius, outerRadius, percent } = props;
    const RADIAN = Math.PI / 180;
    const radius = innerRadius + (outerRadius - innerRadius) * 0.5;
    const x = cx + radius * Math.cos(-midAngle * RADIAN);
    const y = cy + radius * Math.sin(-midAngle * RADIAN);

    // Only show label if percentage is > 5%
    if (percent < 0.05) return null;

    return (
        <text
            x={x}
            y={y}
            fill="white"
            textAnchor={x > cx ? 'start' : 'end'}
            dominantBaseline="central"
            className="font-semibold text-sm"
            style={{ textShadow: '0 1px 3px rgba(0,0,0,0.5)' }}
        >
            {`${(percent * 100).toFixed(0)}%`}
        </text>
    );
};

// Custom legend with modern styling
const renderLegend = (props: any) => {
    const { payload } = props;
    return (
        <div className="flex flex-wrap justify-center gap-4 mt-4">
            {payload.map((entry: any, index: number) => (
                <div
                    key={`legend-${index}`}
                    className="flex items-center gap-2"
                >
                    <div
                        className="w-3 h-3 rounded-full"
                        style={{ backgroundColor: entry.color }}
                    />
                    <span className="text-sm text-white font-medium">
                        {entry.value}: {entry.payload.value}
                    </span>
                </div>
            ))}
        </div>
    );
};

export function SeverityChart({
    critical,
    high,
    medium,
    low
}: SeverityChartProps) {
    const total = critical + high + medium + low;

    const data = [
        { name: 'Critical', value: critical, color: COLORS.CRITICAL, total },
        { name: 'High', value: high, color: COLORS.HIGH, total },
        { name: 'Medium', value: medium, color: COLORS.MEDIUM, total },
        { name: 'Low', value: low, color: COLORS.LOW, total }
    ].filter((item) => item.value > 0);

    if (total === 0) {
        return (
            <Card className="bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-white">
                        <span className="text-lg">Incidents by Severity</span>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-center h-[350px] text-zinc-500">
                        No incidents recorded
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="bg-gradient-to-br from-zinc-900 to-zinc-950 border-zinc-800">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-white">
                    <span className="text-lg">Incidents by Severity</span>
                    <span className="text-xs text-zinc-500 font-normal">
                        ({total} total)
                    </span>
                </CardTitle>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={350}>
                    <PieChart>
                        <defs>
                            {data.map((entry, index) => (
                                <filter
                                    key={`shadow-${index}`}
                                    id={`shadow-${entry.name}`}
                                    height="130%"
                                >
                                    <feGaussianBlur
                                        in="SourceAlpha"
                                        stdDeviation="3"
                                    />
                                    <feOffset
                                        dx="0"
                                        dy="2"
                                        result="offsetblur"
                                    />
                                    <feComponentTransfer>
                                        <feFuncA type="linear" slope="0.3" />
                                    </feComponentTransfer>
                                    <feMerge>
                                        <feMergeNode />
                                        <feMergeNode in="SourceGraphic" />
                                    </feMerge>
                                </filter>
                            ))}
                        </defs>
                        <Pie
                            data={data}
                            cx="50%"
                            cy="45%"
                            labelLine={false}
                            label={renderCustomLabel}
                            outerRadius={100}
                            innerRadius={50}
                            fill="#8884d8"
                            dataKey="value"
                            paddingAngle={2}
                            animationBegin={0}
                            animationDuration={1000}
                            animationEasing="ease-out"
                        >
                            {data.map((entry, index) => (
                                <Cell
                                    key={`cell-${index}`}
                                    fill={entry.color}
                                    stroke="rgba(0,0,0,0.2)"
                                    strokeWidth={2}
                                />
                            ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                        <Legend content={renderLegend} />
                    </PieChart>
                </ResponsiveContainer>
            </CardContent>
        </Card>
    );
}
