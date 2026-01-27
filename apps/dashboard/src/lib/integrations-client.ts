/**
 * Client-side API helpers for integrations, api-keys, and services.
 * All requests go directly to the backend API via fetchClient.
 */
import { fetchClient } from './client-api';
import { getApiBaseUrl } from './config';

export async function generateApiKey(name: string) {
    const res = await fetchClient('/api-keys/generate', {
        method: 'POST',
        body: JSON.stringify({ name })
    });
    if (!res.ok) {
        const text = await res.text();
        return { error: `Failed to generate API key: ${res.status}` };
    }
    const data = await res.json();
    return { apiKey: data.api_key, keyPrefix: data.key_prefix };
}

export async function connectGithub(accessToken: string) {
    const res = await fetchClient('/integrations/github/connect', {
        method: 'POST',
        body: JSON.stringify({ access_token: accessToken })
    });
    if (!res.ok) {
        const text = await res.text();
        return { error: text };
    }
    return await res.json();
}

export async function listProviders() {
    const res = await fetchClient('/integrations/providers');
    if (!res.ok) return { providers: [] };
    const data = await res.json();
    return data;
}

export async function listIntegrations() {
    const res = await fetchClient('/integrations');
    if (!res.ok) return { integrations: [] };
    return await res.json();
}

export async function listApiKeys() {
    const res = await fetchClient('/api-keys');
    if (!res.ok) return [];
    const data = await res.json();
    return data.keys ?? [];
}

const base = () => getApiBaseUrl();

export function getAgentInstallCommand(apiKey: string) {
    const installUrl = `${base()}/agent/install.sh`;
    return {
        linux: `curl -sL ${installUrl} | sudo bash -s -- --api-key=${apiKey}`,
        windows: `iwr -useb ${base()}/agent/install.ps1 | iex`,
        macos: `curl -sL ${installUrl} | sudo bash -s -- --api-key=${apiKey}`
    };
}

export async function getIntegrationConfig(integrationId: number) {
    const res = await fetchClient(`/integrations/${integrationId}/config`);
    if (!res.ok) return { error: 'Failed to fetch integration config' };
    return await res.json();
}

export async function addServiceMapping(
    integrationId: number,
    serviceName: string,
    repoName: string
) {
    const res = await fetchClient(
        `/integrations/${integrationId}/service-mapping`,
        {
            method: 'POST',
            body: JSON.stringify({
                service_name: serviceName,
                repo_name: repoName
            })
        }
    );
    if (!res.ok) {
        const text = await res.text();
        return { error: text };
    }
    return await res.json();
}

export async function removeServiceMapping(
    integrationId: number,
    serviceName: string
) {
    const res = await fetchClient(
        `/integrations/${integrationId}/service-mapping/${encodeURIComponent(serviceName)}`,
        { method: 'DELETE' }
    );
    if (!res.ok) {
        const text = await res.text();
        return { error: text };
    }
    return await res.json();
}

export async function getServices() {
    const res = await fetchClient('/services');
    if (!res.ok) return { services: [] };
    const data = await res.json();
    return data;
}

export async function getRepositories(integrationId: number) {
    const res = await fetchClient(
        `/integrations/${integrationId}/repositories`
    );
    if (!res.ok) return { repositories: [] };
    const data = await res.json();
    return data;
}

export async function completeIntegrationSetup(
    integrationId: number,
    setupData: {
        default_repo: string;
        service_mappings?: Record<string, string>;
    }
) {
    const res = await fetchClient(
        `/integrations/${integrationId}/setup`,
        {
            method: 'POST',
            body: JSON.stringify(setupData)
        }
    );
    if (!res.ok) {
        const text = await res.text();
        return { error: text || 'Failed to complete setup' };
    }
    return await res.json();
}

export async function updateIntegration(
    integrationId: number,
    updateData: {
        default_repo?: string;
        service_mappings?: Record<string, string>;
        name?: string;
    }
) {
    const res = await fetchClient(
        `/integrations/${integrationId}`,
        {
            method: 'PUT',
            body: JSON.stringify(updateData)
        }
    );
    if (!res.ok) {
        const text = await res.text();
        return { error: text || 'Failed to update integration' };
    }
    return await res.json();
}

export async function getIntegrationDetails(integrationId: number) {
    const res = await fetchClient(`/integrations/${integrationId}`);
    if (!res.ok) {
        return {
            error: 'Failed to fetch integration details'
        };
    }
    return await res.json();
}

async function fetchRedirect(
    url: string,
    options: RequestInit = {}
): Promise<{ redirectUrl?: string; error?: string }> {
    const res = await fetchClient(url, {
        ...options,
        redirect: 'manual' as RequestRedirect
    });
    if (res.status === 302 || res.status === 307 || res.status === 308) {
        const location = res.headers.get('Location');
        if (location) return { redirectUrl: location };
    }
    const text = await res.text();
    return { error: text || 'Redirect failed' };
}

export async function initiateGitHubOAuth() {
    return fetchRedirect('/integrations/github/authorize');
}

export async function reconnectGitHubIntegration(integrationId: number) {
    return fetchRedirect(
        `/integrations/github/reconnect?integration_id=${integrationId}`
    );
}

export async function initiateLinearOAuth(
    reconnect?: boolean,
    integrationId?: number
) {
    const params = new URLSearchParams();
    if (reconnect) params.append('reconnect', 'true');
    if (integrationId)
        params.append('integration_id', integrationId.toString());
    const qs = params.toString();
    const url = qs
        ? `/integrations/linear/authorize?${qs}`
        : '/integrations/linear/authorize';
    return fetchRedirect(url);
}

export async function reconnectLinearIntegration(integrationId: number) {
    return fetchRedirect(
        `/integrations/linear/reconnect/${integrationId}`,
        { method: 'POST' }
    );
}

export async function disconnectIntegration(integrationId: number) {
    const res = await fetchClient(`/integrations/${integrationId}`, {
        method: 'DELETE'
    });
    if (!res.ok) {
        const text = await res.text();
        return { error: text || 'Failed to disconnect integration' };
    }
    return await res.json();
}
