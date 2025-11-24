"use client"

import { useEffect, useState, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"

interface Log {
  id: number
  timestamp: string
  level: "INFO" | "WARN" | "ERROR" | "CRITICAL"
  service: string
  message: string
}

export function LiveLogs() {
  const [logs, setLogs] = useState<Log[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Simulate incoming logs
    const interval = setInterval(() => {
      const services = ["payment-service", "auth-service", "db-shard-01", "frontend-proxy"]
      const levels: ("INFO" | "WARN" | "ERROR")[] = ["INFO", "INFO", "INFO", "WARN", "ERROR"]
      
      const newLog: Log = {
        id: Date.now(),
        timestamp: new Date().toISOString().split("T")[1].split(".")[0],
        level: levels[Math.floor(Math.random() * levels.length)],
        service: services[Math.floor(Math.random() * services.length)],
        message: `Processed request ${Math.floor(Math.random() * 10000)} in ${Math.floor(Math.random() * 200)}ms`
      }
      
      setLogs(prev => [...prev.slice(-50), newLog])
    }, 2000)

    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs])

  return (
    <Card className="col-span-4 bg-black border-zinc-800">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-mono text-zinc-400">LIVE_SYSTEM_LOGS</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[300px] overflow-y-auto font-mono text-xs" ref={scrollRef}>
          {logs.map((log) => (
            <div key={log.id} className="mb-1">
              <span className="text-zinc-500">[{log.timestamp}]</span>
              <span className={`mx-2 ${
                log.level === "ERROR" ? "text-red-500" : 
                log.level === "WARN" ? "text-yellow-500" : "text-blue-500"
              }`}>
                {log.level}
              </span>
              <span className="text-zinc-400 mr-2">[{log.service}]</span>
              <span className="text-zinc-300">{log.message}</span>
            </div>
          ))}
          {logs.length === 0 && <div className="text-zinc-600">Waiting for logs...</div>}
        </div>
      </CardContent>
    </Card>
  )
}
