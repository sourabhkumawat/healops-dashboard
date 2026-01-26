'use client';

import { useFormStatus } from 'react-dom';
import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';

interface SubmitButtonProps {
    children?: React.ReactNode;
    loadingText?: string;
}

export function SubmitButton({
    children = 'Sign In',
    loadingText = 'Authenticating...'
}: SubmitButtonProps) {
    const { pending } = useFormStatus();

    return (
        <Button
            className="w-full mt-6 bg-green-600 hover:bg-green-700 text-white"
            type="submit"
            disabled={pending}
        >
            {pending ? (
                <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {loadingText}
                </>
            ) : (
                children
            )}
        </Button>
    );
}
