'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { API_BASE } from '@/lib/config';

export async function loginAction(prevState: any, formData: FormData) {
    console.log('🔐 Login action called');
    const email = formData.get('email');
    const password = formData.get('password');
    console.log('📧 Email:', email);

    try {
        console.log('🌐 Calling API...');
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams({
                username: email as string,
                password: password as string
            })
        });

        console.log('📡 API Response status:', response.status);

        if (!response.ok) {
            console.log('❌ Login failed');
            return { message: 'Invalid email or password' };
        }

        interface LoginResponse {
            access_token: string;
            token_type: string;
        }

        const data = (await response.json()) as LoginResponse;

        (await cookies()).set('auth_token', data.access_token, {
            path: '/',
            httpOnly: true,
            secure: process.env.NODE_ENV === 'production',
            maxAge: 60 * 60 * 24 * 7 // 1 week
        });

        // Return success and let the client handle redirect
        return { success: true, redirect: '/' };
    } catch (error) {
        console.error('Login error:', error);
        return { message: 'An unexpected error occurred' };
    }
}

export async function logoutAction() {
    (await cookies()).delete('auth_token');
    redirect('/login');
}

export async function registerAction(prevState: any, formData: FormData) {
    const email = formData.get('email');
    const password = formData.get('password');

    try {
        const response = await fetch(`${API_BASE}/auth/register`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                email,
                password
            })
        });

        if (!response.ok) {
            const error = await response.json();
            return { message: error.detail || 'Registration failed' };
        }

        return {
            success: true,
            message: 'Registration successful! Please login.'
        };
    } catch (error) {
        console.error('Registration error:', error);
        return { message: 'An unexpected error occurred' };
    }
}
