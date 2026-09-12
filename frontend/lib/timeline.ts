import type { AgentOutputRow, SessionEventRow } from "@/lib/types";

export type TimelineTone =
  | "running"
  | "complete"
  | "waiting"
  | "error"
  | "neutral";

export interface TimelinePresentation {
  label: string;
  detail: string;
  tone: TimelineTone;
}

const PRESENTATIONS: Record<string, TimelinePresentation> = {
  "verification.tests.started": {
    label: "Verification attempt",
    detail: "Running tests against the applied workspace.",
    tone: "running",
  },
  "verification.tests.completed": {
    label: "Tests finished",
    detail: "Test execution results are available.",
    tone: "complete",
  },
  "debugger.analysis.started": {
    label: "Debugging failure",
    detail: "Investigating failed tests and likely causes.",
    tone: "running",
  },
  "debugger.analysis.completed": {
    label: "Failure analyzed",
    detail: "A focused diagnosis is ready.",
    tone: "complete",
  },
  "debugger.analysis.failed": {
    label: "Debugger could not produce a safe fix",
    detail: "No speculative patch will be applied.",
    tone: "error",
  },
  "patch.generation.started": {
    label: "Generating patch",
    detail: "Writing a structured diff.",
    tone: "running",
  },
  "patch.generation.completed": {
    label: "Patch generated",
    detail: "A structured patch is ready for validation.",
    tone: "complete",
  },
  "patch.application.started": {
    label: "Applying patch",
    detail: "Applying the patch in an isolated repository.",
    tone: "running",
  },
  "patch.application.completed": {
    label: "Patch applied",
    detail: "The isolated working tree reflects the patch.",
    tone: "complete",
  },
  "rollback.started": {
    label: "Rolling back",
    detail: "Restoring the isolated repository to the baseline commit.",
    tone: "running",
  },
  "rollback.completed": {
    label: "Rollback completed",
    detail: "The isolated repository matches the baseline.",
    tone: "complete",
  },
  "rollback.failed": {
    label: "Rollback failed",
    detail: "The isolated repository could not be restored to baseline.",
    tone: "error",
  },
};

function humanizeEventType(eventType: string): string {
  const words = eventType
    .split(".")
    .flatMap((part) => part.split("_"))
    .filter(Boolean);
  if (words.length === 0) return "Execution update";
  const sentence = words.join(" ");
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

export function presentEvent(event: SessionEventRow): TimelinePresentation {
  const known = PRESENTATIONS[event.event_type];
  const payloadSummary =
    event.payload && typeof event.payload.summary === "string"
      ? event.payload.summary
      : "";
  const attempts = event.payload?.verification_attempts;
  const failed =
    event.event_type === "verification.tests.completed" &&
    event.payload?.all_tests_passed === false;
  const passed =
    event.event_type === "verification.tests.completed" &&
    event.payload?.all_tests_passed === true;
  if (known) {
    const attemptLabel =
      event.event_type.startsWith("verification.tests") &&
      typeof attempts === "number"
        ? `Verification attempt ${attempts}`
        : known.label;
    let tone: TimelineTone = known.tone;
    if (failed) tone = "error";
    else if (passed) tone = "complete";
    return {
      ...known,
      label: attemptLabel,
      detail: payloadSummary.trim() || known.detail,
      tone,
    };
  }
  return {
    label: humanizeEventType(event.event_type),
    detail: payloadSummary || "Prism reported an execution update.",
    tone: event.event_type.endsWith(".completed") ? "complete" : "neutral",
  };
}

export function legacyOutputsToEvents(outputs: AgentOutputRow[]): SessionEventRow[] {
  return outputs.map((row, index) => ({
    id: row.id,
    run_id: row.run_id,
    sequence: index,
    event_type:
      row.phase === "start"
        ? `${row.agent}.started`
        : `${row.agent}.completed`,
    payload: row.payload,
    created_at: row.created_at,
  }));
}
