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

    // Create an AbortController for timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout

    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...headers,
                ...(options.headers || {})
            },
            signal: controller.signal
        });

        clearTimeout(timeoutId);

        // Handle 401 Unauthorized - token expired or invalid
        if (response.status === 401) {
            // Clear the invalid token and redirect to login
            await handleUnauthorized();
            throw new Error('Authentication expired. Please log in again.');
        }

        return response;
    } catch (error) {
        clearTimeout(timeoutId);

        // Handle timeout errors
        if (error instanceof Error && error.name === 'AbortError') {
            throw new Error(
                'Request timeout. Please check your connection and try again.'
            );
        }

        throw error;
    }
}

/**
 * Handle 401 unauthorized responses by clearing auth and redirecting to login
 */
async function handleUnauthorized(): Promise<void> {
    try {
        // Clear the auth token cookie
        const { cookies } = await import('next/headers');
        const cookieStore = await cookies();
        cookieStore.delete('auth_token');

        // Redirect to login page
        const { redirect } = await import('next/navigation');
        redirect('/login');
    } catch (error) {
        // If we're in a client component or unable to redirect server-side,
        // reload the page to trigger middleware protection
        if (typeof window !== 'undefined') {
            window.location.href = '/login';
        }
    }
}
