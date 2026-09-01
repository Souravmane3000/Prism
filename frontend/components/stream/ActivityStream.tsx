/**
 * components/stream/ActivityStream.tsx — Live agent activity feed.
 *
 * Reads from useSupabaseRealtime hook (passed via props from page.tsx).
 * Renders one AgentCard per agent that has appeared in agent_outputs.
 * When run reaches a HITL status, renders HITLCard inline with the stream.
 * New cards animate in with animate-fade-slide-up.
 * Pipeline order is fixed (from GRAPH.md §8).
 */

"use client";

import { useEffect, useRef } from "react";
import AgentCard from "@/components/stream/AgentCard";
import HITLCard from "@/components/stream/HITLCard";
import {
  PIPELINE_ORDER,
  isHitlResolved,
  shouldRenderHitlCard,
} from "@/lib/hitlVisibility";
import type {
  AgentName,
  AgentOutputRow,
  CheckpointRow,
  ImplementationPlanItem,
  RunRow,
  Subtask,
} from "@/lib/types";

const PIPELINE_AGENTS = PIPELINE_ORDER.filter(
  (a) => a !== "hitl_1" && a !== "hitl_2",
);

interface ActivityStreamProps {
  runId: string | null;
  runStatus: RunRow | null;
  agentOutputs: AgentOutputRow[];
  checkpointPayload: CheckpointRow | null;
  outputSubtasks?: Subtask[];
  outputPlan?: ImplementationPlanItem[];
  pat: string;
  resolvedCheckpoints: ReadonlySet<string>;
  onApproved: (checkpoint: string) => void;
  onStopped: () => void;
}

/**
 * Coerce a raw unknown value from the Realtime payload into a valid Subtask.
 * Defends against the LLM returning slightly off-schema objects.
 */
function normSubtask(raw: unknown): Subtask {
  const r = ((raw ?? {}) as Record<string, unknown>);
  const COMPLEXITIES = ["low", "medium", "high"] as const;
  return {
    id: String(r.id ?? `st-${Math.random().toString(36).slice(2, 8)}`),
    title: typeof r.title === "string" ? r.title : String(r.title ?? "Untitled"),
    description: typeof r.description === "string" ? r.description : String(r.description ?? ""),
    dependencies: Array.isArray(r.dependencies)
      ? r.dependencies.map((d) => String(d))
      : [],
    likely_files: Array.isArray(r.likely_files)
      ? r.likely_files.map((f) => String(f))
      : [],
    complexity: COMPLEXITIES.includes(r.complexity as Subtask["complexity"])
      ? (r.complexity as Subtask["complexity"])
      : "medium",
  };
}

/** Derive a per-agent view from agent_outputs rows */
function buildAgentMap(
  outputs: AgentOutputRow[],
): Map<
  AgentName,
  { startRow?: AgentOutputRow; completeRow?: AgentOutputRow }
> {
  const map = new Map<
    AgentName,
    { startRow?: AgentOutputRow; completeRow?: AgentOutputRow }
  >();

  for (const row of outputs) {
    if (!row?.agent) continue;
    const existing = map.get(row.agent) ?? {};
    if (row.phase === "start") {
      map.set(row.agent, { ...existing, startRow: row });
    } else {
      map.set(row.agent, { ...existing, completeRow: row });
    }
  }

  return map;
}

export default function ActivityStream({
  runId,
  runStatus,
  agentOutputs,
  checkpointPayload,
  outputSubtasks = [],
  outputPlan = [],
  pat,
  resolvedCheckpoints,
  onApproved,
  onStopped,
}: ActivityStreamProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as new events arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [agentOutputs.length]);

  if (!runId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center h-full p-8">
        <div
          className="text-center max-w-xs"
          style={{ color: "var(--text-secondary)" }}
        >
          <div
            className="w-12 h-12 rounded-xl mb-4 mx-auto flex items-center justify-center"
            style={{
              border: "1px solid var(--border-subtle)",
              backgroundColor: "var(--bg-card)",
            }}
          >
            <span
              className="text-xl"
              style={{ color: "var(--accent-lime)", fontWeight: 700 }}
            >
              ◈
            </span>
          </div>
          <p className="text-base font-medium mb-1" style={{ color: "var(--text-primary)" }}>
            No active run
          </p>
          <p className="text-sm">
            Enter a repository URL and issue on the left to start the pipeline.
          </p>
        </div>
      </div>
    );
  }

  const agentMap = buildAgentMap(agentOutputs);

  // Collect agents that have seen any output (start or complete)
  const seenAgents = new Set(agentOutputs.map((r) => r.agent));
  // Also include the currently-running agent (may have only start emitted)
  if (runStatus?.current_agent) {
    seenAgents.add(runStatus.current_agent as AgentName);
  }
  if (
    shouldRenderHitlCard(
      "hitl_1",
      runStatus,
      checkpointPayload,
      resolvedCheckpoints,
      agentMap,
    ) ||
    isHitlResolved(
      "hitl_1",
      resolvedCheckpoints,
      agentMap,
      checkpointPayload,
      runStatus,
    ) ||
    agentMap.get("planner")?.completeRow
  ) {
    seenAgents.add("hitl_1");
  }
  if (
    shouldRenderHitlCard(
      "hitl_2",
      runStatus,
      checkpointPayload,
      resolvedCheckpoints,
      agentMap,
    ) ||
    isHitlResolved(
      "hitl_2",
      resolvedCheckpoints,
      agentMap,
      checkpointPayload,
      runStatus,
    ) ||
    agentMap.get("impl_planner")?.completeRow
  ) {
    seenAgents.add("hitl_2");
  }

  const visibleAgents = PIPELINE_ORDER.filter(
    (a) => seenAgents.has(a) || a === runStatus?.current_agent,
  );
  const completedPipelineAgents = PIPELINE_AGENTS.filter(
    (a) => !!agentMap.get(a)?.completeRow,
  ).length;

  return (
    <div className="flex flex-col h-full">
      {/* Stream header */}
      <div
        className="flex items-center justify-between px-5 py-3 border-b flex-shrink-0"
        style={{ borderColor: "var(--border-dim)" }}
      >
        <span
          className="text-xs font-medium uppercase tracking-wider"
          style={{ color: "var(--text-dim)" }}
        >
          Agent Pipeline
        </span>
        {agentOutputs.length > 0 && (
          <span className="text-xs" style={{ color: "var(--text-dim)" }}>
            {completedPipelineAgents} / {PIPELINE_AGENTS.length} agents
          </span>
        )}
      </div>

      {/* Scrollable agent cards */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {visibleAgents.map((agent) => {
          const entry = agentMap.get(agent);
          const isRunning =
            runStatus?.current_agent === agent &&
            runStatus?.status === "running";
          const hitlResolved = isHitlResolved(
            agent,
            resolvedCheckpoints,
            agentMap,
            checkpointPayload,
            runStatus,
          );
          const isComplete = !!entry?.completeRow || hitlResolved;
          const isError =
            runStatus?.current_agent === agent && !!runStatus?.error;

          const completePayload = entry?.completeRow?.payload ?? {};
          const isDebuggerSkipped =
            agent === "debugger" &&
            !agentMap.get("debugger") &&
            seenAgents.has("pr_summarizer");

          if (
            shouldRenderHitlCard(
              agent,
              runStatus,
              checkpointPayload,
              resolvedCheckpoints,
              agentMap,
            )
          ) {
            const cpPayload = checkpointPayload?.payload ?? {};
            const rawSubtasks =
              (cpPayload.subtasks as unknown[] | undefined) ??
              (completePayload.subtasks as unknown[] | undefined) ??
              (outputSubtasks as unknown[]) ??
              [];
            const subtasks: Subtask[] = Array.isArray(rawSubtasks)
              ? rawSubtasks.map(normSubtask)
              : [];
            const rawPlan =
              (cpPayload.implementation_plan as unknown[] | undefined) ??
              (completePayload.implementation_plan as unknown[] | undefined) ??
              (outputPlan as unknown[]) ??
              [];
            const implPlan = rawPlan as ImplementationPlanItem[];

            return (
              <HITLCard
                key={agent}
                runId={runId}
                checkpoint={agent as "hitl_1" | "hitl_2"}
                subtasks={subtasks}
                implementationPlan={implPlan}
                pat={pat}
                onApproved={onApproved}
                onStopped={onStopped}
              />
            );
          }

          return (
            <AgentCard
              key={agent}
              agent={agent}
              phase={
                isComplete
                  ? "complete"
                  : entry?.startRow
                    ? "start"
                    : null
              }
              startedAt={entry?.startRow?.created_at ?? null}
              completedAt={entry?.completeRow?.created_at ?? null}
              isRunning={isRunning && !hitlResolved}
              isSkipped={isDebuggerSkipped}
              isError={isError}
              payload={completePayload}
            />
          );
        })}

        {/* Error banner */}
        {runStatus?.error && runStatus?.status === "failed" && (
          <div
            className="p-4 rounded-xl text-xs animate-fade-slide-up"
            style={{
              backgroundColor: "rgba(248, 113, 113, 0.08)",
              border: "1px solid rgba(248, 113, 113, 0.3)",
              color: "var(--status-error)",
            }}
          >
            <p className="font-medium mb-1">Run failed</p>
            <p style={{ color: "var(--text-secondary)" }}>
              {runStatus.error}
            </p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
