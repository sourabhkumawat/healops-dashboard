import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

/**
 * Decode JWT payload without verification (for expiration check only)
 * Only use for checking expiration in middleware - full validation happens on backend
 */
function decodeJWTPayload(token: string): { exp?: number } | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;

    const payload = parts[1];
    // Add padding if needed
    const paddedPayload = payload + '='.repeat((4 - payload.length % 4) % 4);
    const decoded = Buffer.from(paddedPayload, 'base64').toString('utf8');
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

/**
 * Check if JWT token is expired
 */
function isTokenExpired(token: string): boolean {
  const payload = decodeJWTPayload(token);
  if (!payload || !payload.exp) return true;

  // Check if token is expired (exp is in seconds, Date.now() is in milliseconds)
  return payload.exp * 1000 < Date.now();
}

export function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname

  // Define public paths that don't require authentication
  const isPublicPath = path === '/login' || path === '/signup'

  // Check for auth token
  const token = request.cookies.get('auth_token')?.value || ''

  // If user is on public path but has valid token, redirect to dashboard
  if (isPublicPath && token && !isTokenExpired(token)) {
    return NextResponse.redirect(new URL('/', request.nextUrl))
  }

  // If user is on protected path
  if (!isPublicPath) {
    // No token at all - redirect to login
    if (!token) {
      return NextResponse.redirect(new URL('/login', request.nextUrl))
    }

    // Token exists but is expired - clear cookie and redirect to login
    if (isTokenExpired(token)) {
      const response = NextResponse.redirect(new URL('/login', request.nextUrl))
      response.cookies.delete('auth_token')
      return response
    }
  }
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api (API routes)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - login and signup (public pages)
     * This ensures all dashboard routes are protected
     */
    '/((?!api|_next/static|_next/image|favicon.ico|login|signup).*)',
  ],
}