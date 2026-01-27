'use client';

import { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Cloud, Trash2, Loader2 } from 'lucide-react';
import {
    reconnectGitHubIntegration,
    reconnectLinearIntegration,
    getIntegrationConfig
} from '@/lib/integrations-client';
import { IntegrationConfig } from './IntegrationConfig';

type Integration = {
    id: number;
    provider: string;
    name: string;
    status: string;
    created_at: string;
    last_verified: string | null;
    project_id?: string | null;
};

interface IntegrationItemProps {
    integration: Integration;
}

export function IntegrationItem({ integration }: IntegrationItemProps) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [config, setConfig] = useState<{
        default_repo?: string;
        service_mappings?: Record<string, string>;
    } | null>(null);
    const [loadingConfig, setLoadingConfig] = useState(false);

    const isGitHub = integration.provider === 'GITHUB';
    const isLinear = integration.provider === 'LINEAR';

    const fetchConfig = async () => {
        setLoadingConfig(true);
        try {
            const data = await getIntegrationConfig(integration.id);
            if (!data.error) {
                setConfig(data);
            }
        } finally {
            setLoadingConfig(false);
        }
    };

    const handleToggle = async () => {
        if (!isExpanded && !config) {
            setIsExpanded(true);
            await fetchConfig();
        } else {
            setIsExpanded(!isExpanded);
        }
    };

    const handleReconnect = async () => {
        const result = isGitHub
            ? await reconnectGitHubIntegration(integration.id)
            : await reconnectLinearIntegration(integration.id);

        if (result.error) {
            console.error('Failed to reconnect:', result.error);
            alert(
                `Failed to reconnect ${integration.provider}. Please try again.`
            );
        } else if (result.redirectUrl) {
            window.location.href = result.redirectUrl;
        }
    };

    const getStatusBadge = (status: string) => {
        const statusConfig = {
            ACTIVE: { color: 'bg-green-600', label: 'Active' },
            PENDING: { color: 'bg-yellow-600', label: 'Pending' },
            FAILED: { color: 'bg-red-600', label: 'Failed' },
            DISCONNECTED: { color: 'bg-zinc-600', label: 'Disconnected' }
        };
        const conf =
            statusConfig[status as keyof typeof statusConfig] ||
            statusConfig.PENDING;
        return (
            <Badge className={`${conf.color} text-white`}>{conf.label}</Badge>
        );
    };

    return (
        <Card className="border-zinc-800 bg-zinc-900">
            <CardContent className="p-6">
                <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-4 flex-1">
                        <div className="rounded-lg bg-zinc-800 p-3">
                            <Cloud className="h-6 w-6 text-green-500" />
                        </div>
                        <div className="flex-1">
                            <h3 className="font-semibold text-zinc-100">
                                {integration.name}
                            </h3>
                            <p className="text-sm text-zinc-400">
                                {integration.provider}
                            </p>
                            {integration.project_id && (
                                <p className="text-xs text-zinc-500 mt-1 font-mono">
                                    {integration.project_id}
                                </p>
                            )}
                            {config?.default_repo && (
                                <p className="text-xs text-zinc-500 mt-1">
                                    Default:{' '}
                                    <span className="font-mono">
                                        {config.default_repo}
                                    </span>
                                </p>
                            )}
                        </div>
                    </div>
                    <div className="flex items-center space-x-4">
                        {getStatusBadge(integration.status)}
                        {(isGitHub || isLinear) && (
                            <>
                                {isGitHub && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={handleToggle}
                                        className="border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                                    >
                                        {isExpanded ? 'Hide' : 'Configure'}
                                    </Button>
                                )}
                                <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={handleReconnect}
                                    className="border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                                >
                                    <Cloud className="h-4 w-4 mr-2" />
                                    Reconnect
                                </Button>
                            </>
                        )}
                        <Button
                            size="icon"
                            variant="ghost"
                            className="text-zinc-400 hover:text-red-500"
                        >
                            <Trash2 className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                {isExpanded &&
                    isGitHub &&
                    (loadingConfig ? (
                        <div className="flex justify-center p-4">
                            <Loader2 className="h-6 w-6 animate-spin text-zinc-400" />
                        </div>
                    ) : config ? (
                        <IntegrationConfig
                            integrationId={integration.id}
                            config={config}
                            onConfigUpdate={fetchConfig}
                        />
                    ) : null)}
            </CardContent>
        </Card>
    );
}
