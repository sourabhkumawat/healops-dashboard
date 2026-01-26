'use client';

import { useActionState, useEffect } from 'react';
import Image from 'next/image';
import { SubmitButton } from '@/components/submit-button';
import { loginAction } from '@/actions/auth';
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
import { AlertCircle, Loader2 } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';

const initialState = {
    message: ''
};

export default function LoginPage() {
    const [state, formAction] = useActionState(loginAction, initialState);

    useEffect(() => {
        if (state?.success) {
            console.log('✅ Login successful, redirecting...');

            // Store token for client-side API calls
            if (state.access_token) {
                localStorage.setItem('auth_token', state.access_token);
            }

            // Use window.location.href for a full page reload
            // This ensures middleware runs and server components load properly
            window.location.href = state.redirect || '/';
        }
    }, [state]);

    return (
        <div className="flex h-screen w-full items-center justify-center bg-zinc-950 relative">
            {state?.success && (
                <div className="absolute inset-0 bg-zinc-950/95 backdrop-blur-sm z-50 flex items-center justify-center">
                    <div className="flex flex-col items-center gap-4">
                        <Loader2 className="h-12 w-12 animate-spin text-green-600" />
                        <p className="text-zinc-100 text-lg font-medium">
                            Redirecting...
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
                        Healops
                    </CardTitle>
                    <CardDescription className="text-center text-zinc-400">
                        Enter your credentials to access the console
                    </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4">
                    <form action={formAction}>
                        {state?.message && (
                            <Alert
                                variant="destructive"
                                className="mb-4 bg-red-900/50 border-red-900 text-red-200"
                            >
                                <AlertCircle className="h-4 w-4" />
                                <AlertDescription>
                                    {state.message}
                                </AlertDescription>
                            </Alert>
                        )}
                        <div className="grid gap-2">
                            <Label htmlFor="email">Email</Label>
                            <Input
                                id="email"
                                name="email"
                                type="email"
                                placeholder="admin@healops.ai"
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
                        <SubmitButton />
                    </form>
                </CardContent>
                <CardFooter className="flex flex-col gap-2">
                    <div className="text-sm text-center text-zinc-400">
                        Don&apos;t have an account?{' '}
                        <a
                            href="/signup"
                            className="text-green-500 hover:text-green-400 hover:underline"
                        >
                            Sign up
                        </a>
                    </div>
                    <p className="text-xs text-center text-zinc-500 w-full">
                        Protected by Healops Identity Guard
                    </p>
                </CardFooter>
            </Card>
        </div>
    );
}
