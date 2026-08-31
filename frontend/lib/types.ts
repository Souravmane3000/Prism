/**
 * lib/types.ts — TypeScript type definitions for Prism frontend.
 *
 * Mirrors backend/state.py TypedDicts and backend/routers/runs.py Pydantic models
 * exactly. No any types. All API calls and Realtime events are typed against these.
 */

// ── Pipeline state types (mirrors state.py) ─────────────────────────────────

export interface Subtask {
  id: string;
  title: string;
  description: string;
  dependencies: string[];
  likely_files: string[];
  complexity: "low" | "medium" | "high";
}

export interface FileMapEntry {
  path: string;
  relevance_score: number;
  source: "pgvector" | "github" | "both";
}

export interface ImplementationStep {
  order: number;
  file: string;
  function_or_symbol: string | null;
  change_description: string;
  rationale: string;
  tradeoffs: string[];
}

export interface ImplementationPlanItem {
  subtask_id: string;
  steps: ImplementationStep[];
}

export interface TestFailure {
  name: string;
  traceback: string;
  message: string;
}

export interface TestResults {
  framework: string;
  passed: string[];
  failed: TestFailure[];
  passed_count: number;
  failed_count: number;
  exit_code: number;
  stdout: string;
  stderr: string;
}

export interface DebugFix {
  failing_test: string;
  root_cause: string;
  proposed_fix: string;
  confidence: number; // 0.0–1.0
  target_files: string[];
}

export interface DebugReport {
  fixes: DebugFix[];
  summary: string;
}

export interface PRDraft {
  title: string;
  body: string;
  what_changed: string;
  why: string;
  testing_notes: string;
  limitations: string;
  review_checklist: string[];
}

// ── Graph / run metadata ─────────────────────────────────────────────────────

export type RunStatus =
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentName =
  | "planner"
  | "hitl_1"
  | "code_navigator"
  | "impl_planner"
  | "hitl_2"
  | "test_runner"
  | "debugger"
  | "pr_summarizer";

export type AgentPhase = "start" | "complete";

export type CheckpointName = "hitl_1" | "hitl_2";

export type ApproveAction =
  | "approve"
  | "edit"
  | "revise"
  | "restart"
  | "stop";

// ── API request types ────────────────────────────────────────────────────────

export interface StartRunRequest {
  repo_url: string;
  issue_url: string | null;
  issue_text: string | null;
  github_token: string;
}

export interface ApproveRunRequest {
  checkpoint: CheckpointName;
  action: ApproveAction;
  subtasks: Subtask[] | null;
  implementation_plan: ImplementationPlanItem[] | null;
  github_token: string;
}

export interface CreatePRRequest {
  github_token: string;
  head_branch?: string;
  base_branch?: string;
  commit_message?: string;
}

// ── API response types (mirrors Pydantic models in runs.py) ─────────────────

export interface StartRunResponse {
  run_id: string;
  status: RunStatus;
  current_agent: AgentName;
}

export interface RunStatusResponse {
  run_id: string;
  status: RunStatus;
  current_agent: AgentName;
  error: string | null;
  all_tests_passed: boolean | null;
  updated_at: string | null;
}

export interface RunOutputResponse {
  run_id: string;
  status: RunStatus;
  current_agent: AgentName;
  repo_url: string;
  issue_url: string | null;
  issue_text: string | null;
  subtasks: Subtask[];
  planner_approved: boolean | null;
  file_map: Record<string, FileMapEntry[]>;
  implementation_plan: ImplementationPlanItem[];
  impl_approved: boolean | null;
  test_results: TestResults | null;
  all_tests_passed: boolean | null;
  debug_report: DebugReport | null;
  pr_draft: PRDraft | null;
  messages: string[];
  error: string | null;
  pr_url: string | null;
}

export interface ApproveRunResponse {
  run_id: string;
  status: RunStatus;
  current_agent: AgentName;
  message: string;
}

export interface CreatePRResponse {
  run_id: string;
  pr_url: string;
  pr_number: number | null;
  title: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  run_id: string | null;
  details: Record<string, unknown>;
}

// ── Supabase Realtime row types ──────────────────────────────────────────────

export interface RunRow {
  id: string;
  repo_url: string;
  issue_url: string | null;
  status: RunStatus;
  current_agent: AgentName;
  error: string | null;
  all_tests_passed: boolean | null;
  pr_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentOutputRow {
  id: string;
  run_id: string;
  agent: AgentName;
  phase: AgentPhase;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CheckpointRow {
  id: string;
  run_id: string;
  checkpoint_name: CheckpointName;
  payload: Record<string, unknown>;
  user_decision: Record<string, unknown> | null;
  created_at: string;
  resolved_at: string | null;
}

// ── Frontend state types ─────────────────────────────────────────────────────

/** Persisted to localStorage — never includes the GitHub PAT */
export interface SessionRecord {
  run_id: string;
  repo_url: string;
  created_at: string;
  status: RunStatus;
}

/** Realtime subscription state returned by useSupabaseRealtime */
export interface RealtimeState {
  runStatus: RunRow | null;
  agentOutputs: AgentOutputRow[];
  checkpointPayload: CheckpointRow | null;
  isConnected: boolean;
  transport: "realtime" | "poll" | "none";
}

/** Agent display info for the activity stream */
export interface AgentDisplayInfo {
  name: AgentName;
  label: string;
  startedAt: string | null;
  completedAt: string | null;
  phase: AgentPhase | null;
  payload: Record<string, unknown>;
}

// ── HITL checkpoint payload shapes (from GRAPH.md §3.2 and §3.5) ────────────

export interface Hitl1Payload {
  checkpoint: "hitl_1";
  run_id: string;
  type: "subtask_approval";
  subtasks: Subtask[];
  actions_allowed: string[];
}

export interface Hitl2Payload {
  checkpoint: "hitl_2";
  run_id: string;
  type: "implementation_plan_approval";
  implementation_plan: ImplementationPlanItem[];
  actions_allowed: string[];
}
