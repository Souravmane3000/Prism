/**
 * components/output/OutputInspector.tsx — Right panel tabbed output inspector.
 *
 * Manages active tab state. Disables tabs until the relevant agent completes.
 * Tab availability:
 *   Plan      → enabled after planner completes (subtasks available)
 *   Files     → enabled after code_navigator completes (file_map available)
 *   PR Draft  → enabled after pr_summarizer completes, or when the run is terminal
 *   Debug     → enabled after test_runner completes, or when the run is terminal
 */

"use client";

import { useState } from "react";
import Tabs, { TabPanel, type OutputTab } from "@/components/ui/Tabs";
import PlanTab from "@/components/output/PlanTab";
import FilesTab from "@/components/output/FilesTab";
import PRDraftTab from "@/components/output/PRDraftTab";
import DebugTab from "@/components/output/DebugTab";
import {
  normalizeDebugReport,
  normalizePRDraft,
  normalizeTestResults,
} from "@/lib/output";
import type { RunOutputResponse, RunStatus } from "@/lib/types";

interface OutputInspectorProps {
  runOutput: RunOutputResponse | null;
  runId: string | null;
  runStatus: RunStatus | null;
  pat: string;
  prUrl: string | null;
}

export default function OutputInspector({
  runOutput,
  runId,
  runStatus,
  pat,
  prUrl,
}: OutputInspectorProps) {
  const [activeTab, setActiveTab] = useState<OutputTab>("plan");

  const prDraft = normalizePRDraft(runOutput?.pr_draft);
  const testResults = normalizeTestResults(runOutput?.test_results);
  const debugReport = normalizeDebugReport(runOutput?.debug_report);
  const isTerminal =
    runStatus === "completed" ||
    runStatus === "failed" ||
    runOutput?.status === "completed" ||
    runOutput?.status === "failed";

  // Determine which tabs are disabled based on available output
  const disabledTabs = new Set<OutputTab>();
  if (!runOutput?.subtasks?.length) disabledTabs.add("plan");
  if (!runOutput?.file_map || !Object.keys(runOutput.file_map).length)
    disabledTabs.add("files");
  if (!prDraft && !isTerminal) disabledTabs.add("pr-draft");
  if (
    !testResults &&
    !debugReport &&
    runOutput?.all_tests_passed !== true &&
    runOutput?.all_tests_passed !== false &&
    !isTerminal
  ) {
    disabledTabs.add("debug");
  }

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "var(--bg-panel)", backdropFilter: "var(--glass-blur)" }}
    >
      {/* Tab bar */}
      <Tabs
        activeTab={activeTab}
        onTabChange={setActiveTab}
        disabledTabs={disabledTabs}
      />

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        <TabPanel id="plan" activeTab={activeTab}>
          <PlanTab
            subtasks={runOutput?.subtasks ?? []}
            implementationPlan={runOutput?.implementation_plan ?? []}
          />
        </TabPanel>

        <TabPanel id="files" activeTab={activeTab}>
          <FilesTab fileMap={runOutput?.file_map ?? {}} />
        </TabPanel>

        <TabPanel id="pr-draft" activeTab={activeTab}>
          <PRDraftTab
            prDraft={prDraft}
            runId={runId}
            runStatus={runStatus ?? runOutput?.status ?? null}
            prUrl={prUrl ?? runOutput?.pr_url ?? null}
            pat={pat}
          />
        </TabPanel>

        <TabPanel id="debug" activeTab={activeTab}>
          <DebugTab
            testResults={testResults}
            debugReport={debugReport}
            allTestsPassed={runOutput?.all_tests_passed ?? null}
            runStatus={runStatus ?? runOutput?.status ?? null}
          />
        </TabPanel>
      </div>
    </div>
  );
}
