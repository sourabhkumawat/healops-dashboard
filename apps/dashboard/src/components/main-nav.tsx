"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"

export function MainNav({
  className,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  const pathname = usePathname()

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
