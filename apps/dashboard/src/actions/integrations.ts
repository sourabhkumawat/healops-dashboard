'use server';

import { cookies } from 'next/headers';

import { API_BASE } from '@/lib/config';
import { fetchWithAuth, getAuthHeaders } from '@/lib/api-client';

export async function generateApiKey(name: string) {
    try {
        console.log('Generating API key for:', name);
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(`${API_BASE}/api-keys/generate`, {
            method: 'POST',
            headers,
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
        if (!response.ok) {
            console.error('Failed to fetch providers:', response.status, response.statusText);
            return { providers: [] };
        }
        const data = await response.json();
        console.log('Providers fetched:', data);
        return data;
    } catch (error) {
        console.error('Error fetching providers:', error);
        return { providers: [] };
    }
}

export async function listIntegrations() {
    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(`${API_BASE}/integrations`, {
            headers
        });
        if (!response.ok) {
            return { integrations: [] };
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching integrations:', error);
        return { integrations: [] };
    }
}

export async function listApiKeys() {
    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(`${API_BASE}/api-keys`, {
            headers
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

export async function getIntegrationConfig(integrationId: number) {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/integrations/${integrationId}/config`
        );
        if (!response.ok) {
            return { error: 'Failed to fetch integration config' };
        }
        return await response.json();
    } catch (error) {
        return { error: 'Failed to fetch integration config' };
    }
}

export async function addServiceMapping(
    integrationId: number,
    serviceName: string,
    repoName: string
) {
    try {
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(
            `${API_BASE}/integrations/${integrationId}/service-mapping`,
            {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    service_name: serviceName,
                    repo_name: repoName
                })
            }
        );
        if (!response.ok) {
            const errorText = await response.text();
            return { error: errorText };
        }
        return await response.json();
    } catch (error) {
        return { error: 'Failed to add service mapping' };
    }
}

export async function removeServiceMapping(
    integrationId: number,
    serviceName: string
) {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/integrations/${integrationId}/service-mapping/${serviceName}`,
            {
                method: 'DELETE'
            }
        );
        if (!response.ok) {
            const errorText = await response.text();
            return { error: errorText };
        }
        return await response.json();
    } catch (error) {
        return { error: 'Failed to remove service mapping' };
    }
}

export async function getServices() {
    try {
        const apiUrl = `${API_BASE}/services`;
        console.log('Fetching services from:', apiUrl);
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(apiUrl, {
            method: 'GET',
            headers,
            cache: 'no-store'
        });
        console.log('Services response status:', response.status);
        if (!response.ok) {
            const errorText = await response.text();
            console.error(
                'Failed to fetch services:',
                response.status,
                errorText
            );
            return { services: [] };
        }
        const data = await response.json();
        console.log('Services fetched:', data);
        return data;
    } catch (error) {
        console.error('Error fetching services:', error);
        return { services: [] };
    }
}

export async function getRepositories(integrationId: number) {
    try {
        const apiUrl = `${API_BASE}/integrations/${integrationId}/repositories`;
        console.log('Fetching repositories from:', apiUrl);
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(apiUrl, {
            method: 'GET',
            headers,
            cache: 'no-store'
        });
        console.log('Repositories response status:', response.status);
        if (!response.ok) {
            const errorText = await response.text();
            console.error(
                'Failed to fetch repositories:',
                response.status,
                errorText
            );
            return { repositories: [] };
        }
        const data = await response.json();
        console.log('Repositories fetched:', data);
        return data;
    } catch (error) {
        console.error('Error fetching repositories:', error);
        return { repositories: [] };
    }
}

export async function completeIntegrationSetup(
    integrationId: number,
    setupData: { default_repo: string; service_mappings?: Record<string, string> }
) {
    try {
        const apiUrl = `${API_BASE}/integrations/${integrationId}/setup`;
        console.log('Completing integration setup:', apiUrl);
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(apiUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify(setupData)
        });
        console.log('Setup response status:', response.status);
        if (!response.ok) {
            const errorText = await response.text();
            console.error(
                'Failed to complete setup:',
                response.status,
                errorText
            );
            return { error: errorText || 'Failed to complete setup' };
        }
        const data = await response.json();
        console.log('Setup completed successfully:', data);
        return data;
    } catch (error) {
        console.error('Error completing setup:', error);
        return { error: 'Failed to complete integration setup' };
    }
}

export async function updateIntegration(
    integrationId: number,
    updateData: {
        default_repo?: string;
        service_mappings?: Record<string, string>;
        name?: string;
    }
) {
    try {
        const apiUrl = `${API_BASE}/integrations/${integrationId}`;
        console.log('Updating integration:', apiUrl);
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(apiUrl, {
            method: 'PUT',
            headers,
            body: JSON.stringify(updateData)
        });
        console.log('Update response status:', response.status);
        if (!response.ok) {
            const errorText = await response.text();
            console.error(
                'Failed to update integration:',
                response.status,
                errorText
            );
            return { error: errorText || 'Failed to update integration' };
        }
        const data = await response.json();
        console.log('Integration updated successfully:', data);
        return data;
    } catch (error) {
        console.error('Error updating integration:', error);
        return { error: 'Failed to update integration' };
    }
}

export async function getIntegrationDetails(integrationId: number) {
    try {
        const apiUrl = `${API_BASE}/integrations/${integrationId}`;
        console.log('Fetching integration details from:', apiUrl);
        const headers = await getAuthHeaders();
        const response = await fetchWithAuth(apiUrl, {
            method: 'GET',
            headers,
            cache: 'no-store'
        });
        console.log('Integration details response status:', response.status);
        if (!response.ok) {
            const errorText = await response.text();
            console.error(
                'Failed to fetch integration details:',
                response.status,
                errorText
            );
            return { error: errorText || 'Failed to fetch integration details' };
        }
        const data = await response.json();
        console.log('Integration details fetched:', data);
        return data;
    } catch (error) {
        console.error('Error fetching integration details:', error);
        return { error: 'Failed to fetch integration details' };
    }
}

export async function initiateGitHubOAuth() {
    try {
        const apiUrl = `${API_BASE}/integrations/github/authorize`;
        console.log('Initiating GitHub OAuth:', apiUrl);
        const headers = await getAuthHeaders();
        
        // Make authenticated request - backend will return a redirect
        const response = await fetchWithAuth(apiUrl, {
            method: 'GET',
            headers,
            redirect: 'manual' // Don't follow redirect, we want the Location header
        });
        
        console.log('OAuth response status:', response.status);
        
        if (response.status === 302 || response.status === 307 || response.status === 308) {
            const location = response.headers.get('Location');
            if (location) {
                return { redirectUrl: location };
            }
        }
        
        // If not a redirect, something went wrong
        const errorText = await response.text();
        console.error('Failed to initiate OAuth:', response.status, errorText);
        return { error: errorText || 'Failed to initiate GitHub OAuth' };
    } catch (error) {
        console.error('Error initiating GitHub OAuth:', error);
        return { error: 'Failed to initiate GitHub OAuth' };
    }
}

export async function reconnectGitHubIntegration(integrationId: number) {
    try {
        const apiUrl = `${API_BASE}/integrations/github/reconnect?integration_id=${integrationId}`;
        console.log('Reconnecting GitHub integration:', apiUrl);
        const headers = await getAuthHeaders();
        
        // Make authenticated request - backend will return a redirect
        const response = await fetchWithAuth(apiUrl, {
            method: 'GET',
            headers,
            redirect: 'manual' // Don't follow redirect, we want the Location header
        });
        
        console.log('Reconnect response status:', response.status);
        
        if (response.status === 302 || response.status === 307 || response.status === 308) {
            const location = response.headers.get('Location');
            if (location) {
                return { redirectUrl: location };
            }
        }
        
        // If not a redirect, something went wrong
        const errorText = await response.text();
        console.error('Failed to reconnect:', response.status, errorText);
        return { error: errorText || 'Failed to reconnect GitHub integration' };
    } catch (error) {
        console.error('Error reconnecting GitHub integration:', error);
        return { error: 'Failed to reconnect GitHub integration' };
    }
}

export async function initiateLinearOAuth(reconnect?: boolean, integrationId?: number) {
    try {
        let apiUrl = `${API_BASE}/integrations/linear/authorize`;
        const params = new URLSearchParams();
        if (reconnect) params.append('reconnect', 'true');
        if (integrationId) params.append('integration_id', integrationId.toString());
        if (params.toString()) apiUrl += `?${params.toString()}`;
        
        console.log('Initiating Linear OAuth:', apiUrl);
        const headers = await getAuthHeaders();
        
        // Make authenticated request - backend will return a redirect
        const response = await fetchWithAuth(apiUrl, {
            method: 'GET',
            headers,
            redirect: 'manual' // Don't follow redirect, we want the Location header
        });
        
        console.log('Linear OAuth response status:', response.status);
        
        if (response.status === 302 || response.status === 307 || response.status === 308) {
            const location = response.headers.get('Location');
            if (location) {
                return { redirectUrl: location };
            }
        }
        
        // If not a redirect, something went wrong
        const errorText = await response.text();
        console.error('Failed to initiate Linear OAuth:', response.status, errorText);
        return { error: errorText || 'Failed to initiate Linear OAuth' };
    } catch (error) {
        console.error('Error initiating Linear OAuth:', error);
        return { error: 'Failed to initiate Linear OAuth' };
    }
}

export async function reconnectLinearIntegration(integrationId: number) {
    try {
        const apiUrl = `${API_BASE}/integrations/linear/reconnect/${integrationId}`;
        console.log('Reconnecting Linear integration:', apiUrl);
        const headers = await getAuthHeaders();
        
        // Make authenticated request - backend will return a redirect
        const response = await fetchWithAuth(apiUrl, {
            method: 'POST',
            headers,
            redirect: 'manual' // Don't follow redirect, we want the Location header
        });
        
        console.log('Reconnect response status:', response.status);
        
        if (response.status === 302 || response.status === 307 || response.status === 308) {
            const location = response.headers.get('Location');
            if (location) {
                return { redirectUrl: location };
            }
        }
        
        // If not a redirect, something went wrong
        const errorText = await response.text();
        console.error('Failed to reconnect:', response.status, errorText);
        return { error: errorText || 'Failed to reconnect Linear integration' };
    } catch (error) {
        console.error('Error reconnecting Linear integration:', error);
        return { error: 'Failed to reconnect Linear integration' };
    }
}

export async function disconnectIntegration(integrationId: number) {
    try {
        const apiUrl = `${API_BASE}/integrations/${integrationId}`;
        console.log('Disconnecting integration:', apiUrl);
        const headers = await getAuthHeaders();

        const response = await fetchWithAuth(apiUrl, {
            method: 'DELETE',
            headers
        });

        console.log('Disconnect response status:', response.status);

        if (!response.ok) {
            const errorText = await response.text();
            console.error('Failed to disconnect integration:', response.status, errorText);
            return { error: errorText || 'Failed to disconnect integration' };
        }

        const data = await response.json();
        console.log('Integration disconnected successfully:', data);
        return data;
    } catch (error) {
        console.error('Error disconnecting integration:', error);
        return { error: 'Failed to disconnect integration' };
    }
}
