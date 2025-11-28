'use server';

import { cookies } from 'next/headers';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function generateApiKey(name: string) {
    try {
        console.log('Generating API key for:', name);
        const response = await fetch(`${API_BASE}/api-keys/generate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name })
        });

        console.log('Response status:', response.status);

        if (!response.ok) {
            const errorText = await response.text();
            console.error('API error:', errorText);
            return { error: `Failed to generate API key: ${response.status}` };
        }

        const data = await response.json();
        console.log('API key generated successfully');
        return { apiKey: data.api_key, keyPrefix: data.key_prefix };
    } catch (error) {
        console.error('Network error:', error);
        return { error: `Network error: ${error}` };
    }
}



export async function getAgentInstallCommand(apiKey: string) {
    try {
        const response = await fetch(
            `${API_BASE}/integrations/agent/install-command?api_key=${encodeURIComponent(
                apiKey
            )}`
        );
        const data = await response.json();
        return { linux: data.linux, windows: data.windows };
    } catch (error) {
        return { error: 'Failed to get install command' };
    }
}

export async function listProviders() {
    try {
        const response = await fetch(`${API_BASE}/integrations/providers`);
        const data = await response.json();
        return data;
    } catch (error) {
        return {};
    }
}
