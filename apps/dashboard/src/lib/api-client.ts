/**
 * API client helper to add authentication headers to requests
 */
import { cookies } from 'next/headers';
import { API_BASE } from './config';

export async function getAuthHeaders(): Promise<HeadersInit> {
    const cookieStore = await cookies();
    const authToken = cookieStore.get('auth_token')?.value;

    const headers: HeadersInit = {
        'Content-Type': 'application/json'
    };

    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }

    return headers;
}

export async function fetchWithAuth(
    url: string,
    options: RequestInit = {}
): Promise<Response> {
    const headers = await getAuthHeaders();

    return fetch(url, {
        ...options,
        headers: {
            ...headers,
            ...(options.headers || {})
        }
    });
}
