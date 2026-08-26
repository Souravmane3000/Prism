import type { Metadata, Viewport } from "next";
import { Space_Grotesk, Geist_Mono } from "next/font/google";
import "./globals.css";
import PrismBackground from "@/components/ui/PrismBackground";

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Prism — Multi-Agent Software Engineering Teammate",
  description:
    "Production-grade multi-agent pipeline: plan, navigate code, generate implementation plans, run tests, debug failures, and create professional GitHub PRs.",
};

export const viewport: Viewport = {
  themeColor: "#0A0D08",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${spaceGrotesk.variable} ${geistMono.variable} h-full`}
    >
      <body className="h-full overflow-hidden antialiased">
        {/* Ambient animated background — z-0, behind all panels */}
        <PrismBackground />
        {/* App content — z-10+ */}
        <div className="relative z-10 h-full flex flex-col">{children}</div>
      </body>
    </html>
  );
}
