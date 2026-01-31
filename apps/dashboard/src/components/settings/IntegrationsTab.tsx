'use client';

import { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Check, Settings, Unlink, RotateCcw, AlertTriangle, Loader2 } from 'lucide-react';
import {
    listIntegrations,
    initiateGitHubOAuth,
    initiateLinearOAuth,
    connectSignoz,
    disconnectIntegration,
    getIntegrationConfig,
    reconnectGitHubIntegration,
    reconnectLinearIntegration
} from '@/lib/integrations-client';
import Image from 'next/image';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
    DialogDescription
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

type Integration = {
    id: number;
    provider: string;
    name: string;
    status: string;
    created_at: string;
    last_verified: string | null;
    project_id?: string | null;
};

type IntegrationConfig = {
    integration_id: number;
    provider: string;
    default_repo?: string;
    service_mappings?: Record<string, string>;
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
    },
    {
        id: 'signoz',
        name: 'SigNoz',
        description:
            'Fetch error logs and error traces from SigNoz to create incidents and auto-resolve with PRs.',
        logo: '/integration-logos/linear-dark-logo.svg',
        color: 'bg-orange-600/90 hover:bg-orange-600',
        badge: undefined
    }
];

export function IntegrationsTab() {
    const [integrations, setIntegrations] = useState<Integration[]>([]);
    const [loading, setLoading] = useState(true);
    const [connecting, setConnecting] = useState<string | null>(null);
    const [disconnecting, setDisconnecting] = useState<number | null>(null);
    const [reconnecting, setReconnecting] = useState<number | null>(null);
    const [integrationConfigs, setIntegrationConfigs] = useState<Record<number, IntegrationConfig>>({});
    const [loadingConfigs, setLoadingConfigs] = useState<Set<number>>(new Set());
    const [showSignozDialog, setShowSignozDialog] = useState(false);
    const [signozUrl, setSignozUrl] = useState('');
    const [signozApiKey, setSignozApiKey] = useState('');
    const [signozConnecting, setSignozConnecting] = useState(false);
    const fetchingIntegrationsRef = useRef(false);

    const fetchIntegrations = async () => {
        if (fetchingIntegrationsRef.current) return;
        fetchingIntegrationsRef.current = true;

        try {
            setLoading(true);
            const data = await listIntegrations();
            const integrationsData = data.integrations || [];
            setIntegrations(integrationsData);

            // Fetch configurations for connected integrations
            const activeIntegrations = integrationsData.filter(
                (int: Integration) => int.status === 'ACTIVE'
            );
            fetchIntegrationConfigs(activeIntegrations);
        } catch (error) {
            console.error('Failed to fetch integrations:', error);
        } finally {
            setLoading(false);
            fetchingIntegrationsRef.current = false;
        }
    };

    const fetchIntegrationConfigs = async (activeIntegrations: Integration[]) => {
        for (const integration of activeIntegrations) {
            if (loadingConfigs.has(integration.id)) continue;

            setLoadingConfigs(prev => new Set([...prev, integration.id]));
            try {
                const configData = await getIntegrationConfig(integration.id);
                if (!configData.error) {
                    setIntegrationConfigs(prev => ({
                        ...prev,
                        [integration.id]: configData
                    }));
                }
            } catch (error) {
                console.error(`Failed to fetch config for integration ${integration.id}:`, error);
            } finally {
                setLoadingConfigs(prev => {
                    const newSet = new Set(prev);
                    newSet.delete(integration.id);
                    return newSet;
                });
            }
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
                int.status === 'ACTIVE'
        );
    };

    const handleConnect = async (providerId: string) => {
        if (providerId === 'signoz') {
            setSignozUrl('');
            setSignozApiKey('');
            setShowSignozDialog(true);
            return;
        }

        setConnecting(providerId);
        try {
            if (providerId === 'github') {
                const result = await initiateGitHubOAuth();
                if (result.error) {
                    console.error('Failed to initiate OAuth:', result.error);
                    alert(
                        result.error.includes('log in')
                            ? result.error
                            : 'Failed to connect GitHub. Please try again.'
                    );
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
            alert('Failed to connect. Please try again.');
        } finally {
            setConnecting(null);
        }
    };

    const handleSignozConnect = async () => {
        if (!signozUrl.trim() || !signozApiKey.trim()) {
            alert('Please enter SigNoz URL and API key.');
            return;
        }
        setSignozConnecting(true);
        try {
            const result = await connectSignoz(signozUrl.trim(), signozApiKey.trim());
            if (result.error) {
                alert(result.error);
            } else {
                setShowSignozDialog(false);
                setSignozUrl('');
                setSignozApiKey('');
                await fetchIntegrations();
            }
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            alert(`Failed to connect: ${message}`);
        } finally {
            setSignozConnecting(false);
        }
    };

    const handleDisconnect = async (integrationId: number, integrationName: string) => {
        if (!confirm(`Are you sure you want to disconnect ${integrationName}? This will remove your authentication and you'll need to reconnect to use this integration.`)) {
            return;
        }

        setDisconnecting(integrationId);
        try {
            const result = await disconnectIntegration(integrationId);
            if (result.error) {
                alert(`Failed to disconnect: ${result.error}`);
            } else {
                // Remove config from state first for immediate UI update
                setIntegrationConfigs(prev => {
                    const newConfigs = { ...prev };
                    delete newConfigs[integrationId];
                    return newConfigs;
                });
                // Refresh integrations list to update connection status
                await fetchIntegrations();
            }
        } catch (error) {
            console.error('Disconnect error:', error);
            alert('Failed to disconnect integration. Please try again.');
        } finally {
            setDisconnecting(null);
        }
    };

    const handleReconnect = async (integrationId: number, provider: string) => {
        if (provider === 'SIGNOZ') {
            alert('To update SigNoz URL or API key, disconnect this integration and add SigNoz again with the new credentials.');
            return;
        }
        setReconnecting(integrationId);
        try {
            let result;
            if (provider === 'GITHUB') {
                result = await reconnectGitHubIntegration(integrationId);
            } else if (provider === 'LINEAR') {
                result = await reconnectLinearIntegration(integrationId);
            } else {
                setReconnecting(null);
                return;
            }

            if (result.error) {
                alert(`Failed to initiate reconnection: ${result.error}`);
                setReconnecting(null);
            } else if (result.redirectUrl) {
                window.location.href = result.redirectUrl;
            }
        } catch (error) {
            console.error('Reconnection error:', error);
            alert('Failed to initiate reconnection. Please try again.');
            setReconnecting(null);
        }
    };

    return (
        <div className="space-y-8 animate-fade-in">
            {loading ? (
                <div className="space-y-6">
                    <div className="flex items-center space-x-2">
                        <Loader2 className="h-5 w-5 animate-spin text-zinc-400" />
                        <h3 className="text-lg font-medium text-zinc-300">Loading integrations...</h3>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {AVAILABLE_INTEGRATIONS.map((integration, index) => (
                            <Card
                                key={`loading-${integration.id}`}
                                className="border-2 border-zinc-800 bg-zinc-900/50 overflow-hidden animate-pulse"
                                style={{
                                    animationDelay: `${index * 100}ms`
                                }}
                            >
                                <CardContent className="p-6 space-y-4">
                                    <div className="flex items-start justify-between">
                                        <div className="relative w-12 h-12 rounded-lg bg-zinc-800"></div>
                                        <div className="h-5 w-12 bg-zinc-800 rounded"></div>
                                    </div>
                                    <div className="space-y-2">
                                        <div className="h-6 bg-zinc-800 rounded w-3/4"></div>
                                        <div className="space-y-1">
                                            <div className="h-4 bg-zinc-800 rounded w-full"></div>
                                            <div className="h-4 bg-zinc-800 rounded w-5/6"></div>
                                        </div>
                                    </div>
                                    <div className="h-10 bg-zinc-800 rounded w-full"></div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {AVAILABLE_INTEGRATIONS.map((integration, index) => {
                        const connected = isConnected(integration.id);
                        const isConnecting = connecting === integration.id;
                        const connectedIntegration = integrations.find(
                            (int) => int.provider.toLowerCase() === integration.id && int.status === 'ACTIVE'
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

                                    <div className="space-y-2">
                                        <h3 className="text-lg font-medium text-zinc-100">
                                            {integration.name}
                                        </h3>
                                        <p className="text-sm text-zinc-400 leading-relaxed line-clamp-2">
                                            {integration.description}
                                        </p>
                                    </div>

                                    {connected && connectedIntegration && (
                                        <div className="space-y-3">
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

                                            {loadingConfigs.has(connectedIntegration.id) ? (
                                                <div className="p-3 bg-zinc-800/30 rounded-lg border border-zinc-700/30 text-center">
                                                    <span className="text-xs text-zinc-500">Loading configuration...</span>
                                                </div>
                                            ) : integrationConfigs[connectedIntegration.id] && (
                                                <div className="p-3 bg-zinc-800/30 rounded-lg border border-zinc-700/30 space-y-2">
                                                    {integrationConfigs[connectedIntegration.id].default_repo && (
                                                        <div>
                                                            <div className="flex items-center justify-between text-xs">
                                                                <span className="text-zinc-500">Default repo</span>
                                                                <span className="text-zinc-300 font-mono text-[10px]">
                                                                    {integrationConfigs[connectedIntegration.id].default_repo}
                                                                </span>
                                                            </div>
                                                        </div>
                                                    )}

                                                    {integrationConfigs[connectedIntegration.id].service_mappings &&
                                                     Object.keys(integrationConfigs[connectedIntegration.id].service_mappings!).length > 0 && (
                                                        <div>
                                                            <div className="text-xs text-zinc-500 mb-1">Service mappings</div>
                                                            <div className="space-y-1">
                                                                {Object.entries(integrationConfigs[connectedIntegration.id].service_mappings!).slice(0, 2).map(
                                                                    ([service, repo]) => (
                                                                        <div key={service} className="flex items-center justify-between text-[10px]">
                                                                            <span className="text-zinc-400">{service}</span>
                                                                            <span className="text-zinc-300 font-mono">
                                                                                {typeof repo === 'string' ? repo : JSON.stringify(repo)}
                                                                            </span>
                                                                        </div>
                                                                    )
                                                                )}
                                                                {Object.keys(integrationConfigs[connectedIntegration.id].service_mappings!).length > 2 && (
                                                                    <div className="text-[10px] text-zinc-500">
                                                                        +{Object.keys(integrationConfigs[connectedIntegration.id].service_mappings!).length - 2} more
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {connected ? (
                                        <div className="grid grid-cols-3 gap-1.5">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="border-zinc-700 bg-transparent hover:bg-zinc-800 text-zinc-300 text-xs h-8"
                                                onClick={() => {
                                                    // TODO: Open configuration modal/page
                                                    console.log('Configure', connectedIntegration?.id);
                                                }}
                                                disabled={disconnecting === connectedIntegration?.id || reconnecting === connectedIntegration?.id}
                                            >
                                                <Settings className="h-3 w-3 mr-1" />
                                                Config
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="border-blue-900/50 bg-transparent hover:bg-blue-900/20 text-blue-400 hover:text-blue-300 text-xs h-8"
                                                onClick={() => connectedIntegration && handleReconnect(connectedIntegration.id, connectedIntegration.provider)}
                                                disabled={disconnecting === connectedIntegration?.id || reconnecting === connectedIntegration?.id}
                                            >
                                                {reconnecting === connectedIntegration?.id ? (
                                                    <>
                                                        <div className="h-3 w-3 mr-1 border-2 border-blue-400/20 border-t-blue-400 rounded-full animate-spin" />
                                                        ...
                                                    </>
                                                ) : (
                                                    <>
                                                        <RotateCcw className="h-3 w-3 mr-1" />
                                                        Reconnect
                                                    </>
                                                )}
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="border-red-900/50 bg-transparent hover:bg-red-900/20 text-red-400 hover:text-red-300 text-xs h-8"
                                                onClick={() => connectedIntegration && handleDisconnect(connectedIntegration.id, connectedIntegration.name)}
                                                disabled={disconnecting === connectedIntegration?.id || reconnecting === connectedIntegration?.id}
                                            >
                                                {disconnecting === connectedIntegration?.id ? (
                                                    <>
                                                        <div className="h-3 w-3 mr-1 border-2 border-red-400/20 border-t-red-400 rounded-full animate-spin" />
                                                        ...
                                                    </>
                                                ) : (
                                                    <>
                                                        <Unlink className="h-3 w-3 mr-1" />
                                                        Disconnect
                                                    </>
                                                )}
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
            )}

            <Dialog open={showSignozDialog} onOpenChange={setShowSignozDialog}>
                <DialogContent className="sm:max-w-md bg-zinc-900 border-zinc-700">
                    <DialogHeader>
                        <DialogTitle className="text-zinc-100">Connect SigNoz</DialogTitle>
                        <DialogDescription className="text-zinc-400">
                            Enter your SigNoz URL and API key. HealOps will fetch error logs and traces to create incidents.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="space-y-2">
                            <Label className="text-zinc-100">SigNoz URL</Label>
                            <Input
                                type="url"
                                placeholder="https://app.signoz.io"
                                value={signozUrl}
                                onChange={(e) => setSignozUrl(e.target.value)}
                                className="bg-zinc-800 border-zinc-700 text-zinc-100"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label className="text-zinc-100">API Key</Label>
                            <Input
                                type="password"
                                placeholder="Your SigNoz API key"
                                value={signozApiKey}
                                onChange={(e) => setSignozApiKey(e.target.value)}
                                className="bg-zinc-800 border-zinc-700 text-zinc-100"
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            className="border-zinc-700"
                            onClick={() => setShowSignozDialog(false)}
                            disabled={signozConnecting}
                        >
                            Cancel
                        </Button>
                        <Button
                            className="bg-orange-600 hover:bg-orange-700 text-white"
                            onClick={handleSignozConnect}
                            disabled={signozConnecting}
                        >
                            {signozConnecting ? 'Connecting...' : 'Connect SigNoz'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}