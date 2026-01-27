'use client';

import { useState } from 'react';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Loader2, Check, ArrowRight, Github } from 'lucide-react';
import { generateApiKey } from '@/lib/integrations-client';
import { useRouter } from 'next/navigation';

// Helper Component defined outside render to avoid recreation
const FadeIn = ({
    children,
    delay = 0
}: {
    children: React.ReactNode;
    delay?: number;
}) => (
    <div
        className="animate-slide-up opacity-0"
        style={{
            animationDelay: `${delay}ms`
        }}
    >
        {children}
    </div>
);

export default function OnboardingPage() {
    const router = useRouter();
    const [step, setStep] = useState<'welcome' | 'org' | 'github' | 'apikey'>(
        'welcome'
    );
    const [loading, setLoading] = useState(false);

    // Org Step State
    const [orgName, setOrgName] = useState('');

    // API Key Step State
    const [apiKey, setApiKey] = useState('');
    const [copied, setCopied] = useState(false);

    const handleOrgSubmit = async (e?: React.FormEvent) => {
        e?.preventDefault();
        if (!orgName.trim()) return;
        setStep('github');
    };

    const handleGithubConnect = () => {
        setLoading(true);
        // Simulate auth flow or redirect
        setTimeout(() => {
            setLoading(false);
            setStep('apikey');
        }, 1500);
    };

    const handleGenerateKey = async () => {
        setLoading(true);
        const result = await generateApiKey(
            `${orgName || 'default'}-integration`
        );
        setLoading(false);

        if (result.apiKey) {
            setApiKey(result.apiKey);
        }
    };

    const copyToClipboard = () => {
        navigator.clipboard.writeText(apiKey);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const handleFinish = () => {
        router.push('/');
    };

    return (
        <div className="flex min-h-screen w-full bg-[#0a0a0a] text-zinc-100 font-mono selection:bg-zinc-800 selection:text-white">
            {/* Left Column - Image */}
            <div className="hidden lg:block w-1/2 relative border-r border-white/5">
                <Image
                    src="/onboarding-bg.png"
                    alt="Onboarding Atmosphere"
                    fill
                    className="object-cover opacity-90"
                    priority
                />
                <div className="absolute inset-0 bg-black/10" />
            </div>

            {/* Right Column - Interaction */}
            <div className="w-full lg:w-1/2 flex flex-col p-8 lg:p-24 relative">
                {/* Top Left Status */}
                <div className="absolute top-8 left-8 text-xs text-zinc-500 font-mono tracking-widest">
                    <p className="mt-1 opacity-50">HealOps System v1.0.0</p>
                </div>

                <div className="flex-1 flex flex-col justify-center max-w-lg mx-auto w-full">
                    {/* STEP 1: WELCOME */}
                    {step === 'welcome' && (
                        <div className="space-y-12">
                            <FadeIn>
                                <div className="space-y-4">
                                    <h1 className="text-3xl tracking-[0.2em] font-medium uppercase">
                                        HealOps Labs
                                    </h1>
                                    <p className="text-xs text-zinc-500 tracking-widest">
                                        / ヒーロップス /
                                    </p>
                                    <p className="text-xs text-zinc-500 tracking-widest">
                                        Autonomous Reliability Platform
                                    </p>
                                </div>
                            </FadeIn>

                            <FadeIn delay={200}>
                                <div className="space-y-6 text-sm leading-relaxed text-zinc-400">
                                    <p>
                                        HealOps is an API layer that provides
                                        agents with self-healing capabilities,
                                        continuously monitoring context from
                                        logs, metrics, and technical
                                        documentation.
                                    </p>
                                    <p>
                                        It can also be used with coding agents
                                        like Cursor or Windsurf.
                                    </p>
                                </div>
                            </FadeIn>

                            <FadeIn delay={400}>
                                <div className="pt-8 border-t border-zinc-800 flex flex-col gap-3">
                                    <Button
                                        variant="ghost"
                                        className="group text-zinc-300 hover:text-white hover:bg-transparent pl-0 text-xs tracking-widest justify-start"
                                        onClick={() => setStep('org')}
                                    >
                                        [ INITIALIZE SETUP ]
                                        <ArrowRight className="ml-2 h-3 w-3 transition-transform group-hover:translate-x-1" />
                                    </Button>

                                    <div className="flex gap-6">
                                        <Button
                                            variant="ghost"
                                            className="group text-zinc-500 hover:text-white hover:bg-transparent pl-0 text-[10px] tracking-widest h-auto py-0"
                                            onClick={() =>
                                                router.push('/login')
                                            }
                                        >
                                            LOGIN
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            className="group text-zinc-500 hover:text-white hover:bg-transparent pl-0 text-[10px] tracking-widest h-auto py-0"
                                            onClick={() =>
                                                router.push('/signup')
                                            }
                                        >
                                            SIGN UP
                                        </Button>
                                    </div>
                                </div>
                            </FadeIn>
                        </div>
                    )}

                    {/* STEP 2: ORG NAME */}
                    {step === 'org' && (
                        <div className="space-y-16">
                            <FadeIn>
                                <div className="text-center space-y-4">
                                    <h2 className="text-2xl tracking-[0.15em] uppercase">
                                        Your Organization
                                    </h2>
                                    <p className="text-xs text-zinc-500">
                                        Please enter your organization name
                                        below
                                    </p>
                                </div>
                            </FadeIn>

                            <FadeIn delay={200}>
                                <form
                                    onSubmit={handleOrgSubmit}
                                    className="space-y-8"
                                >
                                    <div className="relative group">
                                        <div className="absolute left-0 top-1/2 -translate-y-1/2 h-8 w-[1px] bg-zinc-700 group-focus-within:bg-white transition-colors" />
                                        <Input
                                            autoFocus
                                            value={orgName}
                                            onChange={(e) =>
                                                setOrgName(e.target.value)
                                            }
                                            className="border-0 border-b border-zinc-800 bg-transparent rounded-none px-4 py-6 text-xl text-center focus-visible:ring-0 focus-visible:border-white transition-all placeholder:text-zinc-800"
                                            placeholder="acme-corp"
                                        />
                                    </div>

                                    <div className="flex justify-center">
                                        {orgName && (
                                            <p className="text-[10px] text-yellow-600/80 tracking-widest animate-pulse">
                                                PRESS ENTER TO SUBMIT
                                            </p>
                                        )}
                                    </div>
                                </form>
                            </FadeIn>
                        </div>
                    )}

                    {/* STEP 3: GITHUB */}
                    {step === 'github' && (
                        <div className="space-y-16 text-center">
                            <FadeIn>
                                <div className="space-y-4">
                                    <h2 className="text-2xl tracking-[0.2em] uppercase">
                                        G I T H U B
                                    </h2>
                                    <div className="max-w-md mx-auto space-y-2">
                                        <p className="text-xs text-zinc-500 leading-relaxed">
                                            Authenticate to avoid GitHub rate
                                            limits when indexing repositories.
                                        </p>
                                        <p className="text-xs text-zinc-600">
                                            You don&apos;t need to grant access
                                            to all of your repositories.
                                        </p>
                                    </div>
                                </div>
                            </FadeIn>

                            <FadeIn delay={200}>
                                <div className="flex flex-col items-center gap-6">
                                    <div className="flex items-center gap-2 text-xs text-green-500/80 tracking-widest">
                                        <Check className="w-3 h-3" />
                                        <span>SYSTEM ONLINE</span>
                                    </div>

                                    <Button
                                        onClick={handleGithubConnect}
                                        disabled={loading}
                                        variant="outline"
                                        className="h-12 border-zinc-700 bg-transparent hover:bg-zinc-800 hover:text-white px-8 tracking-widest text-xs uppercase"
                                    >
                                        {loading ? (
                                            <Loader2 className="w-4 h-4 animate-spin mr-2" />
                                        ) : (
                                            <Github className="w-4 h-4 mr-2" />
                                        )}
                                        {loading
                                            ? 'CONNECTING...'
                                            : 'CONNECT GITHUB'}
                                    </Button>

                                    <Button
                                        variant="link"
                                        className="text-zinc-600 text-[10px] hover:text-zinc-400"
                                        onClick={() => setStep('apikey')}
                                    >
                                        SKIP FOR NOW
                                    </Button>
                                </div>
                            </FadeIn>
                        </div>
                    )}

                    {/* STEP 4: API KEY */}
                    {step === 'apikey' && (
                        <div className="space-y-16 text-center">
                            <FadeIn>
                                <div className="space-y-4">
                                    <h2 className="text-2xl tracking-[0.2em] uppercase">
                                        HealOps API
                                    </h2>
                                    <p className="text-xs text-zinc-500">
                                        Generate an API key to use HealOps via
                                        HTTP.
                                    </p>
                                </div>
                            </FadeIn>

                            <FadeIn delay={200}>
                                {!apiKey ? (
                                    <div className="flex justify-center">
                                        <Button
                                            onClick={handleGenerateKey}
                                            disabled={loading}
                                            variant="outline"
                                            className="h-12 border-zinc-700 bg-transparent hover:bg-zinc-800 hover:text-white px-8 tracking-widest text-xs uppercase"
                                        >
                                            {loading ? (
                                                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                                            ) : null}
                                            GENERATE KEY
                                        </Button>
                                    </div>
                                ) : (
                                    <div className="space-y-8 animate-in fade-in duration-500">
                                        <div className="space-y-2">
                                            <p className="text-[10px] text-zinc-600 uppercase tracking-widest">
                                                [ HEALOPS_API_KEY ]
                                            </p>
                                            <div
                                                className="font-mono text-sm text-zinc-300 bg-zinc-900/50 p-4 border border-zinc-800 rounded selection:bg-zinc-700 cursor-pointer hover:border-zinc-700 transition-colors break-all"
                                                onClick={copyToClipboard}
                                            >
                                                {apiKey}
                                            </div>
                                        </div>

                                        <div className="flex justify-center gap-4">
                                            <Button
                                                onClick={copyToClipboard}
                                                variant="outline"
                                                className="h-10 border-zinc-700 bg-transparent hover:bg-zinc-800 text-xs uppercase tracking-widest min-w-[120px]"
                                            >
                                                {copied ? (
                                                    <Check className="w-3 h-3 mr-2" />
                                                ) : null}
                                                {copied ? 'COPIED' : 'COPY KEY'}
                                            </Button>

                                            <Button
                                                variant="outline"
                                                className="h-10 border-zinc-700 bg-transparent hover:bg-zinc-800 text-xs uppercase tracking-widest min-w-[120px]"
                                                onClick={() =>
                                                    window.open(
                                                        'https://docs.healops.com',
                                                        '_blank'
                                                    )
                                                }
                                            >
                                                DOCS
                                            </Button>
                                        </div>

                                        <div className="pt-8 flex justify-center gap-8 text-[10px] uppercase tracking-widest text-zinc-500">
                                            <button
                                                className="hover:text-white transition-colors"
                                                onClick={() =>
                                                    setStep('welcome')
                                                }
                                            >
                                                Reset
                                            </button>
                                            <button
                                                className="hover:text-white transition-colors"
                                                onClick={handleFinish}
                                            >
                                                Continue
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </FadeIn>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
