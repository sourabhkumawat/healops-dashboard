import { MainNav } from "@/components/main-nav";
import { UserNav } from "@/components/user-nav";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex-col md:flex">
      <div className="border-b">
        <div className="flex h-16 items-center px-4">
          <h2 className="text-lg font-bold tracking-tight mr-6">Healops</h2>
          <MainNav className="mx-6" />
          <div className="ml-auto flex items-center space-x-4">

            <UserNav />
          </div>
        </div>
      </div>
      <div className="flex-1 space-y-4 p-8 pt-6">
        {children}
      </div>
    </div>
  );
}
