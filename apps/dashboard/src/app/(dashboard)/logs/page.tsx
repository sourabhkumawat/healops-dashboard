'use client';

import { useEffect, useState, useRef, useMemo, Fragment } from 'react';
import { getLogs, getServices, LogEntry, LogFilters } from '@/actions/logs';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow
} from '@/components/ui/table';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    Search,
    Zap,
    Save,
    Bell,
    Filter,
    ChevronRight,
    ChevronDown,
    ZoomIn,
    ZoomOut,
    Plus,
    Settings,
    RefreshCw,
    X,
    Share2,
    Search as SearchIcon,
    Grid
} from 'lucide-react';
import { getWebSocketUrl } from '@/lib/config';
import { cn } from '@/lib/utils';

const LOG_LEVELS = [
    { value: 'error', label: 'Error', color: 'text-red-500' },
    { value: 'warn', label: 'Warn', color: 'text-orange-500' },
    { value: 'ok', label: 'Ok', color: 'text-green-500' },
    { value: 'info', label: 'Info', color: 'text-blue-500' },
    { value: 'debug', label: 'Debug', color: 'text-purple-500' }
];

export default function LogsPage() {
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [filteredLogs, setFilteredLogs] = useState<LogEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [liveTail, setLiveTail] = useState(false);
    const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
    const [isClosing, setIsClosing] = useState(false);
    const [detailTab, setDetailTab] = useState<'parsed' | 'original' | 'context'>('parsed');
    const [propertyView, setPropertyView] = useState<'flat' | 'nested'>('flat');
    const [propertySearch, setPropertySearch] = useState('');
    const [contextTimeRange, setContextTimeRange] = useState('30s');
    const [groupSimilar, setGroupSimilar] = useState(false);
    
    // Filters
    const [searchQuery, setSearchQuery] = useState('');
    const [eventType, setEventType] = useState<'logs' | 'spans'>('logs');
    const [selectedLevel, setSelectedLevel] = useState<string | null>(null);
    const [selectedService, setSelectedService] = useState<string | null>(null);
    const [serviceSearch, setServiceSearch] = useState('');
    const [environmentSearch, setEnvironmentSearch] = useState('');
    
    // Services list
    const [services, setServices] = useState<string[]>([]);
    const [showMoreServices, setShowMoreServices] = useState(false);
    
    const wsRef = useRef<WebSocket | null>(null);
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const fetchingLogsRef = useRef(false);
    const fetchingServicesRef = useRef(false);

    // Fetch logs
    const fetchLogs = async () => {
        if (fetchingLogsRef.current) return; // Prevent duplicate calls
        fetchingLogsRef.current = true;
        setLoading(true);
        try {
            const filters: LogFilters = {
                limit: 200,
                level: selectedLevel || undefined,
                service: selectedService || undefined,
                search: searchQuery || undefined
            };
            const data = await getLogs(filters);
            setLogs(data);
            if (!liveTail) {
                // Update filtered logs when not in live tail mode
                applyFilters(data);
            }
        } catch (error) {
            console.error('Failed to fetch logs:', error);
        } finally {
            setLoading(false);
            fetchingLogsRef.current = false;
        }
    };

    // Fetch services
    const fetchServices = async () => {
        if (fetchingServicesRef.current) return; // Prevent duplicate calls
        fetchingServicesRef.current = true;
        try {
            const data = await getServices();
            setServices(data);
        } catch (error) {
            console.error('Failed to fetch services:', error);
        } finally {
            fetchingServicesRef.current = false;
        }
    };

    useEffect(() => {
        fetchLogs();
        fetchServices();
    }, [selectedLevel, selectedService]);

    // WebSocket for live tail
    useEffect(() => {
        if (!liveTail) {
            if (wsRef.current) {
                wsRef.current.close();
                wsRef.current = null;
            }
            return;
        }

        const ws = new WebSocket(getWebSocketUrl());
        wsRef.current = ws;

        ws.onopen = () => {
            console.log('Connected to Live Tail WebSocket');
        };

        ws.onmessage = (event) => {
            try {
                const logData = JSON.parse(event.data);
                if (!logData.id) {
                    logData.id = Date.now() + Math.random();
                }
                if (!logData.timestamp) {
                    logData.timestamp = new Date().toISOString();
                }
                
                setLogs((prev) => {
                    const newLogs = [logData, ...prev];
                    if (newLogs.length > 1000) {
                        return newLogs.slice(0, 1000);
                    }
                    return newLogs;
                });
            } catch (e) {
                console.error('Failed to parse log message', e);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        ws.onclose = () => {
            console.log('Disconnected from Live Tail WebSocket');
        };

        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [liveTail]);

    // Apply filters to logs
    const applyFilters = (logsToFilter: LogEntry[]) => {
        let filtered = [...logsToFilter];

        // Level filter
        if (selectedLevel) {
            filtered = filtered.filter((log) => {
                const level = (log.level || log.severity || '').toLowerCase();
                return level === selectedLevel.toLowerCase();
            });
        }

        // Service filter
        if (selectedService) {
            filtered = filtered.filter((log) =>
                log.service_name?.toLowerCase() === selectedService.toLowerCase()
            );
        }

        // Search filter
        if (searchQuery) {
            const query = searchQuery.toLowerCase();
            filtered = filtered.filter(
                (log) =>
                    log.message?.toLowerCase().includes(query) ||
                    log.service_name?.toLowerCase().includes(query)
            );
        }

        // Group similar events
        if (groupSimilar) {
            const grouped = new Map<string, LogEntry[]>();
            filtered.forEach((log) => {
                const key = log.message || 'empty';
                if (!grouped.has(key)) {
                    grouped.set(key, []);
                }
                grouped.get(key)!.push(log);
            });
            // Flatten grouped logs (show first occurrence with count)
            filtered = Array.from(grouped.values()).map((group) => ({
                ...group[0],
                _count: group.length,
                _grouped: true
            })) as any;
        }

        setFilteredLogs(filtered);
    };

    // Update filtered logs when filters change
    useEffect(() => {
        applyFilters(logs);
    }, [logs, selectedLevel, selectedService, searchQuery, groupSimilar]);

    // Chart data
    const chartData = useMemo(() => {
        if (filteredLogs.length === 0) {
            return [];
        }

        // Get time range from filtered logs
        const timestamps = filteredLogs
            .map((log) => (log.timestamp ? new Date(log.timestamp).getTime() : null))
            .filter((ts): ts is number => ts !== null);

        if (timestamps.length === 0) {
            return [];
        }

        const minTime = Math.min(...timestamps);
        const maxTime = Math.max(...timestamps);
        const timeRange = maxTime - minTime;
        
        // Use 1-minute buckets, but adjust if the time range is very large
        const bucketSizeMs = Math.max(60000, Math.floor(timeRange / 15)); // At least 1 min, max 15 buckets
        
        // Create buckets
        const buckets: { [key: number]: number } = {};
        const bucketCount = Math.min(16, Math.ceil(timeRange / bucketSizeMs) + 1);
        
        for (let i = 0; i < bucketCount; i++) {
            const bucketStart = minTime + i * bucketSizeMs;
            buckets[bucketStart] = 0;
        }

        // Distribute logs into buckets
        filteredLogs.forEach((log) => {
            if (log.timestamp) {
                const logTime = new Date(log.timestamp).getTime();
                const bucketStart = minTime + Math.floor((logTime - minTime) / bucketSizeMs) * bucketSizeMs;
                if (buckets[bucketStart] !== undefined) {
                    buckets[bucketStart]++;
                } else {
                    // If log is slightly outside range, add to closest bucket
                    const closestBucket = Object.keys(buckets)
                        .map(Number)
                        .reduce((prev, curr) =>
                            Math.abs(curr - logTime) < Math.abs(prev - logTime) ? curr : prev
                        );
                    buckets[closestBucket]++;
                }
            }
        });

        return Object.entries(buckets)
            .map(([time, count]) => {
                const date = new Date(Number(time));
                return {
                    time: date,
                    timeLabel: date.toLocaleString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        hour: 'numeric',
                        minute: '2-digit',
                        hour12: true
                    }),
                    timeShort: date.toLocaleString('en-US', {
                        hour: 'numeric',
                        minute: '2-digit',
                        hour12: true
                    }),
                    count
                };
            })
            .sort((a, b) => a.time.getTime() - b.time.getTime());
    }, [filteredLogs]);

    const maxCount = Math.max(...chartData.map((d) => d.count), 0);
    // Round up to nearest 6 for cleaner Y-axis (matching reference: 0, 6, 12, 18, 24)
    // But ensure at least 6 for visibility
    const yAxisMax = maxCount > 0 ? Math.max(6, Math.ceil(maxCount / 6) * 6) : 6;
    const yAxisTicks = [0, yAxisMax / 4, yAxisMax / 2, (yAxisMax * 3) / 4, yAxisMax].map(
        (val) => Math.round(val)
    );

    const selectLog = (log: LogEntry) => {
        setIsClosing(false);
        setSelectedLog(log);
        setDetailTab('parsed');
    };

    const closeDetailPanel = () => {
        setIsClosing(true);
        // Wait for exit animation to complete before actually closing
        setTimeout(() => {
            setSelectedLog(null);
            setIsClosing(false);
        }, 300); // Match animation duration
    };

    // Keyboard navigation
    useEffect(() => {
        if (!selectedLog) return;

        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                closeDetailPanel();
            } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                const currentIndex = filteredLogs.findIndex((log) => log.id === selectedLog.id);
                if (currentIndex === -1) return;

                const nextIndex = e.key === 'ArrowRight' 
                    ? currentIndex + 1 
                    : currentIndex - 1;

                if (nextIndex >= 0 && nextIndex < filteredLogs.length) {
                    setSelectedLog(filteredLogs[nextIndex]);
                }
            } else if (e.key === 'k' || e.key === 'j') {
                const currentIndex = filteredLogs.findIndex((log) => log.id === selectedLog.id);
                if (currentIndex === -1) return;

                const nextIndex = e.key === 'j' 
                    ? currentIndex + 1 
                    : currentIndex - 1;

                if (nextIndex >= 0 && nextIndex < filteredLogs.length) {
                    setSelectedLog(filteredLogs[nextIndex]);
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [selectedLog, filteredLogs]);

    const getLevelColor = (level: string | undefined) => {
        const lvl = (level || '').toLowerCase();
        if (lvl.includes('error')) return 'text-red-500';
        if (lvl.includes('warn')) return 'text-orange-500';
        if (lvl.includes('ok')) return 'text-green-500';
        if (lvl.includes('info')) return 'text-blue-500';
        if (lvl.includes('debug')) return 'text-purple-500';
        return 'text-zinc-400';
    };

    const formatTimestamp = (timestamp: string) => {
        return new Date(timestamp).toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
            second: '2-digit',
            hour12: true
        });
    };

    const formatRelativeTime = (timestamp: string) => {
        const now = new Date();
        const logTime = new Date(timestamp);
        const diffMs = now.getTime() - logTime.getTime();
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        return `${diffDays}d ago`;
    };

    const getSurroundingLogs = (log: LogEntry, timeRange: string): LogEntry[] => {
        if (!log.timestamp) return [];
        
        const logTime = new Date(log.timestamp).getTime();
        let actualRangeMs = 30000; // Default 30s
        
        if (timeRange.endsWith('ms')) {
            actualRangeMs = parseInt(timeRange) || 100;
        } else if (timeRange.endsWith('s')) {
            actualRangeMs = parseInt(timeRange) * 1000;
        } else if (timeRange.endsWith('m')) {
            actualRangeMs = parseInt(timeRange) * 60 * 1000;
        } else if (timeRange.endsWith('h')) {
            actualRangeMs = parseInt(timeRange) * 60 * 60 * 1000;
        }

        return filteredLogs.filter((l) => {
            if (!l.timestamp) return false;
            const lTime = new Date(l.timestamp).getTime();
            return Math.abs(lTime - logTime) <= actualRangeMs;
        }).sort((a, b) => {
            const aTime = a.timestamp ? new Date(a.timestamp).getTime() : 0;
            const bTime = b.timestamp ? new Date(b.timestamp).getTime() : 0;
            return aTime - bTime;
        });
    };

    const getParsedProperties = (log: LogEntry) => {
        const props: Record<string, any> = {
            level: log.level || log.severity || 'info',
            message: log.message || '',
            'service.name': log.service_name || '',
        };

        if (log.metadata && typeof log.metadata === 'object') {
            Object.assign(props, log.metadata);
        }

        if (log.source) {
            props.source = log.source;
        }

        return props;
    };

    const getEventTags = (log: LogEntry): string[] => {
        const tags: string[] = [];
        if (log.service_name) {
            tags.push(`service: ${log.service_name}`);
        }
        if (log.metadata && typeof log.metadata === 'object') {
            Object.entries(log.metadata).forEach(([key, value]) => {
                if (typeof value === 'string' && key.includes('.')) {
                    tags.push(`${key}: ${value}`);
                }
            });
        }
        return tags;
    };

    const filteredServices = services.filter((s) =>
        s.toLowerCase().includes(serviceSearch.toLowerCase())
    );
    const displayedServices = showMoreServices
        ? filteredServices
        : filteredServices.slice(0, 10);

    return (
        <div className="flex-1 flex flex-col h-[calc(100vh-8rem)] -m-8 bg-zinc-950 text-zinc-100">
            {/* Top Header */}
            <div className="border-b border-zinc-800 px-4 py-3">
                <div className="flex items-center justify-between gap-4">
                    <div className="flex-1 relative">
                        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-zinc-500" />
                        <Input
                            placeholder="Search your events for anything..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="pl-10 bg-zinc-900 border-zinc-800 text-zinc-100 placeholder:text-zinc-500"
                        />
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            onClick={() => {
                                setLiveTail(!liveTail);
                                if (!liveTail) {
                                    fetchLogs();
                                }
                            }}
                            className={cn(
                                'bg-green-600 hover:bg-green-700',
                                liveTail && 'ring-2 ring-green-400'
                            )}
                        >
                            <Zap className="h-4 w-4 mr-2" />
                            Live Tail
                        </Button>
                        <Button variant="outline" className="border-zinc-700">
                            <Save className="h-4 w-4 mr-2" />
                            Save
                        </Button>
                        <Button variant="outline" className="border-zinc-700">
                            <Bell className="h-4 w-4 mr-2" />
                            Alert
                        </Button>
                    </div>
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden">
                {/* Left Sidebar - Filters */}
                <div className="w-72 border-r border-zinc-800 bg-zinc-900 overflow-y-auto">
                    <div className="p-3 space-y-4">
                        <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
                            Filters
                        </h3>

                        {/* Event Type Filter */}
                        <div className="space-y-2">
                            <div className="flex items-center gap-2 mb-2">
                                <Filter className="h-4 w-4 text-zinc-500" />
                                <Input
                                    placeholder="Q Event Type"
                                    className="bg-zinc-800 border-zinc-700 text-zinc-100 h-8 text-xs"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="flex items-center gap-2 cursor-pointer hover:text-zinc-100">
                                    <input
                                        type="radio"
                                        name="eventType"
                                        checked={eventType === 'logs'}
                                        onChange={() => setEventType('logs')}
                                        className="w-4 h-4 text-blue-500 bg-zinc-800 border-zinc-700 focus:ring-blue-500 focus:ring-2"
                                    />
                                    <span className="text-sm text-zinc-300">Logs {'{}'}</span>
                                </label>
                                <label className="flex items-center gap-2 cursor-pointer hover:text-zinc-100">
                                    <input
                                        type="radio"
                                        name="eventType"
                                        checked={eventType === 'spans'}
                                        onChange={() => setEventType('spans')}
                                        className="w-4 h-4 text-blue-500 bg-zinc-800 border-zinc-700 focus:ring-blue-500 focus:ring-2"
                                    />
                                    <span className="text-sm text-zinc-300">Spans =</span>
                                </label>
                            </div>
                        </div>

                        {/* Level Filter */}
                        <div className="space-y-2">
                            <div className="flex items-center gap-2 mb-2">
                                <Filter className="h-4 w-4 text-zinc-500" />
                                <Input
                                    placeholder="Q Level"
                                    className="bg-zinc-800 border-zinc-700 text-zinc-100 h-8 text-xs"
                                />
                            </div>
                            <div className="space-y-2">
                                {LOG_LEVELS.map((level) => (
                                    <label
                                        key={level.value}
                                        className="flex items-center gap-2 cursor-pointer hover:opacity-80"
                                    >
                                        <input
                                            type="radio"
                                            name="level"
                                            checked={selectedLevel === level.value}
                                            onChange={() =>
                                                setSelectedLevel(
                                                    selectedLevel === level.value
                                                        ? null
                                                        : level.value
                                                )
                                            }
                                            className="w-4 h-4 text-blue-500 bg-zinc-800 border-zinc-700 focus:ring-blue-500 focus:ring-2"
                                        />
                                        <span className={cn('text-sm', level.color)}>
                                            {level.label}
                                        </span>
                                    </label>
                                ))}
                            </div>
                        </div>

                        {/* Service Filter */}
                        <div className="space-y-2">
                            <div className="flex items-center gap-2 mb-2">
                                <Filter className="h-4 w-4 text-zinc-500" />
                                <Input
                                    placeholder="Q Service"
                                    value={serviceSearch}
                                    onChange={(e) => setServiceSearch(e.target.value)}
                                    className="bg-zinc-800 border-zinc-700 text-zinc-100 h-8 text-xs"
                                />
                            </div>
                            <ScrollArea className="h-48">
                                <div className="space-y-2">
                                    {displayedServices.length > 0 ? (
                                        displayedServices.map((service) => (
                                            <label
                                                key={service}
                                                className="flex items-center gap-2 cursor-pointer hover:text-zinc-100 py-1"
                                            >
                                                <input
                                                    type="checkbox"
                                                    checked={selectedService === service}
                                                    onChange={() =>
                                                        setSelectedService(
                                                            selectedService === service
                                                                ? null
                                                                : service
                                                        )
                                                    }
                                                    className="w-4 h-4 text-blue-500 bg-zinc-800 border-zinc-700 rounded focus:ring-blue-500 focus:ring-2"
                                                />
                                                <span className="text-sm text-zinc-300 truncate flex-1">
                                                    {service}
                                                </span>
                                            </label>
                                        ))
                                    ) : (
                                        <p className="text-xs text-zinc-500">
                                            No services found
                                        </p>
                                    )}
                                </div>
                            </ScrollArea>
                            {filteredServices.length > 10 && (
                                <button
                                    onClick={() => setShowMoreServices(!showMoreServices)}
                                    className="text-xs text-blue-500 hover:text-blue-400"
                                >
                                    {showMoreServices ? 'Show less' : 'Show more'}
                                </button>
                            )}
                        </div>

                        {/* Environment Filter */}
                        <div className="space-y-2">
                            <div className="flex items-center gap-2 mb-2">
                                <Filter className="h-4 w-4 text-zinc-500" />
                                <Input
                                    placeholder="Q Environment"
                                    value={environmentSearch}
                                    onChange={(e) => setEnvironmentSearch(e.target.value)}
                                    className="bg-zinc-800 border-zinc-700 text-zinc-100 h-8 text-xs"
                                />
                            </div>
                            <p className="text-xs text-zinc-500">No options found</p>
                        </div>
                    </div>
                </div>

                {/* Main Content */}
                <div className="flex-1 flex flex-col overflow-hidden min-w-0">
                    {/* Results Count */}
                    <div className="px-4 py-2 border-b border-zinc-800">
                        <p className="text-xs text-zinc-400">
                            {filteredLogs.length} Result{filteredLogs.length !== 1 ? 's' : ''}
                        </p>
                    </div>

                    {/* Time Series Chart */}
                    <div className="px-4 py-2 border-b border-zinc-800 bg-zinc-900">
                        <div className="flex items-center justify-between mb-1.5">
                            <h4 className="text-xs font-medium text-zinc-400">
                                Log Volume Over Time
                            </h4>
                            <div className="flex items-center gap-1">
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-zinc-500 hover:text-zinc-300 h-6 px-2 text-[10px]"
                                >
                                    <ZoomOut className="h-3 w-3" />
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-zinc-500 hover:text-zinc-300 h-6 px-2 text-[10px]"
                                >
                                    <ZoomIn className="h-3 w-3" />
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-zinc-500 hover:text-zinc-300 h-6 px-2 text-[10px]"
                                >
                                    <Plus className="h-3 w-3" />
                                </Button>
                            </div>
                        </div>
                        <div
                            ref={chartContainerRef}
                            className="h-24 bg-zinc-950 rounded border border-zinc-800/50 p-1.5 relative"
                        >
                            {chartData.length > 0 ? (
                                <>
                                    {/* Y-axis labels - minimal, only show 2-3 key values */}
                                    <div className="absolute left-0 top-2 bottom-2 flex flex-col justify-between text-[10px] text-zinc-600 pr-1.5">
                                        {[yAxisTicks[yAxisTicks.length - 1], yAxisTicks[Math.floor(yAxisTicks.length / 2)], yAxisTicks[0]]
                                            .filter((val, idx, arr) => arr.indexOf(val) === idx)
                                            .reverse()
                                            .map((tick, idx) => (
                                                <span key={idx} className="tabular-nums">
                                                    {tick}
                                                </span>
                                            ))}
                                    </div>

                                    {/* Chart area */}
                                    <div className="ml-6 h-full relative">
                                        {/* Bars */}
                                        <div className="relative h-full flex items-end justify-between gap-px">
                                            {chartData.map((data, index) => {
                                                const barHeight =
                                                    yAxisMax > 0
                                                        ? `${(data.count / yAxisMax) * 100}%`
                                                        : '0%';
                                                return (
                                                    <div
                                                        key={index}
                                                        className="flex-1 flex flex-col items-center justify-end h-full relative group"
                                                    >
                                                        <div
                                                            className="w-full bg-orange-500/90 hover:bg-orange-400 transition-colors duration-150 cursor-pointer min-h-[1px]"
                                                            style={{
                                                                height: barHeight
                                                            }}
                                                        >
                                                            {/* Tooltip on hover */}
                                                            <div className="absolute bottom-full mb-1.5 left-1/2 transform -translate-x-1/2 bg-zinc-800 text-zinc-100 text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap z-10 border border-zinc-700 shadow-lg">
                                                                <div className="text-green-400">{data.timeLabel}</div>
                                                                <div className="text-zinc-300">Other: {data.count} lines</div>
                                                            </div>
                                                        </div>
                                                        {/* X-axis labels - show every 4th label */}
                                                        {index % 4 === 0 && (
                                                            <span className="text-[9px] text-zinc-600 mt-0.5 whitespace-nowrap">
                                                                {data.timeShort}
                                                            </span>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>
                                </>
                            ) : (
                                <div className="flex items-center justify-center h-full text-zinc-600 text-xs">
                                    No data to display
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Logs Table */}
                    <div className="flex-1 overflow-hidden flex flex-col">
                        <div className="flex-1 overflow-auto">
                            <Table className="text-sm">
                                <TableHeader className="sticky top-0 bg-zinc-900 z-10">
                                    <TableRow className="border-zinc-800/50 hover:bg-transparent">
                                        <TableHead className="w-6 px-2 py-1"></TableHead>
                                        <TableHead className="text-zinc-500 text-[10px] font-medium py-1 px-2">Timestamp (Local)</TableHead>
                                        <TableHead className="text-zinc-500 text-[10px] font-medium py-1 px-2">Level</TableHead>
                                        <TableHead className="text-zinc-500 text-[10px] font-medium py-1 px-2">Service</TableHead>
                                        <TableHead className="text-zinc-500 text-[10px] font-medium py-1 px-2">
                                            <div className="flex items-center gap-1.5">
                                                Message
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    onClick={() => setGroupSimilar(!groupSimilar)}
                                                    className={cn(
                                                        'h-5 text-[10px] px-1.5',
                                                        groupSimilar && 'bg-zinc-800'
                                                    )}
                                                >
                                                    Group Similar Events
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    onClick={fetchLogs}
                                                    className="h-5 w-5 p-0"
                                                >
                                                    <Settings className="h-3 w-3" />
                                                </Button>
                                            </div>
                                        </TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {loading && filteredLogs.length === 0 ? (
                                        <TableRow>
                                            <TableCell
                                                colSpan={5}
                                                className="text-center py-6 text-zinc-500 text-xs"
                                            >
                                                Loading logs...
                                            </TableCell>
                                        </TableRow>
                                    ) : filteredLogs.length === 0 ? (
                                        <TableRow>
                                            <TableCell
                                                colSpan={5}
                                                className="text-center py-6 text-zinc-500 text-xs"
                                            >
                                                No logs found
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        filteredLogs.map((log) => {
                                            const isSelected = selectedLog?.id === log.id;
                                            const logLevel = log.level || log.severity || 'info';
                                            const groupedCount = (log as any)._count;

                                            return (
                                                <TableRow
                                                    key={log.id}
                                                    className={cn(
                                                        "border-zinc-800/30 hover:bg-zinc-900/30 cursor-pointer transition-colors",
                                                        isSelected && "bg-zinc-800/40"
                                                    )}
                                                    onClick={() => selectLog(log)}
                                                >
                                                    <TableCell className="px-2 py-1">
                                                        <ChevronRight className="h-3 w-3 text-zinc-600" />
                                                    </TableCell>
                                                    <TableCell className="text-zinc-400 text-[11px] font-mono px-2 py-1">
                                                        {log.timestamp
                                                            ? formatTimestamp(log.timestamp)
                                                            : '--'}
                                                    </TableCell>
                                                    <TableCell className="px-2 py-1">
                                                        <Badge
                                                            className={cn(
                                                                'text-[10px] px-1.5 py-0.5 font-medium',
                                                                getLevelColor(logLevel)
                                                            )}
                                                            variant="outline"
                                                        >
                                                            {logLevel.toLowerCase()}
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell className="text-zinc-400 text-[11px] px-2 py-1">
                                                        {log.service_name || '--'}
                                                    </TableCell>
                                                    <TableCell className="text-zinc-300 text-[11px] font-mono px-2 py-1">
                                                        <div className="flex items-center gap-2">
                                                            <span className="flex-1 truncate">
                                                                {log.message || '(empty)'}
                                                            </span>
                                                            {groupedCount && (
                                                                <Badge
                                                                    variant="outline"
                                                                    className="text-[10px] px-1.5 py-0.5 ml-1 flex-shrink-0"
                                                                >
                                                                    {groupedCount}x
                                                                </Badge>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </div>
                </div>

                {/* Log Detail Panel */}
                {selectedLog && (
                    <div className={cn(
                        "w-[480px] border-l border-zinc-800 bg-zinc-950 flex flex-col h-full flex-shrink-0",
                        isClosing ? "animate-slide-out-right" : "animate-slide-in-right"
                    )}>
                    {/* Header */}
                    <div className="px-4 py-3 border-b border-zinc-800">
                        <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                                <Badge
                                    className={cn(
                                        'text-[10px] px-1.5 py-0.5',
                                        getLevelColor(selectedLog.level || selectedLog.severity || 'info')
                                    )}
                                    variant="outline"
                                >
                                    {(selectedLog.level || selectedLog.severity || 'info').toLowerCase()}
                                </Badge>
                                <span className="text-xs text-zinc-500">
                                    {formatTimestamp(selectedLog.timestamp)} · {formatRelativeTime(selectedLog.timestamp)}
                                </span>
                            </div>
                            <div className="flex items-center gap-1">
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    className="text-zinc-500 hover:text-zinc-300 h-7 px-2 text-xs"
                                >
                                    <Share2 className="h-3 w-3" />
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={closeDetailPanel}
                                    className="text-zinc-500 hover:text-zinc-300 h-7 w-7 p-0"
                                >
                                    <X className="h-3 w-3" />
                                </Button>
                            </div>
                        </div>
                        <div className="bg-zinc-900 p-2 rounded border border-zinc-800 mb-2">
                            <p className="text-zinc-100 text-xs font-mono">
                                {selectedLog.message || '(empty message)'}
                            </p>
                        </div>
                        {selectedLog.service_name && (
                            <div className="bg-zinc-900 px-2 py-1 rounded border border-zinc-800 inline-block">
                                <span className="text-[10px] text-zinc-500">
                                    service: {selectedLog.service_name}
                                </span>
                            </div>
                        )}
                    </div>

                    {/* Tabs */}
                    <Tabs value={detailTab} onValueChange={(v) => setDetailTab(v as any)} className="flex-1 flex flex-col overflow-hidden">
                        <div className="border-b border-zinc-800 px-4 py-1">
                            <TabsList className="bg-transparent">
                                <TabsTrigger 
                                    value="parsed" 
                                    className="data-[state=active]:bg-transparent data-[state=active]:border-b-2 data-[state=active]:border-green-500 data-[state=active]:text-green-500"
                                >
                                    Parsed Properties
                                </TabsTrigger>
                                <TabsTrigger 
                                    value="original"
                                    className="data-[state=active]:bg-transparent data-[state=active]:border-b-2 data-[state=active]:border-green-500 data-[state=active]:text-green-500"
                                >
                                    Original Line
                                </TabsTrigger>
                                <TabsTrigger 
                                    value="context"
                                    className="data-[state=active]:bg-transparent data-[state=active]:border-b-2 data-[state=active]:border-green-500 data-[state=active]:text-green-500"
                                >
                                    Surrounding Context
                                </TabsTrigger>
                            </TabsList>
                        </div>

                        <ScrollArea className="flex-1">
                            <div className="p-3">
                                {/* Parsed Properties Tab */}
                                <TabsContent value="parsed" className="mt-0 space-y-3">
                                    <div>
                                        <div className="flex items-center justify-between mb-2">
                                            <h3 className="text-xs font-medium text-zinc-400">Properties</h3>
                                            <div className="flex items-center gap-2">
                                                <Button
                                                    size="sm"
                                                    variant={propertyView === 'flat' ? 'default' : 'ghost'}
                                                    onClick={() => setPropertyView('flat')}
                                                    className="h-7 text-xs"
                                                >
                                                    Flat view
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant={propertyView === 'nested' ? 'default' : 'ghost'}
                                                    onClick={() => setPropertyView('nested')}
                                                    className="h-7 text-xs"
                                                >
                                                    Nested view
                                                </Button>
                                            </div>
                                        </div>
                                        <div className="relative mb-2">
                                            <SearchIcon className="absolute left-2 top-1/2 transform -translate-y-1/2 h-3 w-3 text-zinc-500" />
                                            <Input
                                                placeholder="Search properties by key or value"
                                                value={propertySearch}
                                                onChange={(e) => setPropertySearch(e.target.value)}
                                                className="pl-8 bg-zinc-900 border-zinc-800 text-zinc-100 text-xs h-7"
                                            />
                                        </div>
                                        <div className="space-y-0.5 font-mono text-xs">
                                            {Object.entries(getParsedProperties(selectedLog))
                                                .filter(([key, value]) => {
                                                    if (!propertySearch) return true;
                                                    const search = propertySearch.toLowerCase();
                                                    return key.toLowerCase().includes(search) || 
                                                           String(value).toLowerCase().includes(search);
                                                })
                                                .map(([key, value]) => (
                                                    <div key={key} className="flex items-start gap-2 py-1 border-b border-zinc-800/50">
                                                        <span className="text-zinc-500 flex-shrink-0 w-32 text-[10px]">{key}:</span>
                                                        <span className="text-zinc-300 break-words text-[10px] whitespace-pre-wrap flex-1">
                                                            {typeof value === 'object' 
                                                                ? JSON.stringify(value, null, 2)
                                                                : String(value)
                                                            }
                                                        </span>
                                                    </div>
                                                ))}
                                        </div>
                                    </div>
                                    <div>
                                        <h3 className="text-xs font-medium text-zinc-400 mb-2">Event Tags</h3>
                                        <div className="flex flex-wrap gap-2">
                                            {getEventTags(selectedLog).map((tag, idx) => (
                                                <Badge 
                                                    key={idx}
                                                    variant="outline"
                                                    className="text-[10px] px-1.5 py-0.5 bg-zinc-900 border-zinc-700 text-zinc-400"
                                                >
                                                    {tag}
                                                </Badge>
                                            ))}
                                        </div>
                                    </div>
                                </TabsContent>

                                {/* Original Line Tab */}
                                <TabsContent value="original" className="mt-0">
                                    <pre className="text-[10px] font-mono text-zinc-300 bg-zinc-900 p-3 rounded border border-zinc-800 whitespace-pre-wrap break-words overflow-wrap-anywhere">
                                        {JSON.stringify({
                                            level: selectedLog.level || selectedLog.severity,
                                            message: selectedLog.message,
                                            'service.name': selectedLog.service_name,
                                            source: selectedLog.source,
                                            timestamp: selectedLog.timestamp,
                                            ...(selectedLog.metadata || {})
                                        }, null, 2)}
                                    </pre>
                                </TabsContent>

                                {/* Surrounding Context Tab */}
                                <TabsContent value="context" className="mt-0 space-y-3">
                                    <div>
                                        <div className="flex items-center gap-2 mb-2">
                                            <span className="text-xs text-zinc-500">Time range:</span>
                                            <div className="flex gap-1">
                                                {['100ms', '500ms', '1s', '5s', '30s', '1m', '5m', '15m'].map((range) => (
                                                    <Button
                                                        key={range}
                                                        size="sm"
                                                        variant={contextTimeRange === range ? 'default' : 'ghost'}
                                                        onClick={() => setContextTimeRange(range)}
                                                        className="h-7 text-xs"
                                                    >
                                                        {range}
                                                    </Button>
                                                ))}
                                            </div>
                                        </div>
                                        <div className="space-y-1">
                                            {getSurroundingLogs(selectedLog, contextTimeRange).map((log) => {
                                                const isCurrentLog = log.id === selectedLog.id;
                                                const logLevel = log.level || log.severity || 'info';
                                                return (
                                                    <div
                                                        key={log.id}
                                                        className={cn(
                                                            "p-2 rounded border cursor-pointer transition-colors",
                                                            isCurrentLog 
                                                                ? "bg-zinc-800 border-zinc-700" 
                                                                : "bg-zinc-900/50 border-zinc-800 hover:bg-zinc-900"
                                                        )}
                                                        onClick={() => setSelectedLog(log)}
                                                    >
                                                        <div className="flex items-center justify-between mb-1">
                                                            <div className="flex items-center gap-2">
                                                                <Badge
                                                                    className={cn('text-[10px] px-1.5 py-0.5', getLevelColor(logLevel))}
                                                                    variant="outline"
                                                                >
                                                                    {logLevel.toLowerCase()}
                                                                </Badge>
                                                                <span className="text-[10px] text-zinc-500">
                                                                    {log.timestamp ? formatTimestamp(log.timestamp) : '--'}
                                                                </span>
                                                            </div>
                                                            {log.service_name && (
                                                                <span className="text-[10px] text-zinc-500">
                                                                    {log.service_name}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <p className="text-xs text-zinc-300 font-mono">
                                                            {log.message || '(empty)'}
                                                        </p>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    </div>
                                </TabsContent>
                            </div>
                        </ScrollArea>
                    </Tabs>

                    {/* Footer */}
                    <div className="px-4 py-2 border-t border-zinc-800 text-[10px] text-zinc-600 text-center">
                        Use ← → arrow keys or k j to move through events · ESC to close
                    </div>
                </div>
                )}
            </div>
        </div>
    );
}


