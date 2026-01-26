import { getApiBaseUrl } from './config';

/**
 * Client-side helper to make authenticated requests to the backend
 * Reads the auth token from localStorage
 */
export async function fetchClient(endpoint: string, options: RequestInit = {}) {
    const baseUrl = getApiBaseUrl();
    const url = endpoint.startsWith('http')
        ? endpoint
        : `${baseUrl}${endpoint}`;

    const headers = new Headers(options.headers);
    if (!headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
    }

    // Add auth token if available
    if (typeof window !== 'undefined') {
        const token = localStorage.getItem('auth_token');
        if (token) {
            headers.set('Authorization', `Bearer ${token}`);
        }
    }

    try {
        const response = await fetch(url, {
            ...options,
            headers
        });

        if (response.status === 401) {
            // Token expired or invalid
            if (typeof window !== 'undefined') {
                localStorage.removeItem('auth_token');
                window.location.href = '/login';
            }
        }

        return response;
    } catch (error) {
        console.error('Client fetch error:', error);
        throw error;
    }
}
