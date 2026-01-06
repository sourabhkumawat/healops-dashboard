'use client';

import { useEffect, useState, useRef, useMemo } from 'react';
import { IncidentTable, Incident } from '@/components/incident-table';
import { Loader2 } from 'lucide-react';
import { getIncidents } from '@/actions/incidents';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue
} from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { X } from 'lucide-react';

export default function IncidentsPage() {
    const [incidents, setIncidents] = useState<Incident[]>([]);
    const [loading, setLoading] = useState(true);
    const fetchingRef = useRef(false);
    const [filters, setFilters] = useState<{
        status?: string;
        severity?: string;
        source?: string;
        service?: string;
    }>({});

    // Fetch all incidents to get unique values for filters
    const [allIncidents, setAllIncidents] = useState<Incident[]>([]);

    useEffect(() => {
        let intervalId: NodeJS.Timeout | null = null;

        const fetchIncidents = async () => {
            if (fetchingRef.current) return; // Prevent duplicate calls
            fetchingRef.current = true;
            try {
                // Fetch all incidents for filter options (only on initial load or when filters change)
                const allData = await getIncidents();
                setAllIncidents(allData);

                // Fetch filtered incidents
                const data = await getIncidents(filters);
                setIncidents(data);
            } catch (error) {
                console.error('Failed to fetch incidents:', error);
                // Set empty arrays on error to prevent stale data
                setIncidents([]);
            } finally {
                setLoading(false);
                fetchingRef.current = false;
            }
        };

        // Initial fetch
        fetchIncidents();

        // Poll every 10 seconds
        intervalId = setInterval(fetchIncidents, 10000);

        return () => {
            if (intervalId) {
                clearInterval(intervalId);
            }
        };
    }, [filters]);

    // Extract unique values for filter options
    const filterOptions = useMemo(() => {
        const services = Array.from(
            new Set(
                allIncidents
                    .map((i) => i.service_name)
                    .filter((s): s is string => Boolean(s))
            )
        ).sort();

        const sources = Array.from(
            new Set(
                allIncidents
                    .map((i) => i.source)
                    .filter((s): s is string => Boolean(s))
            )
        ).sort();

        const severities = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
        const statuses = [
            'OPEN',
            'INVESTIGATING',
            'HEALING',
            'RESOLVED',
            'FAILED'
        ];

        return { services, sources, severities, statuses };
    }, [allIncidents]);

    const hasActiveFilters = Object.values(filters).some((v) => v);

    const clearFilters = () => {
        setFilters({});
    };

    return (
        <div className="flex-1 flex flex-col space-y-4 h-full">
            <div className="flex items-center justify-between space-y-2">
                <h2 className="text-3xl font-bold tracking-tight">Incidents</h2>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap items-center gap-4 pb-2">
                <Select
                    value={filters.service || 'all'}
                    onValueChange={(value) =>
                        setFilters((prev) => ({
                            ...prev,
                            service: value === 'all' ? undefined : value
                        }))
                    }
                >
                    <SelectTrigger className="w-[180px]">
                        <SelectValue placeholder="Service" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Services</SelectItem>
                        {filterOptions.services.map((service) => (
                            <SelectItem key={service} value={service}>
                                {service}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select
                    value={filters.source || 'all'}
                    onValueChange={(value) =>
                        setFilters((prev) => ({
                            ...prev,
                            source: value === 'all' ? undefined : value
                        }))
                    }
                >
                    <SelectTrigger className="w-[180px]">
                        <SelectValue placeholder="Source" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Sources</SelectItem>
                        {filterOptions.sources.map((source) => (
                            <SelectItem key={source} value={source}>
                                {source?.toUpperCase() || source}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select
                    value={filters.severity || 'all'}
                    onValueChange={(value) =>
                        setFilters((prev) => ({
                            ...prev,
                            severity: value === 'all' ? undefined : value
                        }))
                    }
                >
                    <SelectTrigger className="w-[180px]">
                        <SelectValue placeholder="Severity" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Severities</SelectItem>
                        {filterOptions.severities.map((severity) => (
                            <SelectItem key={severity} value={severity}>
                                {severity}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select
                    value={filters.status || 'all'}
                    onValueChange={(value) =>
                        setFilters((prev) => ({
                            ...prev,
                            status: value === 'all' ? undefined : value
                        }))
                    }
                >
                    <SelectTrigger className="w-[180px]">
                        <SelectValue placeholder="Status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All Statuses</SelectItem>
                        {filterOptions.statuses.map((status) => (
                            <SelectItem key={status} value={status}>
                                {status}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                {hasActiveFilters && (
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={clearFilters}
                        className="gap-2"
                    >
                        <X className="h-4 w-4" />
                        Clear Filters
                    </Button>
                )}
            </div>

            <div className="hidden flex-1 flex-col md:flex min-h-0">
                {loading &&
                incidents.length === 0 &&
                allIncidents.length === 0 ? (
                    <div className="flex h-full items-center justify-center">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                ) : incidents.length === 0 && !loading ? (
                    <div className="flex h-full items-center justify-center">
                        <div className="text-center">
                            <p className="text-muted-foreground">
                                {hasActiveFilters
                                    ? 'No incidents match the selected filters.'
                                    : 'No incidents found.'}
                            </p>
                        </div>
                    </div>
                ) : (
                    <IncidentTable incidents={incidents} fullHeight={true} />
                )}
            </div>
        </div>
    );
}
