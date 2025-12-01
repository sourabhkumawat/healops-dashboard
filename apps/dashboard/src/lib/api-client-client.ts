/**
 * Client-side API client helper to add authentication headers to requests
 * For use in client components (not server actions)
 *
 * Note: Since auth_token is httpOnly, we can't access it from client-side JavaScript.
 * For client components, we should use server actions instead, or make requests
 * through Next.js API routes that can access the cookies.
 *
 * This helper will attempt to get the token, but if httpOnly cookies are used,
 * the token won't be available and requests will work without auth (falling back to default user_id=1).
 */

export function getAuthHeadersClient(): HeadersInit {
    // Try to get auth token from cookies (client-side)
    // Note: This won't work if the cookie is httpOnly
    const getCookie = (name: string) => {
        if (typeof document === 'undefined') return null;
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop()?.split(';').shift();
        return null;
    };

    const authToken = getCookie('auth_token');

    const headers: HeadersInit = {
        'Content-Type': 'application/json'
    };

    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }

    return headers;
}

export async function fetchWithAuthClient(
    url: string,
    options: RequestInit = {}
): Promise<Response> {
    const headers = getAuthHeadersClient();

    return fetch(url, {
        ...options,
        headers: {
            ...headers,
            ...(options.headers || {})
        }
    });
}
