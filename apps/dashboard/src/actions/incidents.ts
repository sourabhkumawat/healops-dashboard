'use server';

import { API_BASE } from '@/lib/config';
import { Incident } from '@/components/incident-table';

export async function getIncidents(status?: string): Promise<Incident[]> {
    try {
        let url = `${API_BASE}/incidents`;
        const params = new URLSearchParams();

        if (status) {
            params.append('status', status);
        }

        if (params.toString()) {
            url += `?${params.toString()}`;
        }

        const response = await fetch(url, {
            cache: 'no-store' // Ensure we always get fresh data
        });

        if (!response.ok) {
            console.error(
                'Failed to fetch incidents:',
                response.status,
                await response.text()
            );
            return [];
        }

        const data = await response.json();
        return Array.isArray(data) ? data : [];
    } catch (error) {
        console.error('Error fetching incidents:', error);
        return [];
    }
}

export async function getRecentIncidents(
    limit: number = 5
): Promise<Incident[]> {
    const allIncidents = await getIncidents();
    // API already returns incidents ordered by last_seen_at desc, so just take first N
    return allIncidents.slice(0, limit);
}
