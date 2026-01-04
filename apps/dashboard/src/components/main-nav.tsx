'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useFlags, useLDClient } from 'launchdarkly-react-client-sdk';

export function MainNav({
    className,
    ...props
}: React.HTMLAttributes<HTMLElement>) {
    const pathname = usePathname();
    const flags = useFlags();
    const ldClient = useLDClient();

    // Feature flag to control logs tab visibility
    // This flag should be OFF by default (not available to users)
    const showLogsTab = flags['show-logs-tab'] ?? false;

    // Debug: Log flags and client status
    useEffect(() => {
        if (ldClient) {
            console.log('LaunchDarkly Client Status:', {
                clientAvailable: true,
                flags: flags,
                'show-logs-tab': flags['show-logs-tab']
            });

            // If flag is explicitly undefined, it might not exist or not be loaded yet
            if (flags['show-logs-tab'] === undefined) {
                console.warn(
                    'show-logs-tab flag not found. Make sure the flag exists in LaunchDarkly with key: "show-logs-tab"'
                );
            }
        } else {
            console.warn(
                'LaunchDarkly client not available - flags will default to false'
            );
        }
    }, [flags, ldClient]);

    return (
        <nav
            className={cn(
                'flex items-center space-x-4 lg:space-x-6',
                className
            )}
            {...props}
        >
            <Link
                href="/"
                className={cn(
                    'text-sm font-medium transition-colors hover:text-primary',
                    pathname === '/' ? 'text-primary' : 'text-muted-foreground'
                )}
            >
                Overview
            </Link>
            <Link
                href="/incidents"
                className={cn(
                    'text-sm font-medium transition-colors hover:text-primary',
                    pathname?.startsWith('/incidents')
                        ? 'text-primary'
                        : 'text-muted-foreground'
                )}
            >
                Incidents
            </Link>
            {showLogsTab && (
                <Link
                    href="/logs"
                    className={cn(
                        'text-sm font-medium transition-colors hover:text-primary',
                        pathname?.startsWith('/logs')
                            ? 'text-primary'
                            : 'text-muted-foreground'
                    )}
                >
                    Logs
                </Link>
            )}
            <Link
                href="/settings"
                className={cn(
                    'text-sm font-medium transition-colors hover:text-primary',
                    pathname?.startsWith('/settings')
                        ? 'text-primary'
                        : 'text-muted-foreground'
                )}
            >
                Settings
            </Link>
        </nav>
    );
}
