/**
 * lib/hitlVisibility.ts — When to show the interactive HITL approve/stop card.
 *
 * After Approve, the next agent can start before Realtime updates the
 * checkpoint row (user_decision stays null). The card must still disappear
 * and stay gone for the rest of the run.
 */

import type {
  AgentName,
  AgentOutputRow,
  CheckpointRow,
  RunRow,
} from "@/lib/types";

export const PIPELINE_ORDER: AgentName[] = [
  "planner",
  "hitl_1",
  "code_navigator",
  "impl_planner",
  "hitl_2",
  "test_runner",
  "debugger",
  "pr_summarizer",
];

const AFTER_HITL_1: AgentName[] = [
  "code_navigator",
  "impl_planner",
  "hitl_2",
  "test_runner",
  "debugger",
  "pr_summarizer",
];

const AFTER_HITL_2: AgentName[] = [
  "test_runner",
  "debugger",
  "pr_summarizer",
];

export type AgentMap = Map<
  AgentName,
  { startRow?: AgentOutputRow; completeRow?: AgentOutputRow }
>;

export function isTerminalRunStatus(
  status: RunRow["status"] | undefined,
): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export function laterPipelineAgentCompleted(
  agent: AgentName,
  agentMap: AgentMap,
): boolean {
  const idx = PIPELINE_ORDER.indexOf(agent);
  if (idx < 0) return false;
  return PIPELINE_ORDER.slice(idx + 1).some(
    (name) => !!agentMap.get(name)?.completeRow,
  );
}

export function firstLaterCompleteAt(
  agent: AgentName,
  agentMap: AgentMap,
): string | null {
  const idx = PIPELINE_ORDER.indexOf(agent);
  if (idx < 0) return null;
  for (const name of PIPELINE_ORDER.slice(idx + 1)) {
    const at = agentMap.get(name)?.completeRow?.created_at;
    if (at) return at;
  }
  return null;
}

export function laterAgentsStarted(
  agent: "hitl_1" | "hitl_2",
  agentMap: AgentMap,
  currentAgent: string | null | undefined,
): boolean {
  const later = agent === "hitl_1" ? AFTER_HITL_1 : AFTER_HITL_2;
  if (currentAgent && later.includes(currentAgent as AgentName)) return true;
  return later.some((name) => agentMap.has(name));
}

export function isHitlResolved(
  agent: AgentName,
  resolvedCheckpoints: ReadonlySet<string>,
  agentMap: AgentMap,
  checkpointPayload: CheckpointRow | null,
  runStatus: RunRow | null,
): boolean {
  if (agent !== "hitl_1" && agent !== "hitl_2") return false;
  if (resolvedCheckpoints.has(agent)) return true;
  if (agentMap.get(agent)?.completeRow) return true;
  if (
    checkpointPayload?.checkpoint_name === agent &&
    checkpointPayload.user_decision != null
  ) {
    return true;
  }
  return laterAgentsStarted(agent, agentMap, runStatus?.current_agent);
}

export function shouldRenderHitlCard(
  agent: AgentName,
  runStatus: RunRow | null,
  checkpointPayload: CheckpointRow | null,
  resolvedCheckpoints: ReadonlySet<string>,
  agentMap: AgentMap,
): boolean {
  if (agent !== "hitl_1" && agent !== "hitl_2") return false;
  if (isTerminalRunStatus(runStatus?.status)) return false;
  if (
    isHitlResolved(
      agent,
      resolvedCheckpoints,
      agentMap,
      checkpointPayload,
      runStatus,
    )
  ) {
    return false;
  }

  if (
    runStatus?.status === "awaiting_approval" &&
    (!runStatus.current_agent || runStatus.current_agent === agent)
  ) {
    return true;
  }
  if (runStatus?.current_agent === agent) return true;
  if (
    checkpointPayload?.checkpoint_name === agent &&
    checkpointPayload.user_decision == null &&
    runStatus?.status === "awaiting_approval"
  ) {
    return true;
  }

  // Heuristic only while still paused — never after the pipeline has moved on.
  if (runStatus?.status !== "awaiting_approval") return false;
  if (agent === "hitl_1") {
    return (
      !!agentMap.get("planner")?.completeRow && !agentMap.get("code_navigator")
    );
  }
  return (
    !!agentMap.get("impl_planner")?.completeRow && !agentMap.get("test_runner")
  );
}
