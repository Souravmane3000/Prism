/**
 * components/ui/Tabs.tsx — Custom tab navigation for the right output panel.
 *
 * Fixed four tabs: Plan | Files | PR Draft | Debug
 * Active tab gets lime underline + subtle glow.
 * Disabled tabs are grayed out (shown when the relevant agent hasn't run yet).
 * All colors via CSS variables.
 */

"use client";

import type { ReactNode } from "react";

export type OutputTab = "plan" | "files" | "pr-draft" | "debug";

interface Tab {
  id: OutputTab;
  label: string;
  disabled?: boolean;
}

interface TabsProps {
  activeTab: OutputTab;
  onTabChange: (tab: OutputTab) => void;
  disabledTabs?: Set<OutputTab>;
}

const TABS: Tab[] = [
  { id: "plan", label: "Plan" },
  { id: "files", label: "Files" },
  { id: "pr-draft", label: "PR Draft" },
  { id: "debug", label: "Debug" },
];

export default function Tabs({
  activeTab,
  onTabChange,
  disabledTabs = new Set(),
}: TabsProps) {
  return (
    <div
      className="flex items-stretch border-b"
      style={{ borderColor: "var(--panel-border)" }}
      role="tablist"
      aria-label="Output sections"
    >
      {TABS.map((tab) => {
        const isActive = activeTab === tab.id;
        const isDisabled = disabledTabs.has(tab.id);

        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            aria-disabled={isDisabled}
            disabled={isDisabled}
            onClick={() => !isDisabled && onTabChange(tab.id)}
            className="relative flex-1 py-3 text-xs font-medium tracking-wide uppercase transition-all duration-200 focus-visible:outline-none min-w-0"
            style={{
              color: isActive
                ? "var(--accent-lime)"
                : isDisabled
                  ? "var(--text-muted)"
                  : "var(--text-secondary)",
              cursor: isDisabled ? "not-allowed" : "pointer",
              background: "transparent",
              border: "none",
            }}
          >
            {tab.label}
            {/* Active tab underline */}
            {isActive && (
              <span
                className="absolute bottom-0 left-0 right-0 h-px"
                style={{
                  backgroundColor: "var(--accent-lime)",
                  boxShadow: "0 0 6px var(--accent-lime)",
                }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

/** TabPanel wrapper — only renders children when the tab is active */
interface TabPanelProps {
  id: OutputTab;
  activeTab: OutputTab;
  children: ReactNode;
}

export function TabPanel({ id, activeTab, children }: TabPanelProps) {
  if (id !== activeTab) return null;
  return (
    <div role="tabpanel" aria-labelledby={`tab-${id}`} className="h-full">
      {children}
    </div>
  );
}
