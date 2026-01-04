'use client';

import { LDProvider, useLDClient, useFlags } from 'launchdarkly-react-client-sdk';
import { useEffect } from 'react';

function LaunchDarklyTracker() {
  const ldClient = useLDClient();
  const flags = useFlags();

  useEffect(() => {
    if (ldClient) {
      // Evaluate a flag to validate SDK connectivity
      // Replace 'example-flag' with an actual flag key from your LaunchDarkly project
      const flagValue = flags['example-flag'] ?? false;
      
      // Track a custom event to validate SDK connectivity
      ldClient.track('sdk-initialized', { source: 'cursor', flagValue });
      
      console.log('LaunchDarkly SDK initialized. Flag evaluation:', flagValue);
    }
  }, [ldClient, flags]);

  return null;
}

export function LaunchDarklyProvider({ children }: { children: React.ReactNode }) {
  const clientSideID = process.env.NEXT_PUBLIC_LAUNCHDARKLY_SDK_KEY || '';

  if (!clientSideID) {
    console.warn('LaunchDarkly SDK key not found. Please set NEXT_PUBLIC_LAUNCHDARKLY_SDK_KEY environment variable.');
    return <>{children}</>;
  }

  return (
    <LDProvider
      clientSideID={clientSideID}
      context={{
        kind: 'user',
        key: 'anonymous-user',
      }}
      options={{
        bootstrap: {},
      }}
    >
      <LaunchDarklyTracker />
      {children}
    </LDProvider>
  );
}

