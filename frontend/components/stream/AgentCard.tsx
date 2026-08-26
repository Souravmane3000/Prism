/**
 * components/stream/AgentCard.tsx — Individual agent step indicator.
 *
 * Shows: display name, status badge (colored dot + ping animation), phase label,
 * and elapsed time. Animates in with fadeSlideUp.
 * All colors via CSS variables.
 */

"use client";

import { CheckCircle2, XCircle, Loader2, Clock, SkipForward } from "lucide-react";
import type { AgentName, AgentPhase } from "@/lib/types";

interface AgentCardProps {
  agent: AgentName;
  phase: AgentPhase | null;
  startedAt: string | null;
  completedAt: string | null;
  isRunning: boolean;
  isSkipped?: boolean;
  isError?: boolean;
  payload: Record<string, unknown>;
}

const AGENT_LABELS: Record<AgentName, string> = {
  planner: "Planner",
  hitl_1: "HITL Checkpoint 1",
  code_navigator: "Code Navigator",
  impl_planner: "Implementation Planner",
  hitl_2: "HITL Checkpoint 2",
  test_runner: "Test Runner",
  debugger: "Debugger",
  pr_summarizer: "PR Summarizer",
};

const AGENT_DESCRIPTIONS: Record<AgentName, string> = {
  planner: "Decomposing issue into ordered subtasks",
  hitl_1: "Awaiting subtask approval",
  code_navigator: "Mapping relevant files via semantic search",
  impl_planner: "Writing step-by-step engineering plan",
  hitl_2: "Awaiting implementation plan approval",
  test_runner: "Running test suite in isolated sandbox",
  debugger: "Analyzing failures and proposing fixes",
  pr_summarizer: "Composing professional PR draft",
};

function elapsed(start: string | null, end: string | null): string {
  if (!start) return "";
  const from = new Date(start).getTime();
  const to = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((to - from) / 1000);
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

export default function AgentCard({
  agent,
  phase,
  startedAt,
  completedAt,
  isRunning,
  isSkipped = false,
  isError = false,
  payload,
}: AgentCardProps) {
  const isComplete = phase === "complete" && !isError;

  // Determine status color
  const dotColor = isError
    ? "var(--status-error)"
    : isSkipped
      ? "var(--status-skipped)"
      : isComplete
        ? "var(--status-complete)"
        : isRunning
          ? "var(--status-running)"
          : "var(--status-pending)";

  // Extract quick summary from payload for complete events
  const summary = getSummary(agent, payload);

  return (
    <div
      className="animate-fade-slide-up rounded-xl p-4 transition-all duration-200"
      style={{
        backgroundColor: "var(--bg-card)",
        backdropFilter: "var(--glass-blur)",
        border: "1px solid var(--border-subtle)",
        boxShadow: isRunning ? "var(--glow-lime-sm)" : "var(--card-shadow)",
      }}
    >
      <div className="flex items-start gap-3">
        {/* Status icon */}
        <div className="mt-0.5 flex-shrink-0 flex flex-col items-center gap-1">
          {isError ? (
            <XCircle size={16} style={{ color: "var(--status-error)" }} />
          ) : isSkipped ? (
            <SkipForward size={16} style={{ color: "var(--status-skipped)" }} />
          ) : isComplete ? (
            <CheckCircle2
              size={16}
              style={{ color: "var(--status-complete)" }}
            />
          ) : isRunning ? (
            <Loader2
              size={16}
              className="animate-spin"
              style={{ color: "var(--status-running)" }}
            />
          ) : (
            <span
              className="w-4 h-4 rounded-full border"
              style={{ borderColor: "var(--border-dim)" }}
            />
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            {/* Status dot */}
            <span className="relative flex items-center">
              {isRunning && (
                <span
                  className="absolute inline-flex h-2 w-2 rounded-full animate-status-ping"
                  style={{ backgroundColor: dotColor, opacity: 0.6 }}
                />
              )}
              <span
                className="relative inline-flex rounded-full w-2 h-2"
                style={{ backgroundColor: dotColor }}
              />
            </span>

            <span
              className="text-sm font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {AGENT_LABELS[agent]}
            </span>

            {/* Elapsed time */}
            {startedAt && (
              <span
                className="flex items-center gap-0.5 text-xs ml-auto"
                style={{ color: "var(--text-dim)" }}
              >
                <Clock size={10} />
                {elapsed(startedAt, completedAt)}
              </span>
            )}
          </div>

          {/* Description / phase label */}
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
            {isSkipped
              ? "Skipped — all tests passed"
              : isError
                ? String(payload.error ?? "An error occurred")
                : isComplete && summary
                  ? summary
                  : AGENT_DESCRIPTIONS[agent]}
          </p>
        </div>
      </div>
    </div>
  );
}

function getSummary(
  agent: AgentName,
  payload: Record<string, unknown>,
): string | null {
  switch (agent) {
    case "planner": {
      const subtasks = payload.subtasks;
      if (Array.isArray(subtasks) && subtasks.length > 0) {
        return `${subtasks.length} subtasks planned`;
      }
      return null;
    }
    case "code_navigator": {
      const fileMap = payload.file_map as Record<string, unknown[]> | undefined;
      if (fileMap) {
        const total = Object.values(fileMap).reduce(
          (acc, arr) => acc + (Array.isArray(arr) ? arr.length : 0),
          0,
        );
        return `${total} files mapped across ${Object.keys(fileMap).length} subtasks`;
      }
      return null;
    }
    case "test_runner": {
      if (typeof payload.error === "string" && payload.error.length > 0) {
        return "Test suite failed to start — see Debug";
      }
      const tr = payload.test_results as
        | {
            passed_count?: number;
            failed_count?: number;
            framework?: string;
            exit_code?: number;
            stderr?: string;
          }
        | undefined;
      if (tr) {
        if ((tr.framework ?? "").toLowerCase() === "skipped") {
          return "Skipped locally — tests run on Modal deploy";
        }
        const passed = tr.passed_count ?? 0;
        const failed = tr.failed_count ?? 0;
        if ((tr.exit_code ?? 0) !== 0 && passed === 0 && failed === 0) {
          return "Test suite did not run — see Debug";
        }
        return `${tr.framework ?? "Unknown"} — ${passed} passed, ${failed} failed`;
      }
      return null;
    }
    case "pr_summarizer": {
      const draft = payload.pr_draft as { title?: string } | undefined;
      return draft?.title ? `"${draft.title}"` : null;
    }
    default:
      return null;
  }
}
