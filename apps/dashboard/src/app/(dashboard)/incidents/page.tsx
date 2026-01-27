'use client';

import { useEffect, useState, useRef, useMemo } from 'react';
import { IncidentTable, Incident } from '@/components/incident-table';
import { Loader2 } from 'lucide-react';
import { fetchClient } from '@/lib/client-api';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue
} from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { X } from 'lucide-react';
import { Pagination } from '@/components/ui/pagination';

export default function IncidentsPage() {
    const [incidents, setIncidents] = useState<Incident[]>([]);
    const [loading, setLoading] = useState(true);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const fetchingRef = useRef(false);
    const hasLoadedRef = useRef(false);
    const [filters, setFilters] = useState<{
        status?: string;
        severity?: string;
        source?: string;
        service?: string;
    }>({});
    
    // Pagination state
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [pagination, setPagination] = useState<{
        total: number;
        totalPages: number;
    } | null>(null);

    // Fetch all incidents to get unique values for filters (only on initial load)
    const [allIncidents, setAllIncidents] = useState<Incident[]>([]);
    const filtersLoadedRef = useRef(false);

    // Fetch all incidents for filter options (only once on initial load)
    useEffect(() => {
        const fetchFilterOptions = async () => {
            if (filtersLoadedRef.current) return;
            try {
                const res = await fetchClient('/incidents');
                const data = res.ok ? await res.json() : null;
                const allData = Array.isArray(data) ? data : (data?.data ?? []);
                setAllIncidents(allData);
                filtersLoadedRef.current = true;
            } catch (error) {
                console.error('Failed to fetch incidents for filters:', error);
            }
        };
        fetchFilterOptions();
    }, []);

    // Fetch paginated incidents
    useEffect(() => {
        let intervalId: NodeJS.Timeout | null = null;

        const fetchIncidents = async () => {
            if (fetchingRef.current) return; // Prevent duplicate calls
            fetchingRef.current = true;

            // Show refreshing state if we've already loaded data at least once
            if (hasLoadedRef.current) {
                setIsRefreshing(true);
            }

            try {
                const params = new URLSearchParams();
                if (filters.status) params.append('status', filters.status);
                if (filters.severity) params.append('severity', filters.severity);
                if (filters.source) params.append('source', filters.source);
                if (filters.service) params.append('service', filters.service);
                params.append('page', currentPage.toString());
                params.append('page_size', pageSize.toString());
                const res = await fetchClient(`/incidents?${params.toString()}`);
                const response = res.ok ? await res.json() : null;

                if (response && typeof response === 'object' && 'data' in response) {
                    setIncidents(response.data);
                    setPagination({
                        total: response.pagination.total,
                        totalPages: response.pagination.total_pages
                    });
                } else if (Array.isArray(response)) {
                    setIncidents(response);
                    setPagination({
                        total: response.length,
                        totalPages: 1
                    });
                } else {
                    setIncidents([]);
                    setPagination({
                        total: 0,
                        totalPages: 0
                    });
                }

                // Mark that we've loaded data at least once
                hasLoadedRef.current = true;
            } catch (error) {
                console.error('Failed to fetch incidents:', error);
                // Set empty arrays on error to prevent stale data
                setIncidents([]);
                setPagination({
                    total: 0,
                    totalPages: 0
                });
            } finally {
                setLoading(false);
                setIsRefreshing(false);
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
    }, [filters, currentPage, pageSize]);

    // Reset to page 1 when filters change
    useEffect(() => {
        setCurrentPage(1);
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
        setCurrentPage(1);
    };

    const handlePageChange = (page: number) => {
        setCurrentPage(page);
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
                    <>
                        <IncidentTable
                            incidents={incidents}
                            fullHeight={true}
                            isLoading={isRefreshing}
                        />
                        {pagination && pagination.totalPages > 1 && (
                            <div className="flex items-center justify-between border-t pt-4 mt-4">
                                <div className="flex items-center gap-2">
                                    <span className="text-sm text-muted-foreground">
                                        Showing {(currentPage - 1) * pageSize + 1} to{' '}
                                        {Math.min(currentPage * pageSize, pagination.total)} of{' '}
                                        {pagination.total} incidents
                                    </span>
                                    <Select
                                        value={pageSize.toString()}
                                        onValueChange={(value) => {
                                            setPageSize(Number(value));
                                            setCurrentPage(1);
                                        }}
                                    >
                                        <SelectTrigger className="w-[100px]">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="10">10</SelectItem>
                                            <SelectItem value="25">25</SelectItem>
                                            <SelectItem value="50">50</SelectItem>
                                            <SelectItem value="100">100</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <span className="text-sm text-muted-foreground">per page</span>
                                </div>
                                <Pagination
                                    currentPage={currentPage}
                                    totalPages={pagination.totalPages}
                                    onPageChange={handlePageChange}
                                />
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
