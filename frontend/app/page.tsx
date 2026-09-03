/**
 * app/page.tsx — Main Prism workspace page.
 *
 * Top-level state:
 *   - activeRunId: string | null — the currently viewed run
 *   - pat: string — GitHub PAT, ephemeral, NEVER persisted
 *   - runOutput: RunOutputResponse | null — full pipeline output
 *   - sessions: SessionRecord[] — loaded from localStorage (no PAT)
 *
 * Data flow:
 *   1. RunForm → onRunStarted(runId, pat) → sets activeRunId + pat
 *   2. useSupabaseRealtime(activeRunId) → feeds ActivityStream
 *   3. On each "complete" agent_output event → calls getRunOutput → updates runOutput
 *   4. Realtime runStatus updates → auto-refresh output on terminal states
 *   5. HITLCard receives pat for approve calls
 *   6. OutputInspector receives runOutput + pat for createPR
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Header from "@/components/layout/Header";
import MainLayout from "@/components/layout/MainLayout";
import Sidebar from "@/components/layout/Sidebar";
import RunForm from "@/components/input/RunForm";
import ActivityStream from "@/components/stream/ActivityStream";
import OutputInspector from "@/components/output/OutputInspector";
import { useSupabaseRealtime } from "@/lib/useSupabaseRealtime";
import { getRunOutput, deleteRun, ApiError } from "@/lib/api";
import { materializeRunOutput } from "@/lib/output";
import type {
  RunOutputResponse,
  RunStatus,
  SessionRecord,
} from "@/lib/types";

export default function PrismWorkspace() {
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [pat, setPat] = useState<string>(""); // ephemeral — never persisted
  const [runOutput, setRunOutput] = useState<RunOutputResponse | null>(null);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  // Checkpoints the user already decided on this run. Must persist for the
  // whole run — clearing when the next agent starts re-shows Approve/Stop.
  const [resolvedCheckpoints, setResolvedCheckpoints] = useState<Set<string>>(
    () => new Set(),
  );

  // Track last processed agent_output count to avoid duplicate fetches
  const lastOutputCountRef = useRef(0);

  // Load sessions from localStorage on mount (no PAT ever stored here)
  useEffect(() => {
    try {
      const raw = localStorage.getItem("prism_sessions");
      if (raw) {
        setSessions(JSON.parse(raw) as SessionRecord[]);
      }
    } catch {
      // localStorage unavailable — silently skip
    }
  }, []);

  // Subscribe to Supabase Realtime for the active run
  const { runStatus, agentOutputs, checkpointPayload, isConnected, transport } =
    useSupabaseRealtime(activeRunId);

  useEffect(() => {
    if (runStatus?.status === "awaiting_approval" && activeRunId) {
      getRunOutput(activeRunId)
        .then((output) => {
          setRunOutput(output);
          if (output.pr_url) setPrUrl(output.pr_url);
        })
        .catch(() => {});
    }
  }, [runStatus?.status, activeRunId]);

  // Refresh full output whenever new complete events arrive
  useEffect(() => {
    const completeCount = agentOutputs.filter(
      (o) => o.phase === "complete",
    ).length;

    if (completeCount > lastOutputCountRef.current && activeRunId) {
      lastOutputCountRef.current = completeCount;
      getRunOutput(activeRunId)
        .then((output) => {
          setRunOutput(output);
          if (output.pr_url) setPrUrl(output.pr_url);
        })
        .catch(() => {
          // Non-critical: Realtime is primary, output fetch is supplementary
        });
    }
  }, [agentOutputs, activeRunId]);

  // Also fetch on terminal run status to ensure final output is hydrated
  useEffect(() => {
    const terminalStatuses: RunStatus[] = ["completed", "failed", "cancelled"];
    if (
      runStatus?.status &&
      terminalStatuses.includes(runStatus.status) &&
      activeRunId
    ) {
      getRunOutput(activeRunId)
        .then((output) => {
          setRunOutput(output);
          if (output.pr_url) setPrUrl(output.pr_url);
        })
        .catch(() => {});

      // Update session status in localStorage
      setSessions((prev) => {
        const updated = prev.map((s) =>
          s.run_id === activeRunId
            ? { ...s, status: runStatus.status }
            : s,
        );
        try {
          localStorage.setItem("prism_sessions", JSON.stringify(updated));
        } catch {}
        return updated;
      });
    }
  }, [runStatus?.status, activeRunId]);

  const handleRunStarted = useCallback(
    (runId: string, token: string) => {
      setActiveRunId(runId);
      setPat(token);
      setRunOutput(null);
      setPrUrl(null);
      setResolvedCheckpoints(new Set());
      lastOutputCountRef.current = 0;

      // Reload sessions from localStorage (RunForm already saved it)
      try {
        const raw = localStorage.getItem("prism_sessions");
        if (raw) {
          setSessions(JSON.parse(raw) as SessionRecord[]);
        }
      } catch {}
    },
    [],
  );

  const handleSelectSession = useCallback((runId: string) => {
    setActiveRunId(runId);
    setRunOutput(null);
    setResolvedCheckpoints(new Set());
    lastOutputCountRef.current = 0;

    // Fetch latest output for selected session
    getRunOutput(runId)
      .then((output) => {
        setRunOutput(output);
        if (output.pr_url) setPrUrl(output.pr_url);
      })
      .catch(() => {});
  }, []);

  const handleApproved = useCallback((checkpoint: string) => {
    setResolvedCheckpoints((prev) => {
      const next = new Set(prev);
      next.add(checkpoint);
      return next;
    });
    
    // Also refresh output
    if (activeRunId) {
      getRunOutput(activeRunId)
        .then((output) => {
          setRunOutput(output);
          if (output.pr_url) setPrUrl(output.pr_url);
        })
        .catch(() => {});
    }
  }, [activeRunId]);

  const handleStopped = useCallback(() => {
    // Run cancelled at HITL — update local session status
    setSessions((prev) => {
      const updated = prev.map((s) =>
        s.run_id === activeRunId ? { ...s, status: "cancelled" as RunStatus } : s,
      );
      try {
        localStorage.setItem("prism_sessions", JSON.stringify(updated));
      } catch {}
      return updated;
    });
  }, [activeRunId]);

  const handleDeleteSession = useCallback(
    async (runId: string) => {
      try {
        await deleteRun(runId);
      } catch (err) {
        if (!(err instanceof ApiError && err.httpStatus === 404)) {
          throw err;
        }
      }

      setSessions((prev) => {
        const updated = prev.filter((s) => s.run_id !== runId);
        try {
          localStorage.setItem("prism_sessions", JSON.stringify(updated));
        } catch {}
        return updated;
      });

      if (activeRunId === runId) {
        setActiveRunId(null);
        setRunOutput(null);
        setPrUrl(null);
        setResolvedCheckpoints(new Set());
        lastOutputCountRef.current = 0;
      }
    },
    [activeRunId],
  );

  const currentRunStatus = runStatus?.status ?? runOutput?.status ?? null;

  const displayOutput = useMemo(() => {
    if (!activeRunId) return null;
    return materializeRunOutput(
      activeRunId,
      runStatus,
      agentOutputs,
      runOutput,
    );
  }, [activeRunId, runStatus, agentOutputs, runOutput]);

  return (
    <>
      {/* Top navigation bar */}
      <Header
        activeRunId={activeRunId}
        runStatus={currentRunStatus}
        isConnected={isConnected}
        transport={transport}
      />

      {/* 3-panel workspace */}
      <div className="flex-1 overflow-hidden">
        <MainLayout
          sidebar={
            <Sidebar
              sessions={sessions}
              activeRunId={activeRunId}
              onSelectSession={handleSelectSession}
              onDeleteSession={handleDeleteSession}
            >
              <RunForm onRunStarted={handleRunStarted} onPatChange={setPat} />
            </Sidebar>
          }
          stream={
            <ActivityStream
              runId={activeRunId}
              runStatus={runStatus}
              agentOutputs={agentOutputs}
              checkpointPayload={checkpointPayload}
              outputSubtasks={displayOutput?.subtasks ?? []}
              outputPlan={displayOutput?.implementation_plan ?? []}
              pat={pat}
              resolvedCheckpoints={resolvedCheckpoints}
              onApproved={handleApproved}
              onStopped={handleStopped}
            />
          }
          inspector={
            <OutputInspector
              runOutput={displayOutput}
              runId={activeRunId}
              runStatus={currentRunStatus}
              pat={pat}
              prUrl={prUrl}
            />
          }
        />
      </div>
    </>
  );
}
