"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { 
  Cloud, 
  Server, 
  Box, 
  Key, 
  Plus, 
  Trash2, 
  CheckCircle2, 
  XCircle, 
  Copy,
  ExternalLink,
  Loader2
} from "lucide-react"
import { generateApiKey, getAWSDeployUrl, getK8sManifest, getAgentInstallCommand } from "@/actions/integrations"

type Integration = {
  id: number
  provider: string
  name: string
  status: string
  created_at: string
  last_verified: string | null
}

type ApiKey = {
  id: number
  name: string
  key_prefix: string
  created_at: string
  last_used: string | null
  is_active: boolean
}

export default function SettingsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([])
  const [showAddIntegration, setShowAddIntegration] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  const [newApiKey, setNewApiKey] = useState("")
  const [loading, setLoading] = useState(false)

  const providers = [
    { id: "gcp", name: "Google Cloud", icon: Cloud, color: "text-blue-500" },
    { id: "aws", name: "AWS", icon: Cloud, color: "text-orange-500" },
    { id: "k8s", name: "Kubernetes", icon: Box, color: "text-cyan-500" },
    { id: "agent", name: "VM / Agent", icon: Server, color: "text-purple-500" }
  ]

  const handleGenerateKey = async () => {
    setLoading(true)
    const result = await generateApiKey(`${selectedProvider}-integration-${Date.now()}`)
    setLoading(false)
    
    if (result.apiKey) {
      setNewApiKey(result.apiKey)
      // Refresh API keys list
      fetchApiKeys()
    }
  }

  const fetchApiKeys = async () => {
    // TODO: Implement API call to fetch keys
    // For now, using mock data
    setApiKeys([
      {
        id: 1,
        name: "aws-integration",
        key_prefix: "healops_live",
        created_at: new Date().toISOString(),
        last_used: null,
        is_active: true
      }
    ])
  }

  const fetchIntegrations = async () => {
    // TODO: Implement API call to fetch integrations
    setIntegrations([])
  }

  useEffect(() => {
    fetchApiKeys()
    fetchIntegrations()
  }, [])

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
  }

  const getStatusBadge = (status: string) => {
    const statusConfig = {
      ACTIVE: { color: "bg-green-600", label: "Active" },
      PENDING: { color: "bg-yellow-600", label: "Pending" },
      FAILED: { color: "bg-red-600", label: "Failed" },
      DISCONNECTED: { color: "bg-zinc-600", label: "Disconnected" }
    }
    const config = statusConfig[status as keyof typeof statusConfig] || statusConfig.PENDING
    return <Badge className={`${config.color} text-white`}>{config.label}</Badge>
  }

  return (
    <div className="flex h-screen bg-zinc-950">
      {/* Sidebar */}
      <div className="w-64 border-r border-zinc-800 bg-zinc-900 p-6">
        <h2 className="text-xl font-bold text-zinc-100 mb-6">Settings</h2>
        <nav className="space-y-2">
          <a href="#integrations" className="block px-4 py-2 rounded-lg bg-zinc-800 text-zinc-100">
            Integrations
          </a>
          <a href="#api-keys" className="block px-4 py-2 rounded-lg text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">
            API Keys
          </a>
        </nav>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto p-8">
        <div className="max-w-6xl mx-auto">
          <div className="mb-8">
            <h1 className="text-3xl font-bold text-zinc-100 mb-2">Integration Settings</h1>
            <p className="text-zinc-400">Manage your cloud integrations and API keys</p>
          </div>

          <Tabs defaultValue="integrations" className="space-y-6">
            <TabsList className="bg-zinc-900 border border-zinc-800">
              <TabsTrigger value="integrations" className="data-[state=active]:bg-zinc-800">
                Integrations
              </TabsTrigger>
              <TabsTrigger value="api-keys" className="data-[state=active]:bg-zinc-800">
                API Keys
              </TabsTrigger>
            </TabsList>

            {/* Integrations Tab */}
            <TabsContent value="integrations" className="space-y-4">
              <div className="flex justify-between items-center">
                <p className="text-zinc-400">
                  {integrations.length} integration{integrations.length !== 1 ? 's' : ''} connected
                </p>
                <Button
                  onClick={() => setShowAddIntegration(!showAddIntegration)}
                  className="bg-green-600 hover:bg-green-700"
                >
                  <Plus className="h-4 w-4 mr-2" />
                  Add Integration
                </Button>
              </div>

              {showAddIntegration && (
                <Card className="border-zinc-800 bg-zinc-900">
                  <CardHeader>
                    <CardTitle className="text-zinc-100">Add New Integration</CardTitle>
                    <CardDescription className="text-zinc-400">
                      Select a platform to connect
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      {providers.map((provider) => {
                        const Icon = provider.icon
                        return (
                          <Card
                            key={provider.id}
                            className={`cursor-pointer border-2 transition-all ${
                              selectedProvider === provider.id
                                ? "border-green-600 bg-zinc-800"
                                : "border-zinc-700 bg-zinc-800/50 hover:border-zinc-600"
                            }`}
                            onClick={() => setSelectedProvider(provider.id)}
                          >
                            <CardContent className="p-4 text-center">
                              <Icon className={`h-8 w-8 mx-auto mb-2 ${provider.color}`} />
                              <p className="text-sm font-medium text-zinc-100">{provider.name}</p>
                            </CardContent>
                          </Card>
                        )
                      })}
                    </div>

                    {selectedProvider && (
                      <div className="mt-6 space-y-4">
                        <Alert className="bg-zinc-800 border-zinc-700">
                          <AlertDescription className="text-zinc-300">
                            Click below to generate an API key for this integration
                          </AlertDescription>
                        </Alert>

                        <Button
                          onClick={handleGenerateKey}
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
                            <Label className="text-zinc-100">Your API Key (save it now!)</Label>
                            <div className="flex space-x-2">
                              <Input
                                value={newApiKey}
                                readOnly
                                className="bg-zinc-800 border-zinc-700 font-mono text-sm text-zinc-100"
                              />
                              <Button
                                size="icon"
                                variant="outline"
                                onClick={() => copyToClipboard(newApiKey)}
                                className="border-zinc-700"
                              >
                                <Copy className="h-4 w-4" />
                              </Button>
                            </div>
                            <Button
                              onClick={() => window.location.href = "/onboarding"}
                              className="w-full"
                              variant="outline"
                            >
                              <ExternalLink className="h-4 w-4 mr-2" />
                              Continue Setup
                            </Button>
                          </div>
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
                      <p className="text-zinc-400 mb-4">No integrations yet</p>
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
                    <Card key={integration.id} className="border-zinc-800 bg-zinc-900">
                      <CardContent className="p-6">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center space-x-4">
                            <div className="rounded-lg bg-zinc-800 p-3">
                              <Cloud className="h-6 w-6 text-green-500" />
                            </div>
                            <div>
                              <h3 className="font-semibold text-zinc-100">{integration.name}</h3>
                              <p className="text-sm text-zinc-400">{integration.provider}</p>
                            </div>
                          </div>
                          <div className="flex items-center space-x-4">
                            {getStatusBadge(integration.status)}
                            <Button size="icon" variant="ghost" className="text-zinc-400 hover:text-red-500">
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))
                )}
              </div>
            </TabsContent>

            {/* API Keys Tab */}
            <TabsContent value="api-keys" className="space-y-4">
              <div className="flex justify-between items-center">
                <p className="text-zinc-400">
                  {apiKeys.length} API key{apiKeys.length !== 1 ? 's' : ''}
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
                            <h3 className="font-semibold text-zinc-100">{key.name}</h3>
                            <p className="text-sm text-zinc-400 font-mono">{key.key_prefix}...</p>
                            <p className="text-xs text-zinc-500 mt-1">
                              Created {new Date(key.created_at).toLocaleDateString()}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center space-x-4">
                          {key.is_active ? (
                            <CheckCircle2 className="h-5 w-5 text-green-500" />
                          ) : (
                            <XCircle className="h-5 w-5 text-red-500" />
                          )}
                          <Button size="icon" variant="ghost" className="text-zinc-400 hover:text-red-500">
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
      </div>
    </div>
  )
}
