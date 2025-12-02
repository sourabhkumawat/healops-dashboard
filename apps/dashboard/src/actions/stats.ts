'use server';

import { API_BASE } from '@/lib/config';
import { fetchWithAuth } from '@/lib/api-client';

export interface SystemStats {
    system_status: string;
    system_status_color: string;
    total_incidents: number;
    open_incidents: number;
    investigating_incidents: number;
    healing_incidents: number;
    resolved_incidents: number;
    failed_incidents: number;
    critical_incidents: number;
    high_incidents: number;
    medium_incidents: number;
    low_incidents: number;
    active_incidents: number;
    total_services: number;
    unhealthy_services: number;
    error_logs_count: number;
}

export async function getSystemStats(): Promise<SystemStats | null> {
    try {
        const response = await fetchWithAuth(`${API_BASE}/stats`, {
            cache: 'no-store' // Ensure we always get fresh data
        });

        if (!response.ok) {
            console.error(
                'Failed to fetch system stats:',
                response.status,
                await response.text()
            );
            return null;
        }

        return await response.json();
    } catch (error) {
        console.error('Error fetching system stats:', error);
        return null;
    }
}
