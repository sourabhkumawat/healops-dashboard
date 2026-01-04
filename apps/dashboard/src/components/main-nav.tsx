"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { useFlags } from "launchdarkly-react-client-sdk"

export function MainNav({
  className,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  const pathname = usePathname()
  const flags = useFlags()
  
  // Feature flag to control logs tab visibility
  // This flag should be OFF by default (not available to users)
  const showLogsTab = flags['show-logs-tab'] ?? false

  return (
    <nav
      className={cn("flex items-center space-x-4 lg:space-x-6", className)}
      {...props}
    >
      <Link
        href="/"
        className={cn(
          "text-sm font-medium transition-colors hover:text-primary",
          pathname === "/" ? "text-primary" : "text-muted-foreground"
        )}
      >
        Overview
      </Link>
      <Link
        href="/incidents"
        className={cn(
          "text-sm font-medium transition-colors hover:text-primary",
          pathname?.startsWith("/incidents") ? "text-primary" : "text-muted-foreground"
        )}
      >
        Incidents
      </Link>
      {showLogsTab && (
        <Link
          href="/logs"
          className={cn(
            "text-sm font-medium transition-colors hover:text-primary",
            pathname?.startsWith("/logs") ? "text-primary" : "text-muted-foreground"
          )}
        >
          Logs
        </Link>
      )}
      <Link
        href="/settings"
        className={cn(
          "text-sm font-medium transition-colors hover:text-primary",
          pathname?.startsWith("/settings") ? "text-primary" : "text-muted-foreground"
        )}
      >
        Settings
      </Link>
    </nav>
  )
}
