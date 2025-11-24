import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
 
export function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname
  
  // Define public paths
  const isPublicPath = path === '/login'
  
  // Check for auth token
  const token = request.cookies.get('auth_token')?.value || ''
  
  if (isPublicPath && token) {
    return NextResponse.redirect(new URL('/', request.nextUrl))
  }
  
  if (!isPublicPath && !token) {
    return NextResponse.redirect(new URL('/login', request.nextUrl))
  }
}
 
export const config = {
  matcher: [
    '/',
    '/incidents',
    '/settings',
    '/onboarding',
    '/login',
  ],
}
