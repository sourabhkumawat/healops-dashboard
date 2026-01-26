'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { SubmitButton } from '@/components/submit-button';
import { API_BASE } from '@/lib/config';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle
} from '@/components/ui/card';
import { AlertCircle, Loader2, CheckCircle2 } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import Link from 'next/link';

export default function SignupPage() {
    const router = useRouter();
    const [isRedirecting, setIsRedirecting] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string>('');
    const [success, setSuccess] = useState(false);
    const [successMessage, setSuccessMessage] = useState('');

    useEffect(() => {
        if (success) {
            // Show success state briefly then redirect to login
            const timer = setTimeout(() => {
                setIsRedirecting(true);
                router.push('/login');
            }, 2000);
            return () => clearTimeout(timer);
        }
    }, [success, router]);

    const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        setIsLoading(true);
        setError('');
        setSuccess(false);

        const formData = new FormData(e.currentTarget);
        const email = formData.get('email') as string;
        const password = formData.get('password') as string;

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
                const errorData = await response.json().catch(() => ({ detail: 'Registration failed' }));
                setError(errorData.detail || 'Registration failed');
                setIsLoading(false);
                return;
            }

            setSuccess(true);
            setSuccessMessage('Registration successful! Please login.');
            setIsLoading(false);
        } catch (error) {
            console.error('Registration error:', error);
            if (error instanceof Error && error.message.includes('fetch failed')) {
                setError('Unable to connect to server. Please ensure the backend server is running on http://localhost:8000.');
            } else {
                setError('An unexpected error occurred. Please try again.');
            }
            setIsLoading(false);
        }
    };

    return (
        <div className="flex h-screen w-full items-center justify-center bg-zinc-950 relative">
            {isRedirecting && (
                <div className="absolute inset-0 bg-zinc-950/95 backdrop-blur-sm z-50 flex items-center justify-center">
                    <div className="flex flex-col items-center gap-4">
                        <Loader2 className="h-12 w-12 animate-spin text-green-600" />
                        <p className="text-zinc-100 text-lg font-medium">
                            Redirecting to Login...
                        </p>
                    </div>
                </div>
            )}
            <Card className="w-[350px] border-zinc-800 bg-zinc-900 text-zinc-100">
                <CardHeader className="space-y-1">
                    <div className="flex justify-center mb-4">
                        <div className="rounded-lg bg-zinc-800 p-3 flex items-center justify-center">
                            <Image
                                src="/logo.png"
                                alt="HealOps Logo"
                                width={48}
                                height={48}
                                className="h-12 w-12"
                                priority
                            />
                        </div>
                    </div>
                    <CardTitle className="text-2xl text-center">
                        Create Account
                    </CardTitle>
                    <CardDescription className="text-center text-zinc-400">
                        Get started with Healops
                    </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4">
                    <form onSubmit={handleSubmit}>
                        {success ? (
                            <Alert className="mb-4 bg-green-900/50 border-green-900 text-green-200">
                                <CheckCircle2 className="h-4 w-4" />
                                <AlertDescription>
                                    {successMessage}
                                </AlertDescription>
                            </Alert>
                        ) : error ? (
                            <Alert
                                variant="destructive"
                                className="mb-4 bg-red-900/50 border-red-900 text-red-200"
                            >
                                <AlertCircle className="h-4 w-4" />
                                <AlertDescription>
                                    {error}
                                </AlertDescription>
                            </Alert>
                        ) : null}

                        <div className="grid gap-2">
                            <Label htmlFor="email">Email</Label>
                            <Input
                                id="email"
                                name="email"
                                type="email"
                                placeholder="name@example.com"
                                className="bg-zinc-800 border-zinc-700"
                                required
                            />
                        </div>
                        <div className="grid gap-2 mt-4">
                            <Label htmlFor="password">Password</Label>
                            <Input
                                id="password"
                                name="password"
                                type="password"
                                className="bg-zinc-800 border-zinc-700"
                                required
                            />
                        </div>
                        <div className="mt-6">
                            <SubmitButton disabled={isLoading} isLoading={isLoading} loadingText="Signing Up...">
                                Sign Up
                            </SubmitButton>
                        </div>
                    </form>
                </CardContent>
                <CardFooter className="flex flex-col gap-2">
                    <div className="text-sm text-center text-zinc-400">
                        Already have an account?{' '}
                        <Link
                            href="/login"
                            className="text-green-500 hover:text-green-400 hover:underline"
                        >
                            Login
                        </Link>
                    </div>
                    <p className="text-xs text-center text-zinc-500 w-full mt-2">
                        By clicking Sign Up, you agree to our Terms and Policy
                    </p>
                </CardFooter>
            </Card>
        </div>
    );
}
