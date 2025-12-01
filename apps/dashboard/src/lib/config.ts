/**
 * Centralized API configuration for the frontend
 * Uses environment variables with sensible defaults for development
 */

export const getApiBaseUrl = (): string => {
    if (typeof window !== 'undefined') {
        // Client-side: use NEXT_PUBLIC_ prefixed env var
        return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    }
    // Server-side: use NEXT_PUBLIC_ prefixed env var
    return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
};

export const getWebSocketUrl = (): string => {
    const apiUrl = getApiBaseUrl();
    // Convert http/https to ws/wss
    if (apiUrl.startsWith('https://')) {
        return apiUrl.replace('https://', 'wss://') + '/ws/logs';
    }
    return apiUrl.replace('http://', 'ws://') + '/ws/logs';
};

export const API_BASE = getApiBaseUrl();
export const WS_URL = getWebSocketUrl();
