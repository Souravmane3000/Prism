/**
 * lib/useSupabaseRealtime.ts — Live run updates via Supabase Realtime, with REST poll fallback.
 *
 * Subscribes to postgres changes on three tables filtered by run_id:
 *   - runs          → updates RunRow (status, current_agent changes)
 *   - agent_outputs → new rows for each agent start/complete event
 *   - hitl_checkpoints → HITL interrupt payloads
 *
 * REST polling always runs as a backup. Realtime UPDATE events on `runs` are
 * easy to miss (replica identity / RLS), which previously left HITL 1 looking
 * stuck without an Approve card.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getRunOutput, getRunStatus } from "@/lib/api";
import { getSupabaseClient } from "@/lib/supabase";
import type {
  AgentOutputRow,
  CheckpointRow,
  RealtimeState,
  RunOutputResponse,
  RunRow,
  RunStatus,
} from "@/lib/types";

const TERMINAL_STATUSES: Set<RunStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

const POLL_MS = 2500;

type RealtimeChannel = ReturnType<
  NonNullable<ReturnType<typeof getSupabaseClient>>["channel"]
>;

function statusToRunRow(
  runId: string,
  status: Awaited<ReturnType<typeof getRunStatus>>,
): RunRow {
  return {
    id: status.run_id,
    repo_url: "",
    issue_url: null,
    status: status.status,
    current_agent: status.current_agent,
    error: status.error,
    all_tests_passed: status.all_tests_passed,
    pr_url: null,
    created_at: "",
    updated_at: status.updated_at ?? "",
  };
}

function mergeAgentOutputs(
  existing: AgentOutputRow[],
  incoming: AgentOutputRow[],
): AgentOutputRow[] {
  const byPhase = new Map<string, AgentOutputRow>();
  for (const row of [...existing, ...incoming]) {
    if (!row?.agent) continue;
    const key = `${row.agent}:${row.phase}`;
    const prev = byPhase.get(key);
    const rowIsSynth = (row.id ?? "").includes("-synth-");
    const prevIsSynth = (prev?.id ?? "").includes("-synth-");
    if (!prev || (prevIsSynth && !rowIsSynth)) {
      byPhase.set(key, row);
    }
  }
  return [...byPhase.values()].sort((a, b) =>
    (a.created_at ?? "").localeCompare(b.created_at ?? ""),
  );
}

function hasTestResults(
  raw: RunOutputResponse["test_results"],
): boolean {
  if (!raw || typeof raw !== "object") return false;
  return Boolean(
    raw.framework ||
      (raw.passed_count ?? 0) > 0 ||
      (raw.failed_count ?? 0) > 0 ||
      (Array.isArray(raw.failed) && raw.failed.length > 0),
  );
}

function synthesizeAgentOutputs(output: RunOutputResponse): AgentOutputRow[] {
  const rows: AgentOutputRow[] = [];
  const created_at = new Date().toISOString();

  if (output.subtasks?.length) {
    rows.push({
      id: `${output.run_id}-synth-planner`,
      run_id: output.run_id,
      agent: "planner",
      phase: "complete",
      payload: { subtasks: output.subtasks },
      created_at,
    });
  }
  if (output.file_map && Object.keys(output.file_map).length > 0) {
    rows.push({
      id: `${output.run_id}-synth-navigator`,
      run_id: output.run_id,
      agent: "code_navigator",
      phase: "complete",
      payload: { file_map: output.file_map },
      created_at,
    });
  }
  if (output.implementation_plan?.length) {
    rows.push({
      id: `${output.run_id}-synth-impl`,
      run_id: output.run_id,
      agent: "impl_planner",
      phase: "complete",
      payload: { implementation_plan: output.implementation_plan },
      created_at,
    });
  }
  if (hasTestResults(output.test_results)) {
    rows.push({
      id: `${output.run_id}-synth-tests`,
      run_id: output.run_id,
      agent: "test_runner",
      phase: "complete",
      payload: { test_results: output.test_results },
      created_at,
    });
  }
  if (output.debug_report) {
    rows.push({
      id: `${output.run_id}-synth-debug`,
      run_id: output.run_id,
      agent: "debugger",
      phase: "complete",
      payload: { debug_report: output.debug_report },
      created_at,
    });
  }
  if (output.pr_draft) {
    rows.push({
      id: `${output.run_id}-synth-pr`,
      run_id: output.run_id,
      agent: "pr_summarizer",
      phase: "complete",
      payload: { pr_draft: output.pr_draft },
      created_at,
    });
  }
  return rows;
}

function checkpointFromOutput(
  runId: string,
  status: Awaited<ReturnType<typeof getRunStatus>>,
  output: RunOutputResponse,
): CheckpointRow | null {
  if (status.status !== "awaiting_approval") return null;
  const name =
    status.current_agent === "hitl_2" ? "hitl_2" : "hitl_1";
  return {
    id: `${runId}-synth-${name}`,
    run_id: runId,
    checkpoint_name: name,
    payload: {
      subtasks: output.subtasks,
      implementation_plan: output.implementation_plan,
    },
    user_decision: null,
    created_at: new Date().toISOString(),
    resolved_at: null,
  };
}

export function useSupabaseRealtime(runId: string | null): RealtimeState {
  const [runStatus, setRunStatus] = useState<RunRow | null>(null);
  const [agentOutputs, setAgentOutputs] = useState<AgentOutputRow[]>([]);
  const [checkpointPayload, setCheckpointPayload] =
    useState<CheckpointRow | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [transport, setTransport] = useState<RealtimeState["transport"]>("none");

  const processedIdsRef = useRef<Set<string>>(new Set());
  const channelRef = useRef<RealtimeChannel | null>(null);

  const unsubscribe = useCallback(() => {
    if (channelRef.current) {
      const supabase = getSupabaseClient();
      supabase?.removeChannel(channelRef.current);
      channelRef.current = null;
      setIsConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!runId) {
      setTransport("none");
      return;
    }

    const id = runId;

    processedIdsRef.current = new Set();
    setRunStatus(null);
    setAgentOutputs([]);
    setCheckpointPayload(null);

    const supabase = getSupabaseClient();

    async function pollOnce(): Promise<void> {
      try {
        const [status, output] = await Promise.all([
          getRunStatus(id),
          getRunOutput(id),
        ]);
        setRunStatus(statusToRunRow(id, status));
        setAgentOutputs((prev) =>
          mergeAgentOutputs(prev, synthesizeAgentOutputs(output)),
        );
        const fromOutput = checkpointFromOutput(id, status, output);
        if (fromOutput) {
          setCheckpointPayload(fromOutput);
        } else if (status.status !== "awaiting_approval") {
          setCheckpointPayload((prev) => {
            if (!prev || prev.user_decision != null) return prev;
            return {
              ...prev,
              user_decision: { action: "approve" },
              resolved_at: prev.resolved_at ?? new Date().toISOString(),
            };
          });
        }
      } catch {
        // Polling is best-effort; the next tick retries.
      }
    }

    const pollTimer = window.setInterval(() => {
      void pollOnce();
    }, POLL_MS);
    void pollOnce();

    if (!supabase) {
      setTransport("poll");
      return () => window.clearInterval(pollTimer);
    }

    setTransport("realtime");
    const channelName = `prism:run:${id}`;

    void (async () => {
      try {
        const [runRes, outputsRes, checkpointRes] = await Promise.all([
          supabase.from("runs").select("*").eq("id", id).maybeSingle(),
          supabase
            .from("agent_outputs")
            .select("*")
            .eq("run_id", id)
            .order("created_at", { ascending: true }),
          supabase
            .from("hitl_checkpoints")
            .select("*")
            .eq("run_id", id)
            .order("created_at", { ascending: false })
            .limit(1)
            .maybeSingle(),
        ]);

        if (runRes.data) {
          setRunStatus(runRes.data as RunRow);
        }

        const existingOutputs = (outputsRes.data ?? []) as AgentOutputRow[];
        if (existingOutputs.length > 0) {
          for (const row of existingOutputs) {
            if (row?.id) processedIdsRef.current.add(row.id);
          }
          setAgentOutputs((prev) => mergeAgentOutputs(existingOutputs, prev));
        }

        if (checkpointRes.data) {
          const row = checkpointRes.data as CheckpointRow;
          if (row.id) processedIdsRef.current.add(row.id);
          setCheckpointPayload(row);
        }
      } catch {
        // Initial hydrate failed; polling still updates status.
      }
    })();

    try {
      const channel = supabase
        .channel(channelName)
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "runs",
            filter: `id=eq.${id}`,
          },
          (payload) => {
            const row = payload.new as RunRow;
            if (!row) return;
            setRunStatus(row);

            if (TERMINAL_STATUSES.has(row.status)) {
              setTimeout(() => unsubscribe(), 2000);
            }
          },
        )
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "agent_outputs",
            filter: `run_id=eq.${id}`,
          },
          (payload) => {
            const row = payload.new as AgentOutputRow;
            if (!row?.id) return;

            if (processedIdsRef.current.has(row.id)) return;
            processedIdsRef.current.add(row.id);

            setAgentOutputs((prev) => mergeAgentOutputs(prev, [row]));
          },
        )
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "hitl_checkpoints",
            filter: `run_id=eq.${id}`,
          },
          (payload) => {
            const row = payload.new as CheckpointRow;
            if (!row?.id) return;

            processedIdsRef.current.add(row.id);
            setCheckpointPayload(row);
          },
        )
        .subscribe((status) => {
          if (status === "SUBSCRIBED") {
            setIsConnected(true);
            setTransport("realtime");
          } else if (status === "CLOSED" || status === "CHANNEL_ERROR") {
            setIsConnected(false);
          }
        });

      channelRef.current = channel;
    } catch {
      setTransport("poll");
    }

    return () => {
      window.clearInterval(pollTimer);
      unsubscribe();
    };
  }, [runId, unsubscribe]);

  return {
    runStatus,
    agentOutputs,
    checkpointPayload,
    isConnected,
    transport,
  };
}
