"use client"

import { useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Cloud, Server, Box, CheckCircle2, Copy, ExternalLink, Loader2 } from "lucide-react"
import { generateApiKey, getAgentInstallCommand } from "@/actions/integrations"

type Provider = "agent" | null

export default function OnboardingPage() {
  const [step, setStep] = useState(1)
  const [selectedProvider, setSelectedProvider] = useState<Provider>(null)
  const [apiKey, setApiKey] = useState("")
  const [loading, setLoading] = useState(false)
  const [deployUrl, setDeployUrl] = useState("")
  const [manifest, setManifest] = useState("")
  const [installCommand, setInstallCommand] = useState("")
  const [copied, setCopied] = useState(false)

  const handleGenerateApiKey = async () => {
    setLoading(true)
    const result = await generateApiKey(`${selectedProvider}-integration`)
    setLoading(false)
    
    if (result.apiKey) {
      setApiKey(result.apiKey)
      setStep(3)
    }
  }

  const handleProviderSetup = async () => {
    setLoading(true)
    
    if (selectedProvider === "agent") {
      const result = await getAgentInstallCommand(apiKey)
      if (result.linux) {
        setInstallCommand(result.linux)
      }
    }
    
    setLoading(false)
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const providers = [
    {
      id: "agent",
      name: "VM / On-Prem",
      icon: Server,
      description: "Universal agent",
      time: "~5 seconds"
    }
  ]

  return (
    <div className="flex h-screen w-full items-center justify-center bg-zinc-950 p-4">
      <Card className="w-full max-w-4xl border-zinc-800 bg-zinc-900 text-zinc-100">
        <CardHeader>
          <CardTitle className="text-2xl">Connect Your Infrastructure</CardTitle>
          <CardDescription className="text-zinc-400">
            Choose a platform to start monitoring in under 30 seconds
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* Step 1: Choose Provider */}
          {step === 1 && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
                      onClick={() => setSelectedProvider(provider.id as Provider)}
                    >
                      <CardContent className="p-6">
                        <div className="flex items-start space-x-4">
                          <div className="rounded-lg bg-green-600/10 p-3">
                            <Icon className="h-6 w-6 text-green-500" />
                          </div>
                          <div className="flex-1">
                            <h3 className="font-semibold text-lg">{provider.name}</h3>
                            <p className="text-sm text-zinc-400 mt-1">{provider.description}</p>
                            <p className="text-xs text-green-500 mt-2">Setup time: {provider.time}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  )
                })}
              </div>
              
              <Button
                className="w-full bg-green-600 hover:bg-green-700"
                disabled={!selectedProvider}
                onClick={() => setStep(2)}
              >
                Continue
              </Button>
            </div>
          )}

          {/* Step 2: Generate API Key */}
          {step === 2 && (
            <div className="space-y-4">
              <Alert className="bg-zinc-800 border-zinc-700">
                <AlertDescription className="text-zinc-300">
                  We'll generate a secure API key for this integration. Keep it safe!
                </AlertDescription>
              </Alert>

              <div className="flex items-center justify-between p-6 bg-zinc-800 rounded-lg border border-zinc-700">
                <div>
                  <h3 className="font-semibold">Generate API Key</h3>
                  <p className="text-sm text-zinc-400 mt-1">
                    This key will be used to authenticate log ingestion
                  </p>
                </div>
                <Button
                  onClick={handleGenerateApiKey}
                  disabled={loading}
                  className="bg-green-600 hover:bg-green-700"
                >
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Generate Key"}
                </Button>
              </div>

              <Button
                variant="outline"
                className="w-full"
                onClick={() => setStep(1)}
              >
                Back
              </Button>
            </div>
          )}

          {/* Step 3: Setup Instructions */}
          {step === 3 && (
            <div className="space-y-4">
              {/* Show API Key */}
              <Alert className="bg-green-900/20 border-green-900">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <AlertDescription className="text-green-200">
                  API Key generated successfully! Copy it now - you won't see it again.
                </AlertDescription>
              </Alert>

              <div className="space-y-2">
                <Label>Your API Key</Label>
                <div className="flex space-x-2">
                  <Input
                    value={apiKey}
                    readOnly
                    className="bg-zinc-800 border-zinc-700 font-mono text-sm"
                  />
                  <Button
                    size="icon"
                    variant="outline"
                    onClick={() => copyToClipboard(apiKey)}
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {/* Provider-specific instructions */}


              {selectedProvider === "agent" && (
                <div className="space-y-4 mt-6">
                  <h3 className="font-semibold text-lg">Agent Installation</h3>
                  <p className="text-sm text-zinc-400">
                    Run this command on your VM or bare metal server:
                  </p>
                  
                  <Button
                    onClick={handleProviderSetup}
                    disabled={loading}
                    className="mb-4"
                  >
                    {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                    Generate Install Command
                  </Button>

                  {installCommand && (
                    <div className="relative">
                      <pre className="bg-zinc-950 p-4 rounded-lg border border-zinc-800 overflow-x-auto text-sm">
                        <code className="text-green-400">{installCommand}</code>
                      </pre>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="absolute top-2 right-2"
                        onClick={() => copyToClipboard(installCommand)}
                      >
                        <Copy className="h-4 w-4" />
                      </Button>
                    </div>
                  )}
                </div>
              )}



              <div className="flex space-x-2 mt-6">
                <Button
                  variant="outline"
                  className="flex-1"
                  onClick={() => {
                    setStep(1)
                    setSelectedProvider(null)
                    setApiKey("")
                  }}
                >
                  Start Over
                </Button>
                <Button
                  className="flex-1 bg-green-600 hover:bg-green-700"
                  onClick={() => window.location.href = "/"}
                >
                  Go to Dashboard
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
