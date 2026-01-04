'use client';

import {
    LDProvider,
    useLDClient,
    useFlags
} from 'launchdarkly-react-client-sdk';
import { useEffect } from 'react';

function LaunchDarklyTracker() {
    const ldClient = useLDClient();
    const flags = useFlags();

    useEffect(() => {
        if (ldClient) {
            // Log all available flags for debugging
            console.log('LaunchDarkly SDK initialized');
            console.log('Available flags:', flags);
            console.log('show-logs-tab flag:', flags['show-logs-tab']);

            // Track a custom event to validate SDK connectivity
            ldClient.track('sdk-initialized', { source: 'cursor' });
        } else {
            console.warn('LaunchDarkly client not initialized');
        }
    }, [ldClient, flags]);

    return null;
}

export function LaunchDarklyProvider({
    children
}: {
    children: React.ReactNode;
}) {
    const clientSideID = process.env.NEXT_PUBLIC_LAUNCHDARKLY_SDK_KEY || '';

    if (!clientSideID) {
        console.warn(
            'LaunchDarkly SDK key not found. Please set NEXT_PUBLIC_LAUNCHDARKLY_SDK_KEY environment variable.'
        );
        console.warn('Feature flags will not work without the SDK key.');
        return <>{children}</>;
    }

    console.log(
        'LaunchDarkly Provider: Initializing with clientSideID:',
        clientSideID.substring(0, 10) + '...'
    );

    return (
        <LDProvider
            clientSideID={clientSideID}
            context={{
                kind: 'user',
                key: 'anonymous-user'
            }}
            options={{
                bootstrap: {}
            }}
        >
            <LaunchDarklyTracker />
            {children}
        </LDProvider>
    );
}
