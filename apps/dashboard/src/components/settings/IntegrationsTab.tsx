'use client';

import { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Check } from 'lucide-react';
import {
    listIntegrations,
    initiateGitHubOAuth,
    initiateLinearOAuth
} from '@/actions/integrations';
import Image from 'next/image';

type Integration = {
    id: number;
    provider: string;
    name: string;
    status: string;
    created_at: string;
    last_verified: string | null;
    project_id?: string | null;
};

const AVAILABLE_INTEGRATIONS = [
    {
        id: 'github',
        name: 'GitHub',
        description:
            'View the latest updates from GitHub in Notion pages and databases',
        logo: '/integration-logos/github-logo.png',
        color: 'bg-[#24292F] hover:bg-[#24292F]/90',
        badge: 'NEW'
    },
    {
        id: 'linear',
        name: 'Linear',
        description:
            'Bring Linear tasks into Notion to see the latest updates across teams',
        logo: '/integration-logos/linear-dark-logo.svg',
        color: 'bg-[#5E6AD2] hover:bg-[#5E6AD2]/90',
        badge: 'NEW'
    }
];

export function IntegrationsTab() {
    const [integrations, setIntegrations] = useState<Integration[]>([]);
    const [connecting, setConnecting] = useState<string | null>(null);
    const fetchingIntegrationsRef = useRef(false);

    const fetchIntegrations = async () => {
        if (fetchingIntegrationsRef.current) return;
        fetchingIntegrationsRef.current = true;
        try {
            const data = await listIntegrations();
            setIntegrations(data.integrations || []);
        } catch (error) {
            console.error('Failed to fetch integrations:', error);
        } finally {
            fetchingIntegrationsRef.current = false;
        }
    };

    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const githubConnected = params.get('github_connected') === 'true';
        const linearConnected = params.get('linear_connected') === 'true';

        if (githubConnected || linearConnected) {
            window.history.replaceState({}, '', window.location.pathname);
        }

        fetchIntegrations();
    }, []);

    const isConnected = (providerId: string) => {
        return integrations.some(
            (int) =>
                int.provider.toLowerCase() === providerId &&
                int.status === 'connected'
        );
    };

    const handleConnect = async (providerId: string) => {
        setConnecting(providerId);

        try {
            if (providerId === 'github') {
                const result = await initiateGitHubOAuth();
                if (result.error) {
                    console.error('Failed to initiate OAuth:', result.error);
                    alert('Failed to connect GitHub. Please try again.');
                } else if (result.redirectUrl) {
                    window.location.href = result.redirectUrl;
                }
            } else if (providerId === 'linear') {
                const result = await initiateLinearOAuth();
                if (result.error) {
                    console.error(
                        'Failed to initiate Linear OAuth:',
                        result.error
                    );
                    alert('Failed to connect Linear. Please try again.');
                } else if (result.redirectUrl) {
                    window.location.href = result.redirectUrl;
                }
            }
        } catch (error) {
            console.error('Connection error:', error);
            setConnecting(null);
        }
    };

    return (
        <div className="space-y-8 animate-fade-in">
            {/* Integration Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {AVAILABLE_INTEGRATIONS.map((integration, index) => {
                    const connected = isConnected(integration.id);
                    const isConnecting = connecting === integration.id;
                    const connectedIntegration = integrations.find(
                        (int) => int.provider.toLowerCase() === integration.id
                    );

                    return (
                        <Card
                            key={integration.id}
                            className={`group border-2 transition-all duration-300 overflow-hidden animate-slide-up ${
                                connected
                                    ? 'border-green-600/50 bg-zinc-900'
                                    : 'border-zinc-800 bg-zinc-900/50 hover:bg-zinc-900 hover:border-zinc-700'
                            }`}
                            style={{
                                animationDelay: `${index * 100}ms`
                            }}
                        >
                            <CardContent className="p-6 space-y-4">
                                {/* Logo and Status Badge */}
                                <div className="flex items-start justify-between">
                                    <div className="relative w-12 h-12 rounded-lg bg-white p-2 flex items-center justify-center">
                                        <Image
                                            src={integration.logo}
                                            alt={`${integration.name} logo`}
                                            width={40}
                                            height={40}
                                            className="object-contain"
                                        />
                                    </div>
                                    <div className="flex flex-col items-end gap-1">
                                        {connected ? (
                                            <span className="px-2 py-0.5 text-[10px] font-medium tracking-wider uppercase bg-green-600/20 text-green-500 rounded flex items-center gap-1">
                                                <Check className="w-3 h-3" />
                                                Active
                                            </span>
                                        ) : integration.badge ? (
                                            <span className="px-2 py-0.5 text-[10px] font-medium tracking-wider uppercase bg-zinc-800 text-zinc-400 rounded">
                                                {integration.badge}
                                            </span>
                                        ) : null}
                                    </div>
                                </div>

                                {/* Name and Description */}
                                <div className="space-y-2">
                                    <h3 className="text-lg font-medium text-zinc-100">
                                        {integration.name}
                                    </h3>
                                    <p className="text-sm text-zinc-400 leading-relaxed line-clamp-2">
                                        {integration.description}
                                    </p>
                                </div>

                                {/* Connection Details for Connected Integrations */}
                                {connected && connectedIntegration && (
                                    <div className="p-3 bg-zinc-800/50 rounded-lg border border-zinc-700/50">
                                        <div className="flex items-center justify-between text-xs">
                                            <span className="text-zinc-500">
                                                Connected as
                                            </span>
                                            <span className="text-zinc-300 font-medium">
                                                {connectedIntegration.name}
                                            </span>
                                        </div>
                                        {connectedIntegration.last_verified && (
                                            <div className="flex items-center justify-between text-xs mt-1">
                                                <span className="text-zinc-500">
                                                    Last verified
                                                </span>
                                                <span className="text-zinc-400">
                                                    {new Date(
                                                        connectedIntegration.last_verified
                                                    ).toLocaleDateString()}
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                )}

                                {/* Connect/Configure Button */}
                                {connected ? (
                                    <div className="flex gap-2">
                                        <Button
                                            variant="outline"
                                            className="flex-1 border-zinc-700 bg-transparent hover:bg-zinc-800 text-zinc-300"
                                            onClick={() => {
                                                // TODO: Implement configure logic
                                                console.log(
                                                    'Configure',
                                                    integration.id
                                                );
                                            }}
                                        >
                                            Configure
                                        </Button>
                                        <Button
                                            variant="outline"
                                            className="flex-1 border-red-900/50 bg-transparent hover:bg-red-900/20 text-red-400 hover:text-red-300"
                                            onClick={() => {
                                                // TODO: Implement disconnect logic
                                                console.log(
                                                    'Disconnect',
                                                    integration.id
                                                );
                                            }}
                                        >
                                            Disconnect
                                        </Button>
                                    </div>
                                ) : (
                                    <Button
                                        onClick={() =>
                                            handleConnect(integration.id)
                                        }
                                        disabled={isConnecting}
                                        className={`w-full ${integration.color} text-white transition-all duration-200`}
                                    >
                                        {isConnecting
                                            ? 'Connecting...'
                                            : 'Connect'}
                                    </Button>
                                )}
                            </CardContent>
                        </Card>
                    );
                })}
            </div>
        </div>
    );
}
