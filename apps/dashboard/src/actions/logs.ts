'use server';

import { cookies } from 'next/headers';

import { API_BASE } from '@/lib/config';

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
        // Get auth token from cookies
        const authToken = (await cookies()).get('auth_token')?.value;

        // We can also use the API key if we had one stored, but for dashboard we use JWT
        // However, the backend /logs endpoint currently expects an API key in header or query param
        // OR it falls back to listing all logs if no auth is provided (for testing)
        // Let's try to fetch with the auth token first

        const response = await fetch(`${API_BASE}/logs?limit=${limit}`, {
            headers: {
                Authorization: `Bearer ${authToken}`
            },
            cache: 'no-store' // Ensure we always get fresh data
        });

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
