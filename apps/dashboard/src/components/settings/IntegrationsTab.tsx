'use client';

import { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Cloud, Plus } from 'lucide-react';
import { listIntegrations } from '@/actions/integrations';
import { IntegrationItem } from './IntegrationItem';
import { AddIntegration } from './AddIntegration';

type Integration = {
    id: number;
    provider: string;
    name: string;
    status: string;
    created_at: string;
    last_verified: string | null;
    project_id?: string | null;
};

export function IntegrationsTab() {
    const [integrations, setIntegrations] = useState<Integration[]>([]);
    const [showAddIntegration, setShowAddIntegration] = useState(false);
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
        // Check for success param first
        const params = new URLSearchParams(window.location.search);
        const githubConnected = params.get('github_connected') === 'true';
        const linearConnected = params.get('linear_connected') === 'true';

        if (githubConnected || linearConnected) {
            // Clear param
            window.history.replaceState({}, '', window.location.pathname);
            // If connected via OAuth, we likely want to refresh list
            // Fetch is called below anyway.
        }

        fetchIntegrations();
    }, []);

    const handleIntegrationAdded = () => {
        setShowAddIntegration(false);
        fetchIntegrations();
    };

    return (
        <div className="space-y-4">
            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-medium text-zinc-100">
                        Integrations
                    </h3>
                    <p className="text-sm text-zinc-400">
                        Connect multiple cloud providers to start monitoring
                    </p>
                </div>
                <Button
                    onClick={() => setShowAddIntegration(!showAddIntegration)}
                    className="bg-green-600 hover:bg-green-700"
                >
                    <Plus className="h-4 w-4 mr-2" />
                    Add Integration
                </Button>
            </div>

            {showAddIntegration && (
                <AddIntegration
                    onCancel={() => setShowAddIntegration(false)}
                    onSuccess={handleIntegrationAdded}
                />
            )}

            {/* Integrations List */}
            <div className="space-y-4">
                {integrations.length === 0 && !showAddIntegration ? (
                    <Card className="border-zinc-800 bg-zinc-900">
                        <CardContent className="p-12 text-center">
                            <Cloud className="h-12 w-12 mx-auto mb-4 text-zinc-600" />
                            <p className="text-zinc-400 mb-4">
                                No integrations yet
                            </p>
                            <Button
                                onClick={() => setShowAddIntegration(true)}
                                variant="outline"
                                className="border-zinc-700"
                            >
                                Add Your First Integration
                            </Button>
                        </CardContent>
                    </Card>
                ) : (
                    integrations.map((integration) => (
                        <IntegrationItem
                            key={integration.id}
                            integration={integration}
                        />
                    ))
                )}
            </div>
        </div>
    );
}
