'use server';

import { API_BASE } from '@/lib/config';
import { fetchWithAuth } from '@/lib/api-client';

export type LogEntry = {
    id: number;
    service_name: string;
    severity: string;
    level: string;
    message: string;
    source: string;
    timestamp: string;
    metadata: any;
    integration_id: number | null;
};

export interface LogFilters {
    limit?: number;
    level?: string;
    service?: string;
    search?: string;
    eventType?: 'logs' | 'spans';
}

export async function getLogs(filters: LogFilters = {}): Promise<LogEntry[]> {
    try {
        const { limit = 50, level, service, search } = filters;
        const params = new URLSearchParams();
        
        params.append('limit', limit.toString());
        if (level) params.append('level', level);
        if (service) params.append('service', service);
        if (search) params.append('search', search);

        const response = await fetchWithAuth(
            `${API_BASE}/logs?${params.toString()}`,
            {
                cache: 'no-store' // Ensure we always get fresh data
            }
        );

        if (!response.ok) {
            console.error(
                'Failed to fetch logs:',
                response.status,
                await response.text()
            );
            return [];
        }

        const data = await response.json();
        let logs = data.logs || [];

        // Client-side filtering if backend doesn't support it
        if (level && !params.has('level')) {
            logs = logs.filter((log: LogEntry) => 
                (log.level || log.severity || '').toLowerCase() === level.toLowerCase()
            );
        }
        if (service && !params.has('service')) {
            logs = logs.filter((log: LogEntry) => 
                log.service_name?.toLowerCase().includes(service.toLowerCase())
            );
        }
        if (search && !params.has('search')) {
            const searchLower = search.toLowerCase();
            logs = logs.filter((log: LogEntry) => 
                log.message?.toLowerCase().includes(searchLower) ||
                log.service_name?.toLowerCase().includes(searchLower)
            );
        }

        return logs;
    } catch (error) {
        console.error('Error fetching logs:', error);
        return [];
    }
}

// Re-export getServices from integrations.ts but adapt the return type
import { getServices as getServicesFromIntegrations } from './integrations';

export async function getServices(): Promise<string[]> {
    const data = await getServicesFromIntegrations();
    // Handle the return format from integrations.ts: { services: string[] }
    if (data && typeof data === 'object' && 'services' in data) {
        return Array.isArray(data.services) ? data.services : [];
    }
    return [];
}
