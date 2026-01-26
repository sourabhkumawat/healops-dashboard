'use client';

import { useFormStatus } from 'react-dom';
import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';

interface SubmitButtonProps {
    children?: React.ReactNode;
    loadingText?: string;
    disabled?: boolean;
    isLoading?: boolean;
}

export function SubmitButton({
    children = 'Sign In',
    loadingText = 'Authenticating...',
    disabled: disabledProp,
    isLoading: isLoadingProp
}: SubmitButtonProps) {
    // useFormStatus only works with server actions, but it's safe to call
    // even in client-side forms - it will just return { pending: false }
    const { pending } = useFormStatus();
    
    // Use provided props if available, otherwise fall back to form status
    const isLoading = isLoadingProp !== undefined ? isLoadingProp : pending;
    const disabled = disabledProp !== undefined ? disabledProp : pending;

    return (
        <Button
            className="w-full mt-6 bg-green-600 hover:bg-green-700 text-white"
            type="submit"
            disabled={disabled}
        >
            {isLoading ? (
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
