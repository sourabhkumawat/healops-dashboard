import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { LaunchDarklyProvider } from "@/components/launchdarkly-provider";


const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Healops - Self-Healing SaaS",
  description: "Autonomous reliability platform",
  icons: {
    icon: '/favicon.ico',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={inter.className} suppressHydrationWarning>
        <LaunchDarklyProvider>
          {children}
        </LaunchDarklyProvider>
      </body>
    </html>
  );
}
