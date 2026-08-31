/**
 * components/layout/Header.tsx — Top navigation bar.
 *
 * Shows: Prism wordmark (lime), active run ID chip, connection status indicator.
 * Run status dot pulses when a run is actively executing.
 */

"use client";

import { Cpu } from "lucide-react";
import type { RunStatus } from "@/lib/types";

interface HeaderProps {
  activeRunId: string | null;
  runStatus: RunStatus | null;
  isConnected: boolean;
  transport?: "realtime" | "poll" | "none";
}

function statusLabel(status: RunStatus | null): string {
  switch (status) {
    case "running":
      return "Running";
    case "awaiting_approval":
      return "Awaiting Input";
    case "completed":
      return "Complete";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return "Idle";
  }
}

export default function Header({
  activeRunId,
  runStatus,
  isConnected,
  transport = "none",
}: HeaderProps) {
  const isRunning = runStatus === "running";
  const isAwaiting = runStatus === "awaiting_approval";

  return (
    <header
      className="flex items-center justify-between px-5 h-12 border-b flex-shrink-0 relative z-20"
      style={{
        borderColor: "var(--panel-border)",
        backgroundColor: "rgba(10, 13, 8, 0.9)",
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Wordmark */}
      <div className="flex items-center gap-2">
        <Cpu
          size={16}
          style={{ color: "var(--accent-lime)" }}
          strokeWidth={1.5}
        />
        <span
          className="text-sm font-semibold tracking-widest uppercase"
          style={{ color: "var(--accent-lime)", letterSpacing: "0.15em" }}
        >
          Prism
        </span>
      </div>

      {/* Center: active run info */}
      {activeRunId && (
        <div className="flex items-center gap-2">
          {/* Animated status dot */}
          <span className="relative flex items-center justify-center w-4 h-4">
            {(isRunning || isAwaiting) && (
              <span
                className="absolute inline-flex h-full w-full rounded-full animate-status-ping"
                style={{
                  backgroundColor: isAwaiting
                    ? "var(--status-awaiting)"
                    : "var(--status-running)",
                  opacity: 0.4,
                }}
              />
            )}
            <span
              className="relative inline-flex rounded-full w-2 h-2"
              style={{
                backgroundColor:
                  runStatus === "completed"
                    ? "var(--status-complete)"
                    : runStatus === "failed"
                      ? "var(--status-error)"
                      : runStatus === "awaiting_approval"
                        ? "var(--status-awaiting)"
                        : runStatus === "running"
                          ? "var(--status-running)"
                          : "var(--status-pending)",
              }}
            />
          </span>

          <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
            {statusLabel(runStatus)}
          </span>

          <span
            className="text-xs font-mono px-1.5 py-0.5 rounded"
            style={{
              color: "var(--text-dim)",
              backgroundColor: "var(--bg-card)",
              border: "1px solid var(--border-dim)",
            }}
          >
            {activeRunId.slice(0, 8)}
          </span>
        </div>
      )}

      {/* Connection indicator */}
      <div className="flex items-center gap-1.5">
        <span
          className="w-1.5 h-1.5 rounded-full"
          style={{
            backgroundColor: isConnected || transport === "poll"
              ? "var(--status-complete)"
              : "var(--text-muted)",
          }}
        />
        <span
          className="text-xs"
          style={{ color: "var(--text-dim)" }}
        >
          {isConnected
            ? "Live"
            : transport === "poll"
              ? "Polling"
              : activeRunId
                ? "Connecting"
                : "Offline"}
        </span>
      </div>
    </header>
  );
}
