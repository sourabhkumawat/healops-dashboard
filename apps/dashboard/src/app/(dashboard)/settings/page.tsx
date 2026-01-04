'use client';

import { useState, useEffect, useRef } from 'react';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle
} from '@healops/ui';
import { Button } from '@healops/ui';
import { Input } from '@healops/ui';
import { Label } from '@healops/ui';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@healops/ui';
import { Badge } from '@healops/ui';
import { Alert, AlertDescription } from '@healops/ui';
import {
    Cloud,
    Box,
    Key,
    Plus,
    Trash2,
    CheckCircle2,
    XCircle,
    Copy,
    ExternalLink,
    Loader2
} from 'lucide-react';
import {
    generateApiKey,
    connectGithub,
    listApiKeys,
    getIntegrationConfig,
    addServiceMapping,
    removeServiceMapping,
    getServices,
    getRepositories,
    listIntegrations,
    updateIntegration,
    initiateGitHubOAuth,
    reconnectGitHubIntegration
} from '@/actions/integrations';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue
} from '@healops/ui';

type Integration = {
    id: number;
    provider: string;
    name: string;
    status: string;
    created_at: string;
    last_verified: string | null;
    project_id?: string | null;
};

type ApiKey = {
    id: number;
    name: string;
    key_prefix: string;
    created_at: string;
    last_used: string | null;
    is_active: boolean;
};

import {
    getCurrentUser,
    updateUserProfile,
    type CurrentUser
} from '@/actions/auth';

export default function SettingsPage() {
    const [integrations, setIntegrations] = useState<Integration[]>([]);
    const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
    const [showAddIntegration, setShowAddIntegration] = useState(false);
    const [selectedProvider, setSelectedProvider] = useState<string | null>(
        null
    );
    const [newApiKey, setNewApiKey] = useState('');
    const [loading, setLoading] = useState(false);
    const keyCounterRef = useRef(0);
    const [expandedIntegration, setExpandedIntegration] = useState<
        number | null
    >(null);
    const [integrationConfigs, setIntegrationConfigs] = useState<
        Record<
            number,
            { default_repo?: string; service_mappings?: Record<string, string> }
        >
    >({});
    const [newServiceName, setNewServiceName] = useState('');
    const [newRepoName, setNewRepoName] = useState('');
    const [mappingLoading, setMappingLoading] = useState<number | null>(null);
    const [availableServices, setAvailableServices] = useState<string[]>([]);
    const [availableRepos, setAvailableRepos] = useState<
        Record<number, Array<{ full_name: string; name: string }>>
    >({});
    const [loadingServices, setLoadingServices] = useState(false);
    const [loadingRepos, setLoadingRepos] = useState<Record<number, boolean>>(
        {}
    );
    const [editDefaultRepo, setEditDefaultRepo] = useState<
        Record<number, string>
    >({});
    const [updatingDefaultRepo, setUpdatingDefaultRepo] = useState<
        number | null
    >(null);

    // User profile state
    const [user, setUser] = useState<CurrentUser | null>(null);
    const [userName, setUserName] = useState('');
    const [organizationName, setOrganizationName] = useState('');
    const [savingProfile, setSavingProfile] = useState(false);
    const [loadingUser, setLoadingUser] = useState(true);
    const [profileMessage, setProfileMessage] = useState<{
        type: 'success' | 'error';
        text: string;
    } | null>(null);

    const providers = [
        {
            id: 'github',
            name: 'GitHub',
            icon: Box,
            color: 'text-white'
        }
    ];

    const handleConnect = async () => {
        if (!selectedProvider) return;

        setLoading(true);
        if (selectedProvider === 'github') {
            const result = await connectGithub(newApiKey);
            if (result.status === 'connected') {
                setShowAddIntegration(false);
                fetchIntegrations();
                setNewApiKey('');
            } else {
                // Handle error
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
                fetchApiKeys();
            }
        }
        setLoading(false);
    };

    const fetchingApiKeysRef = useRef(false);
    const fetchingIntegrationsRef = useRef(false);
    const fetchingServicesRef = useRef(false);
    const fetchingReposRef = useRef<Record<number, boolean>>({});
    const fetchingConfigRef = useRef<Record<number, boolean>>({});
    const fetchingUserRef = useRef(false);

    const fetchApiKeys = async () => {
        if (fetchingApiKeysRef.current) return; // Prevent duplicate calls
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

    const fetchIntegrations = async () => {
        if (fetchingIntegrationsRef.current) return; // Prevent duplicate calls
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

        if (githubConnected) {
            // Clear param
            window.history.replaceState({}, '', window.location.pathname);
        }

        // Fetch data on mount (only fetch once)
        const loadData = async () => {
            await Promise.all([fetchApiKeys(), fetchIntegrations()]);
        };
        loadData();
    }, []);

    // Fetch user data
    useEffect(() => {
        const fetchUser = async () => {
            if (fetchingUserRef.current) return; // Prevent duplicate calls
            fetchingUserRef.current = true;
            setLoadingUser(true);
            try {
                const currentUser = await getCurrentUser();
                if (currentUser) {
                    setUser(currentUser);
                    setUserName(currentUser.name || '');
                    setOrganizationName(currentUser.organization_name || '');
                }
            } finally {
                setLoadingUser(false);
                fetchingUserRef.current = false;
            }
        };
        fetchUser();
    }, []);

    const handleSaveProfile = async () => {
        setSavingProfile(true);
        setProfileMessage(null);

        const result = await updateUserProfile({
            name: userName,
            organization_name: organizationName
        });

        if (result.success && result.user) {
            setUser(result.user);
            setProfileMessage({
                type: 'success',
                text: 'Profile updated successfully!'
            });
            // Clear message after 3 seconds
            setTimeout(() => setProfileMessage(null), 3000);
        } else {
            setProfileMessage({
                type: 'error',
                text: result.message || 'Failed to update profile'
            });
        }

        setSavingProfile(false);
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
    };

    const fetchIntegrationConfig = async (integrationId: number) => {
        if (fetchingConfigRef.current[integrationId]) return; // Prevent duplicate calls
        fetchingConfigRef.current[integrationId] = true;
        try {
            const config = await getIntegrationConfig(integrationId);
            if (!config.error) {
                setIntegrationConfigs((prev) => ({
                    ...prev,
                    [integrationId]: config
                }));
            }
        } finally {
            fetchingConfigRef.current[integrationId] = false;
        }
    };

    const handleToggleIntegration = async (integrationId: number) => {
        if (expandedIntegration === integrationId) {
            setExpandedIntegration(null);
        } else {
            setExpandedIntegration(integrationId);
            if (!integrationConfigs[integrationId]) {
                await fetchIntegrationConfig(integrationId);
            }
            // Fetch services first (always fetch to get latest)
            if (fetchingServicesRef.current) return; // Prevent duplicate calls
            fetchingServicesRef.current = true;
            setLoadingServices(true);
            try {
                const servicesData = await getServices();
                console.log('Services data:', servicesData);
                if (
                    servicesData.services &&
                    Array.isArray(servicesData.services)
                ) {
                    setAvailableServices(servicesData.services);
                    console.log(
                        'Set available services:',
                        servicesData.services
                    );
                } else {
                    console.warn(
                        'No services found or invalid format:',
                        servicesData
                    );
                    setAvailableServices([]);
                }
            } catch (error) {
                console.error('Error loading services:', error);
                setAvailableServices([]);
            } finally {
                setLoadingServices(false);
                fetchingServicesRef.current = false;
            }

            // Fetch repositories for this integration
            if (fetchingReposRef.current[integrationId]) return; // Prevent duplicate calls
            fetchingReposRef.current[integrationId] = true;
            setLoadingRepos((prev) => ({ ...prev, [integrationId]: true }));
            try {
                const reposData = await getRepositories(integrationId);
                console.log(
                    'Repos data for integration',
                    integrationId,
                    ':',
                    reposData
                );
                if (
                    reposData.repositories &&
                    Array.isArray(reposData.repositories)
                ) {
                    setAvailableRepos((prev) => ({
                        ...prev,
                        [integrationId]: reposData.repositories
                    }));
                    console.log('Set available repos:', reposData.repositories);
                } else {
                    console.warn(
                        'No repositories found or invalid format:',
                        reposData
                    );
                    setAvailableRepos((prev) => ({
                        ...prev,
                        [integrationId]: []
                    }));
                }
            } catch (error) {
                console.error('Error loading repositories:', error);
                setAvailableRepos((prev) => ({
                    ...prev,
                    [integrationId]: []
                }));
            } finally {
                setLoadingRepos((prev) => ({
                    ...prev,
                    [integrationId]: false
                }));
                fetchingReposRef.current[integrationId] = false;
            }
        }
    };

    const handleAddServiceMapping = async (integrationId: number) => {
        if (!newServiceName || !newRepoName) return;

        setMappingLoading(integrationId);
        const result = await addServiceMapping(
            integrationId,
            newServiceName,
            newRepoName
        );
        if (!result.error) {
            await fetchIntegrationConfig(integrationId);
            setNewServiceName('');
            setNewRepoName('');
        }
        setMappingLoading(null);
    };

    const handleRemoveServiceMapping = async (
        integrationId: number,
        serviceName: string
    ) => {
        setMappingLoading(integrationId);
        const result = await removeServiceMapping(integrationId, serviceName);
        if (!result.error) {
            await fetchIntegrationConfig(integrationId);
        }
        setMappingLoading(null);
    };

    const handleUpdateDefaultRepo = async (integrationId: number) => {
        const newDefaultRepo = editDefaultRepo[integrationId];
        if (!newDefaultRepo) return;

        setUpdatingDefaultRepo(integrationId);
        try {
            const result = await updateIntegration(integrationId, {
                default_repo: newDefaultRepo
            });
            if (!result.error) {
                await fetchIntegrationConfig(integrationId);
                setEditDefaultRepo((prev) => {
                    const updated = { ...prev };
                    delete updated[integrationId];
                    return updated;
                });
            }
        } catch (error) {
            console.error('Error updating default repository:', error);
        } finally {
            setUpdatingDefaultRepo(null);
        }
    };

    const getStatusBadge = (status: string) => {
        const statusConfig = {
            ACTIVE: { color: 'bg-green-600', label: 'Active' },
            PENDING: { color: 'bg-yellow-600', label: 'Pending' },
            FAILED: { color: 'bg-red-600', label: 'Failed' },
            DISCONNECTED: { color: 'bg-zinc-600', label: 'Disconnected' }
        };
        const config =
            statusConfig[status as keyof typeof statusConfig] ||
            statusConfig.PENDING;
        return (
            <Badge className={`${config.color} text-white`}>
                {config.label}
            </Badge>
        );
    };

    return (
        <div className="space-y-6">
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-zinc-100 mb-2">
                    Integration Settings
                </h1>
                <p className="text-zinc-400">
                    Manage your cloud integrations and API keys
                </p>
            </div>

            <Tabs defaultValue="general" className="space-y-6">
                <TabsList className="bg-zinc-900 border border-zinc-800">
                    <TabsTrigger
                        value="general"
                        className="data-[state=active]:bg-zinc-800"
                    >
                        General
                    </TabsTrigger>
                    <TabsTrigger
                        value="integrations"
                        className="data-[state=active]:bg-zinc-800"
                    >
                        Integrations
                    </TabsTrigger>
                    <TabsTrigger
                        value="api-keys"
                        className="data-[state=active]:bg-zinc-800"
                    >
                        API Keys
                    </TabsTrigger>
                </TabsList>

                {/* General Tab */}
                <TabsContent value="general" className="space-y-6">
                    <Card className="border-zinc-800 bg-zinc-900">
                        <CardHeader>
                            <CardTitle className="text-zinc-100">
                                Profile Settings
                            </CardTitle>
                            <CardDescription className="text-zinc-400">
                                Update your personal information
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {loadingUser ? (
                                <div className="flex items-center justify-center py-12">
                                    <Loader2 className="h-8 w-8 animate-spin text-zinc-400" />
                                    <span className="ml-3 text-zinc-400">
                                        Loading profile...
                                    </span>
                                </div>
                            ) : (
                                <>
                                    {profileMessage && (
                                        <Alert
                                            className={
                                                profileMessage.type ===
                                                'success'
                                                    ? 'bg-green-900/20 border-green-700'
                                                    : 'bg-red-900/20 border-red-700'
                                            }
                                        >
                                            <AlertDescription
                                                className={
                                                    profileMessage.type ===
                                                    'success'
                                                        ? 'text-green-300'
                                                        : 'text-red-300'
                                                }
                                            >
                                                {profileMessage.text}
                                            </AlertDescription>
                                        </Alert>
                                    )}
                                    <div className="grid gap-2">
                                        <Label
                                            htmlFor="name"
                                            className="text-zinc-100"
                                        >
                                            Display Name
                                        </Label>
                                        <Input
                                            id="name"
                                            value={userName}
                                            onChange={(e) =>
                                                setUserName(e.target.value)
                                            }
                                            placeholder="Enter your display name"
                                            className="bg-zinc-800 border-zinc-700 text-zinc-100"
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label
                                            htmlFor="email"
                                            className="text-zinc-100"
                                        >
                                            Email Address
                                        </Label>
                                        <Input
                                            id="email"
                                            value={user?.email || ''}
                                            disabled
                                            className="bg-zinc-800 border-zinc-700 text-zinc-100 opacity-50 cursor-not-allowed"
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label
                                            htmlFor="org-name"
                                            className="text-zinc-100"
                                        >
                                            Organization Name
                                        </Label>
                                        <Input
                                            id="org-name"
                                            value={organizationName}
                                            onChange={(e) =>
                                                setOrganizationName(
                                                    e.target.value
                                                )
                                            }
                                            placeholder="Enter your organization name"
                                            className="bg-zinc-800 border-zinc-700 text-zinc-100"
                                        />
                                    </div>
                                    <Button
                                        onClick={handleSaveProfile}
                                        disabled={savingProfile}
                                        className="bg-green-600 hover:bg-green-700"
                                    >
                                        {savingProfile ? (
                                            <>
                                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                                Saving...
                                            </>
                                        ) : (
                                            'Save Changes'
                                        )}
                                    </Button>
                                </>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* Integrations Tab */}
                <TabsContent value="integrations" className="space-y-4">
                    <div className="flex justify-between items-center">
                        <div>
                            <h3 className="text-lg font-medium text-zinc-100">
                                Integrations
                            </h3>
                            <p className="text-sm text-zinc-400">
                                Connect multiple cloud providers to start
                                monitoring
                            </p>
                        </div>
                        <Button
                            onClick={() =>
                                setShowAddIntegration(!showAddIntegration)
                            }
                            className="bg-green-600 hover:bg-green-700"
                        >
                            <Plus className="h-4 w-4 mr-2" />
                            Add Integration
                        </Button>
                    </div>

                    {showAddIntegration && (
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
                                                    selectedProvider ===
                                                    provider.id
                                                        ? 'border-green-600 bg-zinc-800'
                                                        : 'border-zinc-700 bg-zinc-800/50 hover:border-zinc-600'
                                                }`}
                                                onClick={() =>
                                                    setSelectedProvider(
                                                        provider.id
                                                    )
                                                }
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
                                            <>
                                                <>
                                                    <div className="text-center space-y-4 py-4">
                                                        <p className="text-sm text-zinc-400">
                                                            Connect your GitHub
                                                            account to enable
                                                            automatic pull
                                                            request creation and
                                                            code management.
                                                        </p>
                                                        <Button
                                                            onClick={async () => {
                                                                const result =
                                                                    await initiateGitHubOAuth();
                                                                if (
                                                                    result.error
                                                                ) {
                                                                    console.error(
                                                                        'Failed to initiate OAuth:',
                                                                        result.error
                                                                    );
                                                                    alert(
                                                                        'Failed to connect GitHub. Please try again.'
                                                                    );
                                                                } else if (
                                                                    result.redirectUrl
                                                                ) {
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
                                                </>
                                            </>
                                        ) : (
                                            <>
                                                <Alert className="bg-zinc-800 border-zinc-700">
                                                    <AlertDescription className="text-zinc-300">
                                                        Click below to generate
                                                        an API key for this
                                                        integration
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
                                                            Your API Key (save
                                                            it now!)
                                                        </Label>
                                                        <div className="flex space-x-2">
                                                            <Input
                                                                value={
                                                                    newApiKey
                                                                }
                                                                readOnly
                                                                className="bg-zinc-800 border-zinc-700 font-mono text-sm text-zinc-100"
                                                            />
                                                            <Button
                                                                size="icon"
                                                                variant="outline"
                                                                onClick={() =>
                                                                    copyToClipboard(
                                                                        newApiKey
                                                                    )
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
                    )}

                    {/* Integrations List */}
                    <div className="space-y-4">
                        {integrations.length === 0 ? (
                            <Card className="border-zinc-800 bg-zinc-900">
                                <CardContent className="p-12 text-center">
                                    <Cloud className="h-12 w-12 mx-auto mb-4 text-zinc-600" />
                                    <p className="text-zinc-400 mb-4">
                                        No integrations yet
                                    </p>
                                    <Button
                                        onClick={() =>
                                            setShowAddIntegration(true)
                                        }
                                        variant="outline"
                                        className="border-zinc-700"
                                    >
                                        Add Your First Integration
                                    </Button>
                                </CardContent>
                            </Card>
                        ) : (
                            integrations.map((integration) => {
                                const isExpanded =
                                    expandedIntegration === integration.id;
                                const config =
                                    integrationConfigs[integration.id];
                                const serviceMappings =
                                    config?.service_mappings || {};
                                const isGitHub =
                                    integration.provider === 'GITHUB';

                                return (
                                    <Card
                                        key={integration.id}
                                        className="border-zinc-800 bg-zinc-900"
                                    >
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
                                                            {
                                                                integration.provider
                                                            }
                                                        </p>
                                                        {integration.project_id && (
                                                            <p className="text-xs text-zinc-500 mt-1 font-mono">
                                                                {
                                                                    integration.project_id
                                                                }
                                                            </p>
                                                        )}
                                                        {config?.default_repo && (
                                                            <p className="text-xs text-zinc-500 mt-1">
                                                                Default:{' '}
                                                                <span className="font-mono">
                                                                    {
                                                                        config.default_repo
                                                                    }
                                                                </span>
                                                            </p>
                                                        )}
                                                    </div>
                                                </div>
                                                <div className="flex items-center space-x-4">
                                                    {getStatusBadge(
                                                        integration.status
                                                    )}
                                                    {isGitHub && (
                                                        <>
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                onClick={() =>
                                                                    handleToggleIntegration(
                                                                        integration.id
                                                                    )
                                                                }
                                                                className="border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                                                            >
                                                                {isExpanded
                                                                    ? 'Hide'
                                                                    : 'Configure'}
                                                            </Button>
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                onClick={async () => {
                                                                    const result =
                                                                        await reconnectGitHubIntegration(
                                                                            integration.id
                                                                        );
                                                                    if (
                                                                        result.error
                                                                    ) {
                                                                        console.error(
                                                                            'Failed to reconnect:',
                                                                            result.error
                                                                        );
                                                                        alert(
                                                                            'Failed to reconnect GitHub. Please try again.'
                                                                        );
                                                                    } else if (
                                                                        result.redirectUrl
                                                                    ) {
                                                                        window.location.href =
                                                                            result.redirectUrl;
                                                                    }
                                                                }}
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

                                            {isExpanded && isGitHub && (
                                                <div className="mt-6 pt-6 border-t border-zinc-800 space-y-6">
                                                    {/* Default Repository Section */}
                                                    <div>
                                                        <h4 className="text-sm font-semibold text-zinc-100 mb-3">
                                                            Default Repository
                                                        </h4>
                                                        <p className="text-xs text-zinc-400 mb-4">
                                                            The default
                                                            repository used for
                                                            creating pull
                                                            requests from
                                                            incidents
                                                        </p>
                                                        {editDefaultRepo[
                                                            integration.id
                                                        ] !== undefined ? (
                                                            <div className="flex gap-2">
                                                                {loadingRepos[
                                                                    integration
                                                                        .id
                                                                ] ? (
                                                                    <div className="flex-1 flex items-center justify-center p-2 bg-zinc-800 border border-zinc-700 rounded-md">
                                                                        <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                                                                        <span className="ml-2 text-sm text-zinc-400">
                                                                            Loading
                                                                            repos...
                                                                        </span>
                                                                    </div>
                                                                ) : (
                                                                    <div className="flex-1">
                                                                        <Select
                                                                            value={
                                                                                editDefaultRepo[
                                                                                    integration
                                                                                        .id
                                                                                ]
                                                                            }
                                                                            onValueChange={(
                                                                                value
                                                                            ) => {
                                                                                setEditDefaultRepo(
                                                                                    (
                                                                                        prev
                                                                                    ) => ({
                                                                                        ...prev,
                                                                                        [integration.id]:
                                                                                            value
                                                                                    })
                                                                                );
                                                                            }}
                                                                            disabled={
                                                                                updatingDefaultRepo ===
                                                                                integration.id
                                                                            }
                                                                        >
                                                                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-100 disabled:opacity-50 disabled:cursor-not-allowed">
                                                                                <SelectValue placeholder="Select repository" />
                                                                            </SelectTrigger>
                                                                            <SelectContent className="bg-zinc-800 border-zinc-700">
                                                                                {availableRepos[
                                                                                    integration
                                                                                        .id
                                                                                ]
                                                                                    ?.length >
                                                                                0 ? (
                                                                                    availableRepos[
                                                                                        integration
                                                                                            .id
                                                                                    ].map(
                                                                                        (
                                                                                            repo
                                                                                        ) => (
                                                                                            <SelectItem
                                                                                                key={
                                                                                                    repo.full_name
                                                                                                }
                                                                                                value={
                                                                                                    repo.full_name
                                                                                                }
                                                                                                className="text-zinc-100 focus:bg-zinc-700"
                                                                                            >
                                                                                                {
                                                                                                    repo.full_name
                                                                                                }
                                                                                            </SelectItem>
                                                                                        )
                                                                                    )
                                                                                ) : (
                                                                                    <SelectItem
                                                                                        value="no-repos"
                                                                                        disabled
                                                                                    >
                                                                                        No
                                                                                        repositories
                                                                                        found
                                                                                    </SelectItem>
                                                                                )}
                                                                            </SelectContent>
                                                                        </Select>
                                                                    </div>
                                                                )}
                                                                <Button
                                                                    onClick={() =>
                                                                        handleUpdateDefaultRepo(
                                                                            integration.id
                                                                        )
                                                                    }
                                                                    disabled={
                                                                        updatingDefaultRepo ===
                                                                            integration.id ||
                                                                        !editDefaultRepo[
                                                                            integration
                                                                                .id
                                                                        ]
                                                                    }
                                                                    className="bg-green-600 hover:bg-green-700"
                                                                >
                                                                    {updatingDefaultRepo ===
                                                                    integration.id ? (
                                                                        <Loader2 className="h-4 w-4 animate-spin" />
                                                                    ) : (
                                                                        'Save'
                                                                    )}
                                                                </Button>
                                                                <Button
                                                                    variant="outline"
                                                                    onClick={() => {
                                                                        setEditDefaultRepo(
                                                                            (
                                                                                prev
                                                                            ) => {
                                                                                const updated =
                                                                                    {
                                                                                        ...prev
                                                                                    };
                                                                                delete updated[
                                                                                    integration
                                                                                        .id
                                                                                ];
                                                                                return updated;
                                                                            }
                                                                        );
                                                                    }}
                                                                    disabled={
                                                                        updatingDefaultRepo ===
                                                                        integration.id
                                                                    }
                                                                    className="border-zinc-700"
                                                                >
                                                                    Cancel
                                                                </Button>
                                                            </div>
                                                        ) : (
                                                            <div className="flex items-center justify-between p-3 bg-zinc-800 rounded-lg">
                                                                <div className="flex items-center gap-2">
                                                                    <span className="text-sm text-zinc-300 font-mono">
                                                                        {config?.default_repo ||
                                                                            'Not set'}
                                                                    </span>
                                                                </div>
                                                                <Button
                                                                    size="sm"
                                                                    variant="outline"
                                                                    onClick={() => {
                                                                        setEditDefaultRepo(
                                                                            (
                                                                                prev
                                                                            ) => ({
                                                                                ...prev,
                                                                                [integration.id]:
                                                                                    config?.default_repo ||
                                                                                    ''
                                                                            })
                                                                        );
                                                                    }}
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
                                                            Service to
                                                            Repository Mappings
                                                        </h4>
                                                        <p className="text-xs text-zinc-400 mb-4">
                                                            Map service names to
                                                            GitHub repositories
                                                            for automatic PR
                                                            creation
                                                        </p>

                                                        {Object.keys(
                                                            serviceMappings
                                                        ).length > 0 ? (
                                                            <div className="space-y-2 mb-4">
                                                                {Object.entries(
                                                                    serviceMappings
                                                                ).map(
                                                                    ([
                                                                        serviceName,
                                                                        repoName
                                                                    ]) => (
                                                                        <div
                                                                            key={
                                                                                serviceName
                                                                            }
                                                                            className="flex items-center justify-between p-3 bg-zinc-800 rounded-lg"
                                                                        >
                                                                            <div>
                                                                                <span className="text-sm font-medium text-zinc-100">
                                                                                    {
                                                                                        serviceName
                                                                                    }
                                                                                </span>
                                                                                <span className="text-xs text-zinc-400 mx-2">
                                                                                    →
                                                                                </span>
                                                                                <span className="text-sm text-zinc-300 font-mono">
                                                                                    {
                                                                                        repoName as string
                                                                                    }
                                                                                </span>
                                                                            </div>
                                                                            <Button
                                                                                size="icon"
                                                                                variant="ghost"
                                                                                onClick={() =>
                                                                                    handleRemoveServiceMapping(
                                                                                        integration.id,
                                                                                        serviceName
                                                                                    )
                                                                                }
                                                                                disabled={
                                                                                    mappingLoading ===
                                                                                    integration.id
                                                                                }
                                                                                className="text-zinc-400 hover:text-red-500 h-8 w-8"
                                                                            >
                                                                                {mappingLoading ===
                                                                                integration.id ? (
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
                                                                No service
                                                                mappings
                                                                configured
                                                            </p>
                                                        )}

                                                        <div className="flex gap-2">
                                                            {loadingServices ? (
                                                                <div className="flex-1 flex items-center justify-center p-2 bg-zinc-800 border border-zinc-700 rounded-md">
                                                                    <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                                                                    <span className="ml-2 text-sm text-zinc-400">
                                                                        Loading
                                                                        services...
                                                                    </span>
                                                                </div>
                                                            ) : (
                                                                <Select
                                                                    value={
                                                                        newServiceName
                                                                    }
                                                                    onValueChange={
                                                                        setNewServiceName
                                                                    }
                                                                    disabled={
                                                                        availableServices.length ===
                                                                        0
                                                                    }
                                                                >
                                                                    <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-100 flex-1 disabled:opacity-50 disabled:cursor-not-allowed">
                                                                        <SelectValue
                                                                            placeholder={
                                                                                availableServices.length ===
                                                                                0
                                                                                    ? 'No services available'
                                                                                    : 'Select service name'
                                                                            }
                                                                        />
                                                                    </SelectTrigger>
                                                                    <SelectContent className="bg-zinc-800 border-zinc-700">
                                                                        {availableServices.length >
                                                                        0 ? (
                                                                            availableServices.map(
                                                                                (
                                                                                    service
                                                                                ) => (
                                                                                    <SelectItem
                                                                                        key={
                                                                                            service
                                                                                        }
                                                                                        value={
                                                                                            service
                                                                                        }
                                                                                        className="text-zinc-100 focus:bg-zinc-700"
                                                                                    >
                                                                                        {
                                                                                            service
                                                                                        }
                                                                                    </SelectItem>
                                                                                )
                                                                            )
                                                                        ) : (
                                                                            <SelectItem
                                                                                value="no-services"
                                                                                disabled
                                                                            >
                                                                                No
                                                                                services
                                                                                found
                                                                            </SelectItem>
                                                                        )}
                                                                    </SelectContent>
                                                                </Select>
                                                            )}
                                                            {loadingRepos[
                                                                integration.id
                                                            ] ? (
                                                                <div className="flex-1 flex items-center justify-center p-2 bg-zinc-800 border border-zinc-700 rounded-md">
                                                                    <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
                                                                    <span className="ml-2 text-sm text-zinc-400">
                                                                        Loading
                                                                        repos...
                                                                    </span>
                                                                </div>
                                                            ) : (
                                                                <Select
                                                                    value={
                                                                        newRepoName
                                                                    }
                                                                    onValueChange={
                                                                        setNewRepoName
                                                                    }
                                                                    disabled={
                                                                        !availableRepos[
                                                                            integration
                                                                                .id
                                                                        ] ||
                                                                        availableRepos[
                                                                            integration
                                                                                .id
                                                                        ]
                                                                            .length ===
                                                                            0
                                                                    }
                                                                >
                                                                    <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-100 flex-1 disabled:opacity-50 disabled:cursor-not-allowed">
                                                                        <SelectValue
                                                                            placeholder={
                                                                                !availableRepos[
                                                                                    integration
                                                                                        .id
                                                                                ] ||
                                                                                availableRepos[
                                                                                    integration
                                                                                        .id
                                                                                ]
                                                                                    .length ===
                                                                                    0
                                                                                    ? 'No repositories available'
                                                                                    : 'Select repository'
                                                                            }
                                                                        />
                                                                    </SelectTrigger>
                                                                    <SelectContent className="bg-zinc-800 border-zinc-700">
                                                                        {availableRepos[
                                                                            integration
                                                                                .id
                                                                        ]
                                                                            ?.length >
                                                                        0 ? (
                                                                            availableRepos[
                                                                                integration
                                                                                    .id
                                                                            ].map(
                                                                                (
                                                                                    repo
                                                                                ) => (
                                                                                    <SelectItem
                                                                                        key={
                                                                                            repo.full_name
                                                                                        }
                                                                                        value={
                                                                                            repo.full_name
                                                                                        }
                                                                                        className="text-zinc-100 focus:bg-zinc-700"
                                                                                    >
                                                                                        {
                                                                                            repo.full_name
                                                                                        }
                                                                                    </SelectItem>
                                                                                )
                                                                            )
                                                                        ) : (
                                                                            <SelectItem
                                                                                value="no-repos"
                                                                                disabled
                                                                            >
                                                                                No
                                                                                repositories
                                                                                found
                                                                            </SelectItem>
                                                                        )}
                                                                    </SelectContent>
                                                                </Select>
                                                            )}
                                                            <Button
                                                                onClick={() =>
                                                                    handleAddServiceMapping(
                                                                        integration.id
                                                                    )
                                                                }
                                                                disabled={
                                                                    !newServiceName ||
                                                                    !newRepoName ||
                                                                    mappingLoading ===
                                                                        integration.id
                                                                }
                                                                className="bg-green-600 hover:bg-green-700"
                                                            >
                                                                {mappingLoading ===
                                                                integration.id ? (
                                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                                ) : (
                                                                    <Plus className="h-4 w-4" />
                                                                )}
                                                            </Button>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </CardContent>
                                    </Card>
                                );
                            })
                        )}
                    </div>
                </TabsContent>

                {/* API Keys Tab */}
                <TabsContent value="api-keys" className="space-y-4">
                    <div className="flex justify-between items-center">
                        <p className="text-zinc-400">
                            {apiKeys.length} API key
                            {apiKeys.length !== 1 ? 's' : ''}
                        </p>
                    </div>

                    <div className="space-y-4">
                        {apiKeys.map((key) => (
                            <Card
                                key={key.id}
                                className="border-zinc-800 bg-zinc-900"
                            >
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
                </TabsContent>
            </Tabs>
        </div>
    );
}
