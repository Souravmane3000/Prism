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

import { useCallback, useEffect, useRef, useState } from "react";
import Header from "@/components/layout/Header";
import MainLayout from "@/components/layout/MainLayout";
import Sidebar from "@/components/layout/Sidebar";
import RunForm from "@/components/input/RunForm";
import ActivityStream from "@/components/stream/ActivityStream";
import OutputInspector from "@/components/output/OutputInspector";
import { useSupabaseRealtime } from "@/lib/useSupabaseRealtime";
import { getRunOutput } from "@/lib/api";
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
  // Track which checkpoint was just approved to hide that specific card (before Realtime catches up)
  // null = no recent approval, "hitl_1" or "hitl_2" = that checkpoint was just approved
  const [approvedCheckpoint, setApprovedCheckpoint] = useState<string | null>(null);

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
  const { runStatus, agentOutputs, checkpointPayload, isConnected } =
    useSupabaseRealtime(activeRunId);

  // Reset approvedCheckpoint when:
  // 1. Status changes away from awaiting_approval (pipeline resumed)
  // 2. Current agent changes to a different checkpoint (now at a new HITL)
  useEffect(() => {
    if (runStatus?.status && runStatus.status !== "awaiting_approval") {
      setApprovedCheckpoint(null);
    }
  }, [runStatus?.status]);

  // Also reset if we're now at a different checkpoint than the one we approved
  useEffect(() => {
    if (
      approvedCheckpoint &&
      runStatus?.current_agent &&
      runStatus.current_agent !== approvedCheckpoint &&
      (runStatus.current_agent === "hitl_1" || runStatus.current_agent === "hitl_2")
    ) {
      setApprovedCheckpoint(null);
    }
  }, [runStatus?.current_agent, approvedCheckpoint]);

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
      setApprovedCheckpoint(null); // Reset for new run
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
    // Immediately hide this specific HITL card to prevent double-clicks while Realtime catches up
    setApprovedCheckpoint(checkpoint);
    
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

  const currentRunStatus = runStatus?.status ?? runOutput?.status ?? null;

  return (
    <>
      {/* Top navigation bar */}
      <Header
        activeRunId={activeRunId}
        runStatus={currentRunStatus}
        isConnected={isConnected}
      />

      {/* 3-panel workspace */}
      <div className="flex-1 overflow-hidden">
        <MainLayout
          sidebar={
            <Sidebar
              sessions={sessions}
              activeRunId={activeRunId}
              onSelectSession={handleSelectSession}
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
              pat={pat}
              approvedCheckpoint={approvedCheckpoint}
              onApproved={handleApproved}
              onStopped={handleStopped}
            />
          }
          inspector={
            <OutputInspector
              runOutput={runOutput}
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
