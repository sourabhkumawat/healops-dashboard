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

export async function getLogs(limit: number = 50): Promise<LogEntry[]> {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/logs?limit=${limit}`,
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
        return data.logs || [];
    } catch (error) {
        console.error('Error fetching logs:', error);
        return [];
    }
}
