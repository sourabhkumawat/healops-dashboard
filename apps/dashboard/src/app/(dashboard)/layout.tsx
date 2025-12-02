import Link from 'next/link';
import Image from 'next/image';
import { MainNav } from '@/components/main-nav';
import { UserNav } from '@/components/user-nav';
import { getCurrentUser } from '@/actions/auth';

export default async function DashboardLayout({
    children
}: Readonly<{
    children: React.ReactNode;
}>) {
    const user = await getCurrentUser();

    return (
        <div className="flex-col md:flex">
            <div className="border-b">
                <div className="flex h-16 items-center px-4">
                    <Link href="/" className="mr-6 flex items-center space-x-2">
                        <Image
                            src="/logo.png"
                            alt="HealOps Logo"
                            width={32}
                            height={32}
                            className="h-8 w-8"
                            priority
                        />
                        <h2 className="text-lg font-bold tracking-tight cursor-pointer">
                            Healops
                        </h2>
                    </Link>
                    <MainNav className="mx-6" />
                    <div className="ml-auto flex items-center space-x-4">
                        <UserNav user={user} />
                    </div>
                </div>
            </div>
            <div className="flex-1 space-y-4 p-8 pt-6">{children}</div>
        </div>
    );
}
