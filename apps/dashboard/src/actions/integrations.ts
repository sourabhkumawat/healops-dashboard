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

export async function connectGithub(accessToken: string) {
    try {
        const response = await fetch(
            `${API_BASE}/integrations/github/connect`,
            {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ access_token: accessToken })
            }
        );

        if (!response.ok) {
            const errorText = await response.text();
            return { error: errorText };
        }

        const data = await response.json();
        return data;
    } catch (error) {
        return { error: 'Failed to connect to GitHub' };
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

export async function listApiKeys() {
    try {
        // Get auth token from cookies
        const authToken = (await cookies()).get('auth_token')?.value;

        const response = await fetch(`${API_BASE}/api-keys`, {
            headers: {
                Authorization: `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            return [];
        }

        const data = await response.json();
        return data.keys || [];
    } catch (error) {
        console.error('Failed to list API keys:', error);
        return [];
    }
}

export async function getAgentInstallCommand(apiKey: string) {
    // In a real app, this might fetch from an API or be constructed dynamically
    const installUrl = `${API_BASE}/agent/install.sh`;
    return {
        linux: `curl -sL ${installUrl} | sudo bash -s -- --api-key=${apiKey}`,
        windows: `iwr -useb ${API_BASE}/agent/install.ps1 | iex`,
        macos: `curl -sL ${installUrl} | sudo bash -s -- --api-key=${apiKey}`
    };
}
