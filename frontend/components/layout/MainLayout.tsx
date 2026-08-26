/**
 * components/layout/MainLayout.tsx — 3-panel flex grid.
 *
 * Fixed layout:
 *   Left:   280px — Sidebar (sessions + run form)
 *   Center: flex 1 — Activity stream (live agent feed)
 *   Right:  380px — Output inspector (tabbed)
 *
 * Each panel scrolls independently. PrismBackground renders behind all panels.
 */

import type { ReactNode } from "react";

interface MainLayoutProps {
  sidebar: ReactNode;
  stream: ReactNode;
  inspector: ReactNode;
}

export default function MainLayout({
  sidebar,
  stream,
  inspector,
}: MainLayoutProps) {
  return (
    <div
      className="flex h-full overflow-hidden relative z-10"
      style={{ minHeight: 0 }}
    >
      {/* Left: Sidebar */}
      <div className="w-[280px] flex-shrink-0 h-full overflow-hidden">
        {sidebar}
      </div>

      {/* Center: Activity Stream */}
      <div
        className="flex-1 h-full overflow-y-auto min-w-0"
        style={{
          borderLeft: "1px solid var(--panel-border)",
          borderRight: "1px solid var(--panel-border)",
        }}
      >
        {stream}
      </div>

      {/* Right: Output Inspector */}
      <div className="w-[380px] flex-shrink-0 h-full overflow-hidden">
        {inspector}
      </div>
    </div>
  );
}
