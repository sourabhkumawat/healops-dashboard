"use client"

import { useEffect, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Loader2, ArrowLeft, CheckCircle2, AlertTriangle, AlertOctagon, Info } from "lucide-react"
import { Incident } from "@/components/incident-table"

interface LogEntry {
  id: number
  timestamp: string
  level: string
  message: string
  service_name: string
  source: string
  metadata_json: any
}

interface IncidentDetails extends Incident {
  logs: LogEntry[]
}

export default function IncidentDetailsPage() {
  const params = useParams()
  const router = useRouter()
  const [data, setData] = useState<{ incident: Incident, logs: LogEntry[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [resolving, setResolving] = useState(false)

  useEffect(() => {
    const fetchIncident = async () => {
      try {
        const response = await fetch(`http://localhost:8000/incidents/${params.id}`)
        if (response.ok) {
          const result = await response.json()
          setData(result)
        }
      } catch (error) {
        console.error("Failed to fetch incident:", error)
      } finally {
        setLoading(false)
      }
    }

    fetchIncident()
  }, [params.id])

  const handleResolve = async () => {
    setResolving(true)
    try {
      const response = await fetch(`http://localhost:8000/incidents/${params.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "RESOLVED" }),
      })
      
      if (response.ok) {
        // Refresh data
        const updatedIncident = await response.json()
        setData(prev => prev ? { ...prev, incident: updatedIncident } : null)
      }
    } catch (error) {
      console.error("Failed to resolve incident:", error)
    } finally {
      setResolving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex h-full flex-col items-center justify-center space-y-4">
        <h2 className="text-xl font-bold">Incident not found</h2>
        <Button onClick={() => router.push("/incidents")}>Back to Incidents</Button>
      </div>
    )
  }

  const { incident, logs } = data

  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <Button variant="ghost" size="icon" onClick={() => router.push("/incidents")}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h2 className="text-2xl font-bold tracking-tight">{incident.title}</h2>
            <div className="flex items-center space-x-2 mt-1">
              <Badge variant="outline">{incident.service_name}</Badge>
              <span className="text-sm text-muted-foreground">
                First seen {new Date(incident.created_at).toLocaleString()}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          <Badge 
            variant={incident.severity === "CRITICAL" ? "destructive" : "outline"}
            className="text-sm px-3 py-1"
          >
            {incident.severity}
          </Badge>
          <Badge 
            variant={incident.status === "RESOLVED" ? "default" : "secondary"}
            className="text-sm px-3 py-1"
          >
            {incident.status}
          </Badge>
          {incident.status !== "RESOLVED" && (
            <Button onClick={handleResolve} disabled={resolving}>
              {resolving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Mark as Resolved
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
        <div className="col-span-4 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Related Logs</CardTitle>
              <CardDescription>
                Recent logs associated with this incident
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px] w-full rounded-md border p-4">
                <div className="space-y-4">
                  {logs.map((log) => (
                    <div key={log.id} className="flex flex-col space-y-1 border-b pb-2 last:border-0">
                      <div className="flex items-center justify-between">
                        <span className={`text-xs font-bold ${
                          log.level === "CRITICAL" ? "text-red-500" : 
                          log.level === "ERROR" ? "text-red-400" : "text-zinc-400"
                        }`}>
                          {log.level}
                        </span>
                        <span className="text-xs text-muted-foreground font-mono">
                          {new Date(log.timestamp).toLocaleTimeString()}
                        </span>
                      </div>
                      <p className="text-sm font-mono text-zinc-300 break-all">
                        {log.message}
                      </p>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        <div className="col-span-3 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>AI Analysis</CardTitle>
              <CardDescription>Automated root cause analysis</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {incident.root_cause ? (
                <div className="rounded-lg bg-zinc-900 p-4 border border-zinc-800">
                  <p className="text-sm text-zinc-300">{incident.root_cause}</p>
                </div>
              ) : (
                <div className="flex items-center justify-center p-8 text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Analyzing incident...
                </div>
              )}
              
              {incident.action_taken && (
                <div className="rounded-lg bg-green-900/20 p-4 border border-green-900/50">
                  <div className="flex items-center mb-2">
                    <CheckCircle2 className="h-4 w-4 text-green-500 mr-2" />
                    <h4 className="font-semibold text-green-500">Action Taken</h4>
                  </div>
                  <p className="text-sm text-zinc-300">{incident.action_taken}</p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
