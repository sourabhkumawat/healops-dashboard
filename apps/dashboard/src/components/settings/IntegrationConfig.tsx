'use client';

import { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue
} from '@/components/ui/select';
import { Loader2, Trash2, Plus } from 'lucide-react';
import {
    addServiceMapping,
    removeServiceMapping,
    updateIntegration,
    getServices,
    getRepositories
} from '@/actions/integrations';

interface IntegrationConfigProps {
    integrationId: number;
    config: {
        default_repo?: string;
        service_mappings?: Record<string, string>;
    };
    onConfigUpdate: () => Promise<void>;
}

export function IntegrationConfig({
    integrationId,
    config,
    onConfigUpdate
}: IntegrationConfigProps) {
    const [availableServices, setAvailableServices] = useState<string[]>([]);
    const [availableRepos, setAvailableRepos] = useState<
        Array<{ full_name: string; name: string }>
    >([]);
    const [loadingServices, setLoadingServices] = useState(false);
    const [loadingRepos, setLoadingRepos] = useState(false);

    // Form states
    const [editDefaultRepo, setEditDefaultRepo] = useState<string | null>(null);
    const [updatingDefaultRepo, setUpdatingDefaultRepo] = useState(false);
    const [newServiceName, setNewServiceName] = useState('');
    const [newRepoName, setNewRepoName] = useState('');
    const [mappingLoading, setMappingLoading] = useState(false);

    const fetchingServicesRef = useRef(false);
    const fetchingReposRef = useRef(false);

    useEffect(() => {
        const loadData = async () => {
            // Fetch services
            if (!fetchingServicesRef.current) {
                fetchingServicesRef.current = true;
                setLoadingServices(true);
                try {
                    const servicesData = await getServices();
                    if (
                        servicesData.services &&
                        Array.isArray(servicesData.services)
                    ) {
                        setAvailableServices(servicesData.services);
                    }
                } catch (error) {
                    console.error('Error loading services:', error);
                } finally {
                    setLoadingServices(false);
                    fetchingServicesRef.current = false;
                }
            }

            // Fetch repos
            if (!fetchingReposRef.current) {
                fetchingReposRef.current = true;
                setLoadingRepos(true);
                try {
                    const reposData = await getRepositories(integrationId);
                    if (
                        reposData.repositories &&
                        Array.isArray(reposData.repositories)
                    ) {
                        setAvailableRepos(reposData.repositories);
                    }
                } catch (error) {
                    console.error('Error loading repositories:', error);
                } finally {
                    setLoadingRepos(false);
                    fetchingReposRef.current = false;
                }
            }
        };
        loadData();
    }, [integrationId]);

    const handleUpdateDefaultRepo = async () => {
        if (!editDefaultRepo) return;

        setUpdatingDefaultRepo(true);
        try {
            const result = await updateIntegration(integrationId, {
                default_repo: editDefaultRepo
            });
            if (!result.error) {
                await onConfigUpdate();
                setEditDefaultRepo(null);
            }
        } catch (error) {
            console.error('Error updating default repository:', error);
        } finally {
            setUpdatingDefaultRepo(false);
        }
    };

    const handleAddServiceMapping = async () => {
        if (!newServiceName || !newRepoName) return;

        setMappingLoading(true);
        try {
            const result = await addServiceMapping(
                integrationId,
                newServiceName,
                newRepoName
            );
            if (!result.error) {
                await onConfigUpdate();
                setNewServiceName('');
                setNewRepoName('');
            }
        } finally {
            setMappingLoading(false);
        }
    };

    const handleRemoveServiceMapping = async (serviceName: string) => {
        setMappingLoading(true);
        try {
            const result = await removeServiceMapping(
                integrationId,
                serviceName
            );
            if (!result.error) {
                await onConfigUpdate();
            }
        } finally {
            setMappingLoading(false);
        }
    };

    return (
        <div className="mt-6 pt-6 border-t border-zinc-800 space-y-6">
            {/* Default Repository Section */}
            <div>
                <h4 className="text-sm font-semibold text-zinc-100 mb-3">
                    Default Repository
                </h4>
                <p className="text-xs text-zinc-400 mb-4">
                    The default repository used for creating pull requests from
                    incidents
                </p>
                {editDefaultRepo !== null ? (
                    <div className="flex gap-2">
                        {loadingRepos ? (
                            <div className="flex-1 flex items-center justify-center p-2 bg-zinc-800 border border-zinc-700 rounded-md">
                                <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                                <span className="ml-2 text-sm text-zinc-400">
                                    Loading repos...
                                </span>
                            </div>
                        ) : (
                            <div className="flex-1">
                                <Select
                                    value={editDefaultRepo}
                                    onValueChange={setEditDefaultRepo}
                                    disabled={updatingDefaultRepo}
                                >
                                    <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-100 disabled:opacity-50 disabled:cursor-not-allowed">
                                        <SelectValue placeholder="Select repository" />
                                    </SelectTrigger>
                                    <SelectContent className="bg-zinc-800 border-zinc-700">
                                        {availableRepos.length > 0 ? (
                                            availableRepos.map((repo) => (
                                                <SelectItem
                                                    key={repo.full_name}
                                                    value={repo.full_name}
                                                    className="text-zinc-100 focus:bg-zinc-700"
                                                >
                                                    {repo.full_name}
                                                </SelectItem>
                                            ))
                                        ) : (
                                            <SelectItem
                                                value="no-repos"
                                                disabled
                                            >
                                                No repositories found
                                            </SelectItem>
                                        )}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                        <Button
                            onClick={handleUpdateDefaultRepo}
                            disabled={updatingDefaultRepo || !editDefaultRepo}
                            className="bg-green-600 hover:bg-green-700"
                        >
                            {updatingDefaultRepo ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                'Save'
                            )}
                        </Button>
                        <Button
                            variant="outline"
                            onClick={() => setEditDefaultRepo(null)}
                            disabled={updatingDefaultRepo}
                            className="border-zinc-700"
                        >
                            Cancel
                        </Button>
                    </div>
                ) : (
                    <div className="flex items-center justify-between p-3 bg-zinc-800 rounded-lg">
                        <div className="flex items-center gap-2">
                            <span className="text-sm text-zinc-300 font-mono">
                                {config?.default_repo || 'Not set'}
                            </span>
                        </div>
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                                setEditDefaultRepo(config?.default_repo || '')
                            }
                            className="border-zinc-700 text-zinc-300 hover:bg-zinc-700"
                        >
                            Edit
                        </Button>
                    </div>
                )}
            </div>

            {/* Service Mappings Section */}
            <div>
                <h4 className="text-sm font-semibold text-zinc-100 mb-3">
                    Service to Repository Mappings
                </h4>
                <p className="text-xs text-zinc-400 mb-4">
                    Map service names to GitHub repositories for automatic PR
                    creation
                </p>

                {config.service_mappings &&
                Object.keys(config.service_mappings).length > 0 ? (
                    <div className="space-y-2 mb-4">
                        {Object.entries(config.service_mappings).map(
                            ([serviceName, repoName]) => (
                                <div
                                    key={serviceName}
                                    className="flex items-center justify-between p-3 bg-zinc-800 rounded-lg"
                                >
                                    <div>
                                        <span className="text-sm font-medium text-zinc-100">
                                            {serviceName}
                                        </span>
                                        <span className="text-xs text-zinc-400 mx-2">
                                            →
                                        </span>
                                        <span className="text-sm text-zinc-300 font-mono">
                                            {repoName as string}
                                        </span>
                                    </div>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        onClick={() =>
                                            handleRemoveServiceMapping(
                                                serviceName
                                            )
                                        }
                                        disabled={mappingLoading}
                                        className="text-zinc-400 hover:text-red-500 h-8 w-8"
                                    >
                                        {mappingLoading ? (
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                        ) : (
                                            <Trash2 className="h-4 w-4" />
                                        )}
                                    </Button>
                                </div>
                            )
                        )}
                    </div>
                ) : (
                    <p className="text-sm text-zinc-500 mb-4">
                        No service mappings configured
                    </p>
                )}

                <div className="flex gap-2">
                    {loadingServices ? (
                        <div className="flex-1 flex items-center justify-center p-2 bg-zinc-800 border border-zinc-700 rounded-md">
                            <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                            <span className="ml-2 text-sm text-zinc-400">
                                Loading services...
                            </span>
                        </div>
                    ) : (
                        <Select
                            value={newServiceName}
                            onValueChange={setNewServiceName}
                            disabled={availableServices.length === 0}
                        >
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-100 flex-1 disabled:opacity-50 disabled:cursor-not-allowed">
                                <SelectValue
                                    placeholder={
                                        availableServices.length === 0
                                            ? 'No services available'
                                            : 'Select service name'
                                    }
                                />
                            </SelectTrigger>
                            <SelectContent className="bg-zinc-800 border-zinc-700">
                                {availableServices.length > 0 ? (
                                    availableServices.map((service) => (
                                        <SelectItem
                                            key={service}
                                            value={service}
                                            className="text-zinc-100 focus:bg-zinc-700"
                                        >
                                            {service}
                                        </SelectItem>
                                    ))
                                ) : (
                                    <SelectItem value="no-services" disabled>
                                        No services found
                                    </SelectItem>
                                )}
                            </SelectContent>
                        </Select>
                    )}
                    {loadingRepos ? (
                        <div className="flex-1 flex items-center justify-center p-2 bg-zinc-800 border border-zinc-700 rounded-md">
                            <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                            <span className="ml-2 text-sm text-zinc-400">
                                Loading repos...
                            </span>
                        </div>
                    ) : (
                        <Select
                            value={newRepoName}
                            onValueChange={setNewRepoName}
                            disabled={
                                !availableRepos || availableRepos.length === 0
                            }
                        >
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-100 flex-1 disabled:opacity-50 disabled:cursor-not-allowed">
                                <SelectValue
                                    placeholder={
                                        !availableRepos ||
                                        availableRepos.length === 0
                                            ? 'No repositories available'
                                            : 'Select repository'
                                    }
                                />
                            </SelectTrigger>
                            <SelectContent className="bg-zinc-800 border-zinc-700">
                                {availableRepos.length > 0 ? (
                                    availableRepos.map((repo) => (
                                        <SelectItem
                                            key={repo.full_name}
                                            value={repo.full_name}
                                            className="text-zinc-100 focus:bg-zinc-700"
                                        >
                                            {repo.full_name}
                                        </SelectItem>
                                    ))
                                ) : (
                                    <SelectItem value="no-repos" disabled>
                                        No repositories found
                                    </SelectItem>
                                )}
                            </SelectContent>
                        </Select>
                    )}
                    <Button
                        onClick={handleAddServiceMapping}
                        disabled={
                            !newServiceName || !newRepoName || mappingLoading
                        }
                        className="bg-green-600 hover:bg-green-700"
                    >
                        {mappingLoading ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Plus className="h-4 w-4" />
                        )}
                    </Button>
                </div>
            </div>
        </div>
    );
}
