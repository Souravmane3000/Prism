/**
 * lib/output.ts — Normalize pipeline output objects for the inspector tabs.
 *
 * Agent payloads (and older completed runs) may omit nested arrays. The UI
 * must never crash on `.length` of undefined.
 */

import type {
  AgentOutputRow,
  DebugReport,
  FileMapEntry,
  ImplementationPlanItem,
  PRDraft,
  RunOutputResponse,
  RunRow,
  Subtask,
  TestResults,
} from "@/lib/types";

export type TestOutcome =
  | "missing"
  | "skipped"
  | "did_not_run"
  | "failed"
  | "passed";

/** Keep in lockstep with backend/test_outcome.py classify_test_results. */
export function classifyTestResults(
  raw: TestResults | null | undefined,
): TestOutcome {
  if (!raw) return "missing";
  const framework = (raw.framework ?? "").toLowerCase();
  if (framework === "skipped" || framework === "not_run") return "skipped";

  const passed = raw.passed_count ?? 0;
  const failedCount = raw.failed_count ?? 0;
  const failedList = Array.isArray(raw.failed) ? raw.failed : [];
  const exitCode = raw.exit_code ?? 0;
  const collected = passed + failedCount > 0 || failedList.length > 0;

  // Unknown framework + 0 collected is an empty sandbox dump, not a green suite.
  if (!collected && (framework === "unknown" || framework === "")) {
    return "did_not_run";
  }
  if (exitCode !== 0 && !collected) return "did_not_run";
  if (failedCount > 0 || failedList.length > 0) return "failed";
  if (exitCode !== 0) return "failed";
  return "passed";
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function normalizePRDraft(raw: PRDraft | null | undefined): PRDraft | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as unknown as Record<string, unknown>;
  const title = asString(record.title);
  const body = asString(record.body);
  if (!title && !body) return null;

  const checklist = Array.isArray(record.review_checklist)
    ? record.review_checklist.map((item) => String(item))
    : [];

  return {
    title: title || "Prism Analysis PR",
    body,
    what_changed: asString(record.what_changed),
    why: asString(record.why),
    testing_notes: asString(record.testing_notes),
    limitations: asString(record.limitations),
    review_checklist: checklist,
  };
}

export function normalizeTestResults(
  raw: TestResults | null | undefined,
): TestResults | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as unknown as Record<string, unknown>;

  const failed = Array.isArray(record.failed)
    ? record.failed.map((item) => {
        const f = (item ?? {}) as Record<string, unknown>;
        return {
          name: asString(f.name, "unknown"),
          traceback: asString(f.traceback),
          message: asString(f.message),
        };
      })
    : [];

  const passed = Array.isArray(record.passed)
    ? record.passed.map((item) => String(item))
    : [];

  return {
    framework: asString(record.framework, "unknown"),
    passed,
    failed,
    passed_count:
      typeof record.passed_count === "number" ? record.passed_count : passed.length,
    failed_count:
      typeof record.failed_count === "number" ? record.failed_count : failed.length,
    exit_code: typeof record.exit_code === "number" ? record.exit_code : 0,
    stdout: asString(record.stdout),
    stderr: asString(record.stderr),
  };
}

export function normalizeDebugReport(
  raw: DebugReport | null | undefined,
): DebugReport | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as unknown as Record<string, unknown>;
  const summary = asString(record.summary);
  const fixes = Array.isArray(record.fixes)
    ? record.fixes.map((item) => {
        const f = (item ?? {}) as Record<string, unknown>;
        return {
          failing_test: asString(f.failing_test, "unknown"),
          root_cause: asString(f.root_cause),
          proposed_fix: asString(f.proposed_fix),
          confidence: typeof f.confidence === "number" ? f.confidence : 0,
          target_files: Array.isArray(f.target_files)
            ? f.target_files.map((path) => String(path))
            : [],
        };
      })
    : [];

  if (!summary && fixes.length === 0) return null;
  return { summary, fixes };
}

function isNonEmptyArray(val: unknown): val is unknown[] {
  return Array.isArray(val) && val.length > 0;
}

function isNonEmptyRecord(val: unknown): val is Record<string, unknown> {
  return (
    !!val &&
    typeof val === "object" &&
    !Array.isArray(val) &&
    Object.keys(val as object).length > 0
  );
}

/**
 * Inspector tabs historically waited on GET /output. After Realtime drops,
 * that call can return empty checkpoint fields while agent_outputs already
 * has planner/navigator/PR payloads. Merge both, preferring non-empty data.
 */
export function materializeRunOutput(
  runId: string,
  runStatus: RunRow | null,
  outputs: AgentOutputRow[],
  apiOutput: RunOutputResponse | null,
): RunOutputResponse {
  const fromAgents: Record<string, unknown> = {};
  for (const row of outputs) {
    if (row.phase !== "complete" || !row.payload) continue;
    Object.assign(fromAgents, row.payload);
  }

  const subtasks = isNonEmptyArray(apiOutput?.subtasks)
    ? apiOutput.subtasks
    : isNonEmptyArray(fromAgents.subtasks)
      ? (fromAgents.subtasks as Subtask[])
      : (apiOutput?.subtasks ?? []);

  const apiMap = apiOutput?.file_map;
  const agentMap = fromAgents.file_map;
  const fileMap = isNonEmptyRecord(apiMap)
    ? apiMap
    : isNonEmptyRecord(agentMap)
      ? (agentMap as Record<string, FileMapEntry[]>)
      : (apiMap ?? {});

  const implementationPlan = isNonEmptyArray(apiOutput?.implementation_plan)
    ? apiOutput.implementation_plan
    : isNonEmptyArray(fromAgents.implementation_plan)
      ? (fromAgents.implementation_plan as ImplementationPlanItem[])
      : (apiOutput?.implementation_plan ?? []);

  const prDraft =
    normalizePRDraft(apiOutput?.pr_draft) ??
    normalizePRDraft(fromAgents.pr_draft as PRDraft | undefined);

  const testResults =
    normalizeTestResults(apiOutput?.test_results) ??
    normalizeTestResults(fromAgents.test_results as TestResults | undefined);

  const debugReport =
    normalizeDebugReport(apiOutput?.debug_report) ??
    normalizeDebugReport(fromAgents.debug_report as DebugReport | undefined);

  const allTestsPassed =
    typeof apiOutput?.all_tests_passed === "boolean"
      ? apiOutput.all_tests_passed
      : typeof fromAgents.all_tests_passed === "boolean"
        ? fromAgents.all_tests_passed
        : typeof runStatus?.all_tests_passed === "boolean"
          ? runStatus.all_tests_passed
          : null;

  return {
    run_id: runId,
    status: apiOutput?.status ?? runStatus?.status ?? "running",
    current_agent:
      apiOutput?.current_agent ??
      (runStatus?.current_agent as RunOutputResponse["current_agent"]) ??
      "planner",
    repo_url: apiOutput?.repo_url ?? runStatus?.repo_url ?? "",
    issue_url: apiOutput?.issue_url ?? runStatus?.issue_url ?? null,
    issue_text: apiOutput?.issue_text ?? null,
    subtasks,
    planner_approved: apiOutput?.planner_approved ?? null,
    file_map: fileMap as Record<string, FileMapEntry[]>,
    implementation_plan: implementationPlan,
    impl_approved: apiOutput?.impl_approved ?? null,
    test_results: testResults,
    all_tests_passed: allTestsPassed,
    debug_report: debugReport,
    pr_draft: prDraft,
    messages: apiOutput?.messages ?? [],
    error: apiOutput?.error ?? runStatus?.error ?? null,
    pr_url: apiOutput?.pr_url ?? runStatus?.pr_url ?? null,
  };
}
