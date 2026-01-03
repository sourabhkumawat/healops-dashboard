'use client';

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue
} from '@/components/ui/select';
import { Loader2, CheckCircle2, Github, ArrowRight } from 'lucide-react';
import { getRepositories, completeIntegrationSetup } from '@/actions/integrations';

type Repository = {
    full_name: string;
    name: string;
    private: boolean;
};

function GitHubSetupPageContent() {
    const searchParams = useSearchParams();
    const router = useRouter();

    const integrationId = searchParams.get('integration_id');
    const isNew = searchParams.get('new') === 'true';
    const isReconnect = searchParams.get('reconnected') === 'true';

    const [repositories, setRepositories] = useState<Repository[]>([]);
    const [selectedRepo, setSelectedRepo] = useState('');
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!integrationId) {
            setError('No integration ID provided');
            setLoading(false);
            return;
        }
        fetchRepositories();
    }, [integrationId]);

    async function fetchRepositories() {
        try {
            setLoading(true);
            setError(null);

            const data = await getRepositories(Number(integrationId));

            if (data.error) {
                setError(data.error);
                setRepositories([]);
            } else if (data.repositories && Array.isArray(data.repositories)) {
                setRepositories(data.repositories);
                if (data.repositories.length === 0) {
                    setError(
                        'No repositories found. Make sure your GitHub account has access to repositories.'
                    );
                }
            } else {
                setError('Failed to load repositories');
                setRepositories([]);
            }
        } catch (err) {
            console.error('Error fetching repositories:', err);
            setError('Failed to load repositories. Please try again.');
            setRepositories([]);
        } finally {
            setLoading(false);
        }
    }

    async function handleCompleteSetup() {
        if (!selectedRepo) {
            setError('Please select a repository');
            return;
        }

        setSaving(true);
        setError(null);

        try {
            const result = await completeIntegrationSetup(
                Number(integrationId),
                {
                    default_repo: selectedRepo,
                    service_mappings: {}
                }
            );

            if (result.error) {
                setError(result.error);
                setSaving(false);
            } else {
                // Success - redirect to settings
                router.push('/settings?tab=integrations&github_setup_complete=true');
            }
        } catch (err) {
            console.error('Error completing setup:', err);
            setError('Failed to complete setup. Please try again.');
            setSaving(false);
        }
    }

    if (loading) {
        return (
            <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
                <Card className="w-full max-w-2xl border-zinc-800 bg-zinc-900">
                    <CardContent className="p-12 text-center">
                        <Loader2 className="h-12 w-12 animate-spin text-green-500 mx-auto mb-4" />
                        <p className="text-zinc-400">
                            Loading your GitHub repositories...
                        </p>
                    </CardContent>
                </Card>
            </div>
        );
    }

    if (error && repositories.length === 0) {
        return (
            <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
                <Card className="w-full max-w-2xl border-zinc-800 bg-zinc-900">
                    <CardHeader>
                        <CardTitle className="text-zinc-100 text-center">
                            Setup Failed
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Alert className="bg-red-900/20 border-red-700">
                            <AlertDescription className="text-red-300">
                                {error}
                            </AlertDescription>
                        </Alert>
                        <div className="flex justify-center space-x-4">
                            <Button
                                onClick={fetchRepositories}
                                variant="outline"
                                className="border-zinc-700"
                            >
                                Try Again
                            </Button>
                            <Button
                                onClick={() => router.push('/settings?tab=integrations')}
                                className="bg-green-600 hover:bg-green-700"
                            >
                                Back to Settings
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
            <Card className="w-full max-w-2xl border-zinc-800 bg-zinc-900">
                <CardHeader className="text-center pb-8">
                    <div className="mx-auto mb-4 h-16 w-16 rounded-full bg-green-600 flex items-center justify-center">
                        <Github className="h-8 w-8 text-white" />
                    </div>
                    <CardTitle className="text-2xl text-zinc-100">
                        {isNew &&
                            'Complete GitHub Integration Setup'}
                        {isReconnect && 'GitHub Reconnected Successfully!'}
                        {!isNew && !isReconnect && 'Configure GitHub Integration'}
                    </CardTitle>
                    <CardDescription className="text-zinc-400 text-base">
                        {isNew && (
                            <>
                                You're almost done! Select a default repository to
                                enable automatic PR creation from incidents.
                            </>
                        )}
                        {isReconnect && (
                            <>
                                Your GitHub account has been reconnected. Please
                                select your default repository.
                            </>
                        )}
                        {!isNew && !isReconnect && (
                            <>Select your default repository for this integration.</>
                        )}
                    </CardDescription>
                </CardHeader>

                <CardContent className="space-y-6">
                    {error && (
                        <Alert className="bg-red-900/20 border-red-700">
                            <AlertDescription className="text-red-300">
                                {error}
                            </AlertDescription>
                        </Alert>
                    )}

                    <div className="space-y-3">
                        <Label
                            htmlFor="default-repo"
                            className="text-zinc-100 text-base"
                        >
                            Default Repository
                        </Label>
                        <p className="text-sm text-zinc-400">
                            This repository will be used for creating pull requests
                            from incidents by default. You can configure
                            service-specific repositories later in settings.
                        </p>
                        <Select
                            value={selectedRepo}
                            onValueChange={setSelectedRepo}
                            disabled={saving}
                        >
                            <SelectTrigger
                                id="default-repo"
                                className="bg-zinc-800 border-zinc-700 text-zinc-100 h-12"
                            >
                                <SelectValue placeholder="Select a repository..." />
                            </SelectTrigger>
                            <SelectContent className="bg-zinc-800 border-zinc-700">
                                {repositories.map((repo) => (
                                    <SelectItem
                                        key={repo.full_name}
                                        value={repo.full_name}
                                        className="text-zinc-100 focus:bg-zinc-700"
                                    >
                                        <div className="flex items-center">
                                            <span className="font-mono">
                                                {repo.full_name}
                                            </span>
                                            {repo.private && (
                                                <span className="ml-2 text-xs text-zinc-400">
                                                    🔒 Private
                                                </span>
                                            )}
                                        </div>
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    <Alert className="bg-blue-900/20 border-blue-700">
                        <AlertDescription className="text-blue-300 text-sm">
                            💡 <strong>Tip:</strong> You can configure different
                            repositories for specific services in the Settings page
                            after completing this setup.
                        </AlertDescription>
                    </Alert>

                    <div className="flex flex-col sm:flex-row gap-3 pt-4">
                        <Button
                            variant="outline"
                            onClick={() => router.push('/settings?tab=integrations')}
                            disabled={saving}
                            className="flex-1 border-zinc-700"
                        >
                            Skip for Now
                        </Button>
                        <Button
                            onClick={handleCompleteSetup}
                            disabled={!selectedRepo || saving}
                            className="flex-1 bg-green-600 hover:bg-green-700"
                        >
                            {saving ? (
                                <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    Completing Setup...
                                </>
                            ) : (
                                <>
                                    Complete Setup
                                    <ArrowRight className="h-4 w-4 ml-2" />
                                </>
                            )}
                        </Button>
                    </div>

                    {isNew && (
                        <div className="pt-4 border-t border-zinc-800">
                            <p className="text-xs text-center text-zinc-500">
                                After setup, you'll be able to create pull requests
                                directly from incident details.
                            </p>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

export default function GitHubSetupPage() {
    return (
        <Suspense
            fallback={
                <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
                    <Card className="w-full max-w-2xl border-zinc-800 bg-zinc-900">
                        <CardContent className="p-12 text-center">
                            <Loader2 className="h-12 w-12 animate-spin text-green-500 mx-auto mb-4" />
                            <p className="text-zinc-400">
                                Loading...
                            </p>
                        </CardContent>
                    </Card>
                </div>
            }
        >
            <GitHubSetupPageContent />
        </Suspense>
    );
}
