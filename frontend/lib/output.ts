/**
 * lib/output.ts — Normalize pipeline output objects for the inspector tabs.
 *
 * Agent payloads (and older completed runs) may omit nested arrays. The UI
 * must never crash on `.length` of undefined.
 */

import type { DebugReport, PRDraft, TestResults } from "@/lib/types";

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
