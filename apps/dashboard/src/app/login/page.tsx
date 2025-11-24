"use client"

import { useActionState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { SubmitButton } from "@/components/submit-button"
import { loginAction } from "@/actions/auth"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { ShieldCheck, AlertCircle } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"

const initialState = {
  message: "",
}

export default function LoginPage() {
  const router = useRouter()
  const [state, formAction] = useActionState(loginAction, initialState)

  useEffect(() => {
    if (state?.success) {
      router.push(state.redirect || "/")
    }
  }, [state, router])

  return (
    <div className="flex h-screen w-full items-center justify-center bg-zinc-950">
      <Card className="w-[350px] border-zinc-800 bg-zinc-900 text-zinc-100">
        <CardHeader className="space-y-1">
          <div className="flex justify-center mb-4">
            <div className="rounded-full bg-zinc-800 p-3">
              <ShieldCheck className="h-6 w-6 text-green-500" />
            </div>
          </div>
          <CardTitle className="text-2xl text-center">Healops</CardTitle>
          <CardDescription className="text-center text-zinc-400">
            Enter your credentials to access the console
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <form action={formAction}>
            {state?.message && (
              <Alert variant="destructive" className="mb-4 bg-red-900/50 border-red-900 text-red-200">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  {state.message}
                </AlertDescription>
              </Alert>
            )}
            <div className="grid gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" placeholder="admin@healops.ai" className="bg-zinc-800 border-zinc-700" required />
            </div>
            <div className="grid gap-2 mt-4">
              <Label htmlFor="password">Password</Label>
              <Input id="password" name="password" type="password" className="bg-zinc-800 border-zinc-700" required />
            </div>
            <SubmitButton />
          </form>
        </CardContent>
        <CardFooter>
          <p className="text-xs text-center text-zinc-500 w-full">
            Protected by Healops Identity Guard
          </p>
        </CardFooter>
      </Card>
    </div>
  )
}
