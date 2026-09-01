/**
 * components/output/DebugTab.tsx — Test failure report + debug analysis.
 *
 * Shows:
 *   - Test summary (framework, pass/fail counts)
 *   - If all_tests_passed=true → "Debugger skipped" success state
 *   - If debug_report present → summary + per-fix cards with confidence bars
 *   - Failing test tracebacks (collapsible)
 */

"use client";

import { useState } from "react";
import {
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileCode,
} from "lucide-react";
import type { DebugReport, RunStatus, TestResults } from "@/lib/types";
import { classifyTestResults } from "@/lib/output";

interface DebugTabProps {
  testResults: TestResults | null;
  debugReport: DebugReport | null;
  allTestsPassed: boolean | null;
  runStatus?: RunStatus | null;
}

function confidenceColor(score: number): string {
  if (score >= 0.7) return "var(--confidence-high)";
  if (score >= 0.4) return "var(--confidence-mid)";
  return "var(--confidence-low)";
}

export default function DebugTab({
  testResults,
  debugReport,
  allTestsPassed,
  runStatus = null,
}: DebugTabProps) {
  const [expandedTracebacks, setExpandedTracebacks] = useState<Set<number>>(
    new Set(),
  );
  const [expandedFixes, setExpandedFixes] = useState<Set<number>>(
    new Set([0]),
  );

  function toggleTraceback(i: number) {
    setExpandedTracebacks((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  function toggleFix(i: number) {
    setExpandedFixes((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  const failedTests = testResults?.failed ?? [];
  const isComplete =
    runStatus === "completed" || runStatus === "failed";
  const outcome = classifyTestResults(testResults);
  const suiteDidNotRun = outcome === "did_not_run";
  const runnerLog = (testResults?.stderr || testResults?.stdout || "").trim();
  const knownFramework = !["unknown", "skipped", "not_run", ""].includes(
    (testResults?.framework ?? "").toLowerCase(),
  );
  const testsCollected =
    (testResults?.passed_count ?? 0) + (testResults?.failed_count ?? 0) > 0 ||
    (testResults?.failed?.length ?? 0) > 0;
  const showAllPassedBanner =
    allTestsPassed === true &&
    outcome === "passed" &&
    (testsCollected || knownFramework);

  // Nothing to show yet
  if (!testResults && !debugReport && allTestsPassed === null && !isComplete) {
    return (
      <div
        className="flex items-center justify-center h-full p-6"
        style={{ color: "var(--text-dim)" }}
      >
        <p className="text-xs text-center">
          Test Runner has not completed yet. Results will appear here.
        </p>
      </div>
    );
  }

  if (!testResults && !debugReport && allTestsPassed === null && isComplete) {
    return (
      <div
        className="flex items-center justify-center h-full p-6"
        style={{ color: "var(--text-dim)" }}
      >
        <p className="text-xs text-center">
          No test results were recorded for this run. If tests were skipped or
          the sandbox could not start, re-run the pipeline to populate this tab.
        </p>
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-4 overflow-y-auto">
      {/* Test summary */}
      {testResults && (
        <div
          className="rounded-xl p-4"
          style={{
            border: "1px solid var(--border-subtle)",
            backgroundColor: "var(--bg-card)",
          }}
        >
          <div className="flex items-center gap-2 mb-3">
            {outcome === "passed" ? (
              <CheckCircle2
                size={14}
                style={{ color: "var(--status-complete)" }}
              />
            ) : (
              <AlertTriangle
                size={14}
                style={{ color: "var(--status-awaiting)" }}
              />
            )}
            <span
              className="text-sm font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              Test Results
            </span>
            <span
              className="text-xs font-mono px-1.5 py-0.5 rounded ml-auto"
              style={{
                backgroundColor: "var(--bg-hover)",
                color: "var(--text-dim)",
              }}
            >
              {outcome === "skipped"
                ? "not run locally"
                : outcome === "did_not_run"
                  ? "no tests collected"
                  : testResults.framework}
            </span>
          </div>

          <p className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
            {outcome === "skipped"
              ? "Local runs use OpenAI and Supabase API keys from .env. The repository test suite runs in Modal.Sandbox only after the backend is deployed."
              : "Counts are tests in the target repository, not Prism agents. All pipeline agents finishing does not mean the repo test suite passed."}
          </p>

          {outcome !== "skipped" && (
          <div className="flex gap-4">
            <div>
              <p
                className="text-xs"
                style={{ color: "var(--text-dim)" }}
              >
                Passed
              </p>
              <p
                className="text-lg font-bold"
                style={{ color: "var(--status-complete)" }}
              >
                {testResults.passed_count}
              </p>
            </div>
            <div>
              <p
                className="text-xs"
                style={{ color: "var(--text-dim)" }}
              >
                Failed
              </p>
              <p
                className="text-lg font-bold"
                style={{
                  color:
                    testResults.failed_count > 0
                      ? "var(--status-error)"
                      : "var(--text-dim)",
                }}
              >
                {testResults.failed_count}
              </p>
            </div>
            <div>
              <p
                className="text-xs"
                style={{ color: "var(--text-dim)" }}
              >
                Exit Code
              </p>
              <p
                className="text-lg font-bold font-mono"
                style={{
                  color:
                    testResults.exit_code === 0
                      ? "var(--status-complete)"
                      : "var(--status-error)",
                }}
              >
                {testResults.exit_code}
              </p>
            </div>
          </div>
          )}
        </div>
      )}

      {outcome === "skipped" && testResults && (
        <div
          className="flex flex-col gap-2 p-4 rounded-xl"
          style={{
            backgroundColor: "var(--bg-card)",
            border: "1px solid var(--border-subtle)",
          }}
        >
          <p
            className="text-xs font-medium"
            style={{ color: "var(--text-primary)" }}
          >
            Repository tests were not executed
          </p>
          <p className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
            {testResults.stderr ||
              "Test Runner is skipped in local development. No Modal token is required here."}
          </p>
        </div>
      )}

      {suiteDidNotRun && testResults && (
        <div
          className="flex flex-col gap-2 p-4 rounded-xl"
          style={{
            backgroundColor:
              testResults.exit_code === 0
                ? "var(--bg-card)"
                : "rgba(248, 113, 113, 0.06)",
            border:
              testResults.exit_code === 0
                ? "1px solid var(--border-subtle)"
                : "1px solid rgba(248, 113, 113, 0.25)",
          }}
        >
          <p
            className="text-xs font-medium"
            style={{
              color:
                testResults.exit_code === 0
                  ? "var(--text-primary)"
                  : "var(--status-error)",
            }}
          >
            {testResults.exit_code === 0
              ? "No test suite was detected"
              : `Repository tests never ran (exit code ${testResults.exit_code})`}
          </p>
          <p className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
            {testResults.exit_code === 0
              ? "Passed and failed stay at 0 because the sandbox did not find pytest, unittest, or Jest in this repository. That is not a successful test run."
              : "Passed/failed stay at 0 because the sandbox exited before collecting tests. This is not a count of Prism agents. The log below is the actual failure."}
          </p>
          {runnerLog && (
            <pre
              className="text-xs overflow-x-auto p-2 rounded mt-1"
              style={{
                backgroundColor: "rgba(0,0,0,0.3)",
                color: "var(--text-secondary)",
                fontFamily: "var(--font-mono)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: "180px",
                overflowY: "auto",
              }}
            >
              {runnerLog}
            </pre>
          )}
        </div>
      )}

      {/* All tests passed / debugger skipped */}
      {showAllPassedBanner && (
        <div
          className="flex items-center gap-3 p-4 rounded-xl"
          style={{
            backgroundColor: "rgba(52, 211, 153, 0.06)",
            border: "1px solid rgba(52, 211, 153, 0.2)",
          }}
        >
          <CheckCircle2
            size={16}
            style={{ color: "var(--status-complete)", flexShrink: 0 }}
          />
          <div>
            <p
              className="text-xs font-medium"
              style={{ color: "var(--status-complete)" }}
            >
              All tests passed
            </p>
            <p className="text-xs mt-0.5" style={{ color: "var(--text-dim)" }}>
              Debugger was skipped. Pipeline proceeded directly to PR Summarizer.
            </p>
          </div>
        </div>
      )}

      {/* Failing test tracebacks */}
      {testResults && failedTests.length > 0 && (
        <div className="flex flex-col gap-2">
          <p
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: "var(--text-dim)" }}
          >
            Failing Tests ({failedTests.length})
          </p>
          {failedTests.map((failure, i) => (
            <div
              key={i}
              className="rounded-xl overflow-hidden"
              style={{
                border: "1px solid rgba(248, 113, 113, 0.2)",
                backgroundColor: "rgba(248, 113, 113, 0.04)",
              }}
            >
              <button
                onClick={() => toggleTraceback(i)}
                className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
                style={{ background: "transparent", border: "none", cursor: "pointer" }}
              >
                <span style={{ color: "var(--text-dim)" }}>
                  {expandedTracebacks.has(i) ? (
                    <ChevronDown size={12} />
                  ) : (
                    <ChevronRight size={12} />
                  )}
                </span>
                <span
                  className="text-xs font-mono flex-1 truncate"
                  style={{ color: "var(--status-error)" }}
                >
                  {failure.name}
                </span>
              </button>
              {expandedTracebacks.has(i) && (
                <div
                  className="px-3 pb-3"
                  style={{ borderTop: "1px solid rgba(248, 113, 113, 0.15)" }}
                >
                  <p
                    className="text-xs pt-2 mb-2"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    {failure.message}
                  </p>
                  <pre
                    className="text-xs overflow-x-auto p-2 rounded"
                    style={{
                      backgroundColor: "rgba(0,0,0,0.3)",
                      color: "var(--text-dim)",
                      fontFamily: "var(--font-mono)",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                      maxHeight: "120px",
                      overflowY: "auto",
                    }}
                  >
                    {failure.traceback}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Debug report — skip empty "no failures" when the suite never ran */}
      {debugReport && outcome !== "skipped" && !(suiteDidNotRun && (debugReport.fixes?.length ?? 0) === 0) && (
        <div className="flex flex-col gap-2">
          {/* Summary */}
          <div
            className="p-3 rounded-xl"
            style={{
              backgroundColor: "var(--bg-card)",
              border: "1px solid var(--border-subtle)",
            }}
          >
            <p
              className="text-xs font-medium uppercase tracking-wider mb-2"
              style={{ color: "var(--text-dim)" }}
            >
              Debug Summary
            </p>
            <p
              className="text-xs"
              style={{
                color: "var(--text-secondary)",
                lineHeight: "1.6",
              }}
            >
              {debugReport.summary}
            </p>
          </div>

          {(debugReport.fixes?.length ?? 0) > 0 && (
            <>
          <p
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: "var(--text-dim)" }}
          >
            Fix Proposals ({(debugReport.fixes ?? []).length})
          </p>

          {(debugReport.fixes ?? []).map((fix, i) => (
            <div
              key={i}
              className="rounded-xl overflow-hidden"
              style={{
                border: "1px solid var(--border-subtle)",
                backgroundColor: "var(--bg-card)",
              }}
            >
              <button
                onClick={() => toggleFix(i)}
                className="w-full flex items-center gap-2 px-4 py-3 text-left"
                style={{ background: "transparent", border: "none", cursor: "pointer" }}
              >
                <span style={{ color: "var(--text-dim)" }}>
                  {expandedFixes.has(i) ? (
                    <ChevronDown size={12} />
                  ) : (
                    <ChevronRight size={12} />
                  )}
                </span>
                <span
                  className="text-xs font-mono flex-1 truncate"
                  style={{ color: "var(--status-error)" }}
                >
                  {fix.failing_test}
                </span>
                {/* Confidence indicator */}
                <span
                  className="text-xs font-medium flex-shrink-0"
                  style={{ color: confidenceColor(fix.confidence) }}
                >
                  {Math.round(fix.confidence * 100)}%
                </span>
              </button>

              {expandedFixes.has(i) && (
                <div
                  className="px-4 pb-4 flex flex-col gap-3"
                  style={{ borderTop: "1px solid var(--border-dim)" }}
                >
                  {/* Confidence bar */}
                  <div className="pt-3">
                    <div className="flex items-center justify-between mb-1">
                      <span
                        className="text-xs"
                        style={{ color: "var(--text-dim)" }}
                      >
                        Confidence
                      </span>
                      <span
                        className="text-xs font-medium"
                        style={{ color: confidenceColor(fix.confidence) }}
                      >
                        {Math.round(fix.confidence * 100)}%
                      </span>
                    </div>
                    <div
                      className="h-1 rounded-full overflow-hidden"
                      style={{ backgroundColor: "var(--border-dim)" }}
                    >
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${fix.confidence * 100}%`,
                          backgroundColor: confidenceColor(fix.confidence),
                        }}
                      />
                    </div>
                  </div>

                  {/* Root cause */}
                  <div>
                    <p
                      className="text-xs font-medium mb-1"
                      style={{ color: "var(--text-dim)" }}
                    >
                      Root Cause
                    </p>
                    <p
                      className="text-xs"
                      style={{
                        color: "var(--text-secondary)",
                        lineHeight: "1.6",
                      }}
                    >
                      {fix.root_cause}
                    </p>
                  </div>

                  {/* Proposed fix */}
                  <div>
                    <p
                      className="text-xs font-medium mb-1"
                      style={{ color: "var(--text-dim)" }}
                    >
                      Proposed Fix
                    </p>
                    <p
                      className="text-xs"
                      style={{
                        color: "var(--text-secondary)",
                        lineHeight: "1.6",
                      }}
                    >
                      {fix.proposed_fix}
                    </p>
                  </div>

                  {/* Target files */}
                  {(fix.target_files?.length ?? 0) > 0 && (
                    <div>
                      <p
                        className="text-xs font-medium mb-1.5"
                        style={{ color: "var(--text-dim)" }}
                      >
                        Target Files
                      </p>
                      <div className="flex flex-col gap-1">
                        {(fix.target_files ?? []).map((f) => (
                          <span
                            key={f}
                            className="flex items-center gap-1.5 text-xs font-mono"
                            style={{ color: "var(--accent-lime)" }}
                          >
                            <FileCode
                              size={10}
                              style={{ flexShrink: 0 }}
                            />
                            {f}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
