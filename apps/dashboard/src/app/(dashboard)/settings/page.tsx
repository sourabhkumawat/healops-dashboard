'use client';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ProfileSettings } from '@/components/settings/ProfileSettings';
import { IntegrationsTab } from '@/components/settings/IntegrationsTab';
import { ApiKeysTab } from '@/components/settings/ApiKeysTab';

export default function SettingsPage() {
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
                    <ProfileSettings />
                </TabsContent>

                {/* Integrations Tab */}
                <TabsContent value="integrations" className="space-y-4">
                    <IntegrationsTab />
                </TabsContent>

                {/* API Keys Tab */}
                <TabsContent value="api-keys" className="space-y-4">
                    <ApiKeysTab />
                </TabsContent>
            </Tabs>
        </div>
    );
}
