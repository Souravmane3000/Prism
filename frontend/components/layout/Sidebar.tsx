/**
 * components/layout/Sidebar.tsx — Left panel container.
 *
 * Holds the RunForm and past sessions list.
 * Sessions are loaded from localStorage — the GitHub PAT is NEVER included.
 * Glass panel styling with backdrop blur.
 */

"use client";

import { Clock, GitBranch } from "lucide-react";
import type { ReactNode } from "react";
import type { SessionRecord } from "@/lib/types";

interface SidebarProps {
  sessions: SessionRecord[];
  activeRunId: string | null;
  onSelectSession: (runId: string) => void;
  children: ReactNode; // RunForm
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const STATUS_COLORS: Record<string, string> = {
  running: "var(--status-running)",
  awaiting_approval: "var(--status-awaiting)",
  completed: "var(--status-complete)",
  failed: "var(--status-error)",
  cancelled: "var(--status-pending)",
};

export default function Sidebar({
  sessions,
  activeRunId,
  onSelectSession,
  children,
}: SidebarProps) {
  return (
    <div
      className="flex flex-col h-full overflow-hidden border-r"
      style={{
        borderColor: "var(--panel-border)",
        backgroundColor: "var(--bg-sidebar)",
        backdropFilter: "var(--glass-blur)",
      }}
    >
      {/* Form area */}
      <div
        className="flex-shrink-0 p-4 border-b"
        style={{ borderColor: "var(--border-dim)" }}
      >
        {children}
      </div>

      {/* Past sessions */}
      <div className="flex-1 overflow-y-auto">
        {sessions.length > 0 && (
          <div className="p-3">
            <p
              className="text-xs font-medium mb-2 uppercase tracking-wider"
              style={{ color: "var(--text-dim)" }}
            >
              Recent Runs
            </p>
            <div className="flex flex-col gap-1">
              {sessions.map((session) => {
                const isActive = session.run_id === activeRunId;
                return (
                  <button
                    key={session.run_id}
                    onClick={() => onSelectSession(session.run_id)}
                    className="w-full text-left p-2.5 rounded-lg transition-all duration-150"
                    style={{
                      backgroundColor: isActive
                        ? "var(--bg-active)"
                        : "transparent",
                      border: isActive
                        ? "1px solid var(--border-subtle)"
                        : "1px solid transparent",
                    }}
                  >
                    {/* Repo path */}
                    <div className="flex items-center gap-1.5 mb-1">
                      <GitBranch
                        size={10}
                        style={{ color: "var(--text-dim)", flexShrink: 0 }}
                      />
                      <span
                        className="text-xs truncate"
                        style={{
                          color: isActive
                            ? "var(--text-primary)"
                            : "var(--text-secondary)",
                        }}
                      >
                        {session.repo_url
                          .replace("https://github.com/", "")
                          .slice(0, 28)}
                      </span>
                    </div>

                    {/* Status + time */}
                    <div className="flex items-center justify-between">
                      <span
                        className="inline-flex items-center gap-1 text-xs"
                        style={{
                          color:
                            STATUS_COLORS[session.status] ??
                            "var(--text-dim)",
                        }}
                      >
                        <span
                          className="w-1 h-1 rounded-full inline-block"
                          style={{
                            backgroundColor:
                              STATUS_COLORS[session.status] ??
                              "var(--text-dim)",
                          }}
                        />
                        {session.status.replace("_", " ")}
                      </span>
                      <span
                        className="flex items-center gap-0.5 text-xs"
                        style={{ color: "var(--text-dim)" }}
                      >
                        <Clock size={9} />
                        {timeAgo(session.created_at)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {sessions.length === 0 && (
          <div className="p-4 mt-4 text-center">
            <p className="text-sm" style={{ color: "var(--text-dim)" }}>
              No runs yet. Start one above.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
