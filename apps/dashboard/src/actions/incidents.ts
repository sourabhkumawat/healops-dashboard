'use server';

import { API_BASE } from '@/lib/config';
import { Incident } from '@/components/incident-table';
import { fetchWithAuth } from '@/lib/api-client';

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

        const response = await fetchWithAuth(url, {
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

export async function getIncident(incidentId: number): Promise<{
    incident: Incident;
    logs: any[];
} | null> {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/incidents/${incidentId}`,
            {
                cache: 'no-store'
            }
        );

        if (!response.ok) {
            console.error(
                'Failed to fetch incident:',
                response.status,
                await response.text()
            );
            return null;
        }

        return await response.json();
    } catch (error) {
        console.error('Error fetching incident:', error);
        return null;
    }
}

export async function triggerIncidentAnalysis(incidentId: number): Promise<{
    status: string;
    message: string;
}> {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/incidents/${incidentId}/analyze`,
            {
                method: 'POST',
                cache: 'no-store'
            }
        );

        if (!response.ok) {
            const errorText = await response.text();
            return {
                status: 'error',
                message: errorText
            };
        }

        return await response.json();
    } catch (error) {
        console.error('Error triggering analysis:', error);
        return {
            status: 'error',
            message: 'Failed to trigger analysis'
        };
    }
}

export async function updateIncidentStatus(
    incidentId: number,
    status: string
): Promise<Incident | null> {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/incidents/${incidentId}`,
            {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ status }),
                cache: 'no-store'
            }
        );

        if (!response.ok) {
            console.error(
                'Failed to update incident:',
                response.status,
                await response.text()
            );
            return null;
        }

        return await response.json();
    } catch (error) {
        console.error('Error updating incident:', error);
        return null;
    }
}
