/**
 * lib/useSupabaseRealtime.ts — Custom hook for Supabase Realtime subscriptions.
 *
 * Subscribes to postgres changes on three tables filtered by run_id:
 *   - runs          → updates RunRow (status, current_agent changes)
 *   - agent_outputs → new rows for each agent start/complete event
 *   - hitl_checkpoints → HITL interrupt payloads
 *
 * Rules enforced here:
 *   - Hydrates existing runs/agent_outputs/hitl_checkpoints on subscribe
 *     (Realtime only delivers rows inserted after subscribe)
 *   - processedIds Set prevents duplicate event renders
 *   - Auto-unsubscribes on terminal run status (completed | failed | cancelled)
 *   - Auto-unsubscribes on component unmount via useEffect cleanup
 *   - Never stores github_token; only subscribes by run_id
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase";
import type {
  AgentOutputRow,
  CheckpointRow,
  RealtimeState,
  RunRow,
  RunStatus,
} from "@/lib/types";

const TERMINAL_STATUSES: Set<RunStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

export function useSupabaseRealtime(runId: string | null): RealtimeState {
  const [runStatus, setRunStatus] = useState<RunRow | null>(null);
  const [agentOutputs, setAgentOutputs] = useState<AgentOutputRow[]>([]);
  const [checkpointPayload, setCheckpointPayload] =
    useState<CheckpointRow | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const processedIdsRef = useRef<Set<string>>(new Set());
  const channelRef = useRef<ReturnType<
    ReturnType<typeof getSupabaseClient>["channel"]
  > | null>(null);

  const unsubscribe = useCallback(() => {
    if (channelRef.current) {
      const supabase = getSupabaseClient();
      supabase.removeChannel(channelRef.current);
      channelRef.current = null;
      setIsConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!runId) return;

    // Reset state for the new run
    processedIdsRef.current = new Set();
    setRunStatus(null);
    setAgentOutputs([]);
    setCheckpointPayload(null);

    const supabase = getSupabaseClient();
    const channelName = `prism:run:${runId}`;

    // Hydrate existing rows so completed / re-opened runs show pipeline cards.
    // Realtime only delivers INSERTs after subscribe.
    void (async () => {
      try {
        const [runRes, outputsRes, checkpointRes] = await Promise.all([
          supabase.from("runs").select("*").eq("id", runId).maybeSingle(),
          supabase
            .from("agent_outputs")
            .select("*")
            .eq("run_id", runId)
            .order("created_at", { ascending: true }),
          supabase
            .from("hitl_checkpoints")
            .select("*")
            .eq("run_id", runId)
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
          setAgentOutputs((prev) => {
            const byId = new Map<string, AgentOutputRow>();
            for (const row of existingOutputs) {
              if (row?.id) byId.set(row.id, row);
            }
            for (const row of prev) {
              if (row?.id) byId.set(row.id, row);
            }
            return [...byId.values()].sort((a, b) =>
              a.created_at.localeCompare(b.created_at),
            );
          });
        }

        if (checkpointRes.data) {
          const row = checkpointRes.data as CheckpointRow;
          if (row.id) processedIdsRef.current.add(row.id);
          setCheckpointPayload(row);
        }
      } catch {
        // Hydration is best-effort; live Realtime events still apply.
      }
    })();

    const channel = supabase
      .channel(channelName)
      // ── runs table: status and current_agent updates ─────────────────────
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "runs",
          filter: `id=eq.${runId}`,
        },
        (payload) => {
          const row = payload.new as RunRow;
          if (!row) return;
          setRunStatus(row);

          // Auto-unsubscribe when run reaches a terminal state
          if (TERMINAL_STATUSES.has(row.status)) {
            // Delay slightly so final events have time to arrive
            setTimeout(() => unsubscribe(), 2000);
          }
        },
      )
      // ── agent_outputs table: start/complete events per agent ─────────────
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "agent_outputs",
          filter: `run_id=eq.${runId}`,
        },
        (payload) => {
          const row = payload.new as AgentOutputRow;
          if (!row?.id) return;

          // Deduplicate: skip if we've already processed this row id
          if (processedIdsRef.current.has(row.id)) return;
          processedIdsRef.current.add(row.id);

          setAgentOutputs((prev) => [...prev, row]);
        },
      )
      // ── hitl_checkpoints table: HITL interrupt payloads ──────────────────────
      // Named hitl_checkpoints (not checkpoints) to avoid collision with
      // LangGraph's own checkpoints table (AsyncPostgresSaver).
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "hitl_checkpoints",
          filter: `run_id=eq.${runId}`,
        },
        (payload) => {
          const row = payload.new as CheckpointRow;
          if (!row?.id) return;

          if (processedIdsRef.current.has(row.id)) return;
          processedIdsRef.current.add(row.id);

          setCheckpointPayload(row);
        },
      )
      .subscribe((status) => {
        if (status === "SUBSCRIBED") {
          setIsConnected(true);
        } else if (status === "CLOSED" || status === "CHANNEL_ERROR") {
          setIsConnected(false);
        }
      });

    channelRef.current = channel;

    return () => {
      unsubscribe();
    };
  }, [runId, unsubscribe]);

  return {
    runStatus,
    agentOutputs,
    checkpointPayload,
    isConnected,
  };
}
