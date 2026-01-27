'use client';

import { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Key, Trash2, CheckCircle2, XCircle } from 'lucide-react';
import { listApiKeys } from '@/lib/integrations-client';

type ApiKey = {
    id: number;
    name: string;
    key_prefix: string;
    created_at: string;
    last_used: string | null;
    is_active: boolean;
};

export function ApiKeysTab() {
    const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
    const fetchingApiKeysRef = useRef(false);

    const fetchApiKeys = async () => {
        if (fetchingApiKeysRef.current) return;
        fetchingApiKeysRef.current = true;
        try {
            const keys = await listApiKeys();
            setApiKeys(keys);
        } catch (error) {
            console.error('Failed to fetch API keys:', error);
        } finally {
            fetchingApiKeysRef.current = false;
        }
    };

    useEffect(() => {
        fetchApiKeys();
    }, []);

    return (
        <div className="space-y-4">
            <div className="flex justify-between items-center">
                <p className="text-zinc-400">
                    {apiKeys.length} API key
                    {apiKeys.length !== 1 ? 's' : ''}
                </p>
            </div>

            <div className="space-y-4">
                {apiKeys.map((key) => (
                    <Card key={key.id} className="border-zinc-800 bg-zinc-900">
                        <CardContent className="p-6">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center space-x-4">
                                    <div className="rounded-lg bg-zinc-800 p-3">
                                        <Key className="h-6 w-6 text-green-500" />
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-zinc-100">
                                            {key.name}
                                        </h3>
                                        <p className="text-sm text-zinc-400 font-mono">
                                            {key.key_prefix}...
                                        </p>
                                        <p className="text-xs text-zinc-500 mt-1">
                                            Created{' '}
                                            {new Date(
                                                key.created_at
                                            ).toLocaleDateString()}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex items-center space-x-4">
                                    {key.is_active ? (
                                        <CheckCircle2 className="h-5 w-5 text-green-500" />
                                    ) : (
                                        <XCircle className="h-5 w-5 text-red-500" />
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
                        </CardContent>
                    </Card>
                ))}
            </div>
        </div>
    );
}
