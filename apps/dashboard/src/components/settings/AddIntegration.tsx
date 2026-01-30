'use client';

import { useState, useEffect, useRef } from 'react';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Cloud, Box, Key, Copy, ExternalLink, Loader2 } from 'lucide-react';
import {
    generateApiKey,
    connectGithub,
    connectSignoz,
    listProviders,
    initiateGitHubOAuth,
    initiateLinearOAuth
} from '@/lib/integrations-client';

interface AddIntegrationProps {
    onCancel: () => void;
    onSuccess: () => void;
}

export function AddIntegration({ onCancel, onSuccess }: AddIntegrationProps) {
    const [selectedProvider, setSelectedProvider] = useState<string | null>(
        null
    );
    const [newApiKey, setNewApiKey] = useState('');
    const [signozUrl, setSignozUrl] = useState('');
    const [signozApiKey, setSignozApiKey] = useState('');
    const [loading, setLoading] = useState(false);
    const keyCounterRef = useRef(0);
    const [providers, setProviders] = useState<
        Array<{
            id: string;
            name: string;
            icon: any;
            color: string;
        }>
    >([
        {
            id: 'github',
            name: 'GitHub',
            icon: Box,
            color: 'text-white'
        },
        {
            id: 'linear',
            name: 'Linear',
            icon: Box,
            color: 'text-white'
        },
        {
            id: 'signoz',
            name: 'SigNoz',
            icon: Box,
            color: 'text-white'
        }
    ]);

    const fetchProviders = async () => {
        try {
            const data = await listProviders();
            if (
                data.providers &&
                Array.isArray(data.providers) &&
                data.providers.length > 0
            ) {
                const mappedProviders = data.providers.map((provider: any) => {
                    return {
                        id: provider.id || provider.provider?.toLowerCase(),
                        name: provider.name,
                        icon: Box,
                        color: 'text-white'
                    };
                });
                setProviders(mappedProviders);
            }
        } catch (error) {
            console.error('Failed to fetch providers:', error);
        }
    };

    useEffect(() => {
        fetchProviders();
    }, []);

    const handleConnect = async () => {
        if (!selectedProvider) return;

        setLoading(true);
        if (selectedProvider === 'github') {
            const result = await connectGithub(newApiKey);
            if (result.status === 'connected') {
                setNewApiKey('');
                onSuccess();
            } else {
                console.error(result.error);
            }
        } else {
            // Legacy key generation
            keyCounterRef.current += 1;
            const result = await generateApiKey(
                `${selectedProvider}-integration-${keyCounterRef.current}`
            );

            if (result.apiKey) {
                setNewApiKey(result.apiKey);
                // We don't close immediately for other providers as we show the key
                // trigger success to refresh list but keep UI open?
                // Creating a key isn't strictly "adding an integration" in the list sense if it's just a key?
                // But the original code called `fetchIntegrations` after success.
                // For manual key gen, it creates a key. The user copies it.
                // It calls `fetchApiKeys` in page.tsx.
                // It does NOT call `fetchIntegrations`.
                // Wait, page.tsx:168: if result.apiKey -> setNewApiKey -> fetchApiKeys.
                // It does NOT call onSuccess (which implies fetchIntegrations).
            }
        }
        setLoading(false);
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
    };

    return (
        <Card className="border-zinc-800 bg-zinc-900">
            <CardHeader>
                <CardTitle className="text-zinc-100">
                    Add New Integration
                </CardTitle>
                <CardDescription className="text-zinc-400">
                    Select a platform to connect
                </CardDescription>
            </CardHeader>
            <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {providers.map((provider) => {
                        const Icon = provider.icon;
                        return (
                            <Card
                                key={provider.id}
                                className={`cursor-pointer border-2 transition-all ${
                                    selectedProvider === provider.id
                                        ? 'border-green-600 bg-zinc-800'
                                        : 'border-zinc-700 bg-zinc-800/50 hover:border-zinc-600'
                                }`}
                                onClick={() => setSelectedProvider(provider.id)}
                            >
                                <CardContent className="p-4 text-center">
                                    <Icon
                                        className={`h-8 w-8 mx-auto mb-2 ${provider.color}`}
                                    />
                                    <p className="text-sm font-medium text-zinc-100">
                                        {provider.name}
                                    </p>
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>

                {selectedProvider && (
                    <div className="mt-6 space-y-4">
                        {selectedProvider === 'github' ? (
                            <div className="text-center space-y-4 py-4">
                                <p className="text-sm text-zinc-400">
                                    Connect your GitHub account to enable
                                    automatic pull request creation and code
                                    management.
                                </p>
                                <Button
                                    onClick={async () => {
                                        const result =
                                            await initiateGitHubOAuth();
                                        if (result.error) {
                                            console.error(
                                                'Failed to initiate OAuth:',
                                                result.error
                                            );
                                            alert(
                                                'Failed to connect GitHub. Please try again.'
                                            );
                                        } else if (result.redirectUrl) {
                                            window.location.href =
                                                result.redirectUrl;
                                        }
                                    }}
                                    className="w-full bg-[#24292F] hover:bg-[#24292F]/90 text-white"
                                >
                                    <Cloud className="h-4 w-4 mr-2" />
                                    Connect with GitHub
                                </Button>
                            </div>
                        ) : selectedProvider === 'linear' ? (
                            <div className="text-center space-y-4 py-4">
                                <p className="text-sm text-zinc-400">
                                    Connect your Linear workspace to enable
                                    automatic issue creation for incidents and
                                    link tickets to branches.
                                </p>
                                <Button
                                    onClick={async () => {
                                        const result =
                                            await initiateLinearOAuth();
                                        if (result.error) {
                                            console.error(
                                                'Failed to initiate Linear OAuth:',
                                                result.error
                                            );
                                            alert(
                                                'Failed to connect Linear. Please try again.'
                                            );
                                        } else if (result.redirectUrl) {
                                            window.location.href =
                                                result.redirectUrl;
                                        }
                                    }}
                                    className="w-full bg-[#5E6AD2] hover:bg-[#5E6AD2]/90 text-white"
                                >
                                    <Cloud className="h-4 w-4 mr-2" />
                                    Connect with Linear
                                </Button>
                            </div>
                        ) : selectedProvider === 'signoz' ? (
                            <div className="space-y-4 py-4">
                                <p className="text-sm text-zinc-400">
                                    Connect SigNoz to fetch error logs and error traces only. HealOps will create incidents and, if Linear is connected, create a ticket for each.
                                </p>
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
                                <Button
                                    onClick={async () => {
                                        if (!signozUrl.trim() || !signozApiKey.trim()) {
                                            alert('Please enter SigNoz URL and API key.');
                                            return;
                                        }
                                        setLoading(true);
                                        const result = await connectSignoz(signozUrl, signozApiKey);
                                        setLoading(false);
                                        if (result.error) {
                                            alert(result.error);
                                        } else {
                                            setSignozUrl('');
                                            setSignozApiKey('');
                                            onSuccess();
                                        }
                                    }}
                                    disabled={loading}
                                    className="w-full bg-orange-600 hover:bg-orange-700 text-white"
                                >
                                    {loading ? (
                                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                    ) : (
                                        <Key className="h-4 w-4 mr-2" />
                                    )}
                                    Connect SigNoz
                                </Button>
                            </div>
                        ) : (
                            <>
                                <Alert className="bg-zinc-800 border-zinc-700">
                                    <AlertDescription className="text-zinc-300">
                                        Click below to generate an API key for
                                        this integration
                                    </AlertDescription>
                                </Alert>
                                <Button
                                    onClick={handleConnect}
                                    disabled={loading}
                                    className="w-full bg-green-600 hover:bg-green-700"
                                >
                                    {loading ? (
                                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                    ) : (
                                        <Key className="h-4 w-4 mr-2" />
                                    )}
                                    Generate API Key
                                </Button>

                                {newApiKey && (
                                    <div className="space-y-2">
                                        <Label className="text-zinc-100">
                                            Your API Key (save it now!)
                                        </Label>
                                        <div className="flex space-x-2">
                                            <Input
                                                value={newApiKey}
                                                readOnly
                                                className="bg-zinc-800 border-zinc-700 font-mono text-sm text-zinc-100"
                                            />
                                            <Button
                                                size="icon"
                                                variant="outline"
                                                onClick={() =>
                                                    copyToClipboard(newApiKey)
                                                }
                                                className="border-zinc-700"
                                            >
                                                <Copy className="h-4 w-4" />
                                            </Button>
                                        </div>
                                        <Button
                                            onClick={() =>
                                                (window.location.href =
                                                    '/onboarding')
                                            }
                                            className="w-full"
                                            variant="outline"
                                        >
                                            <ExternalLink className="h-4 w-4 mr-2" />
                                            Continue Setup
                                        </Button>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
