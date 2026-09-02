/**
 * components/stream/HITLCard.tsx — Interactive HITL checkpoint card.
 *
 * Two variants driven by checkpoint prop:
 *   hitl_1: Inline edit of Subtask list → "Approve & Continue"
 *   hitl_2: Inline edit of ImplementationPlan steps → "Approve & Execute Tests"
 *
 * Visual: pulsing lime border via animate-lime-pulse, glassmorphic bg.
 * On approval → calls approveRun() → emits onApproved() so parent can update.
 * On stop → calls approveRun with action="stop" → emits onStopped().
 * GitHub PAT is passed from parent state — never stored here.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import TextareaAutosize from "react-textarea-autosize";
import {
  CheckCheck,
  Pencil,
  Plus,
  Trash2,
  XCircle,
  ChevronDown,
  ChevronRight,
  AlertCircle,
} from "lucide-react";
import Button from "@/components/ui/Button";
import { approveRun } from "@/lib/api";
import type {
  CheckpointName,
  ImplementationPlanItem,
  ImplementationStep,
  Subtask,
} from "@/lib/types";

interface HITLCardProps {
  runId: string;
  checkpoint: CheckpointName;
  subtasks?: Subtask[];
  implementationPlan?: ImplementationPlanItem[];
  pat: string;
  onApproved: (checkpoint: string) => void;
  onStopped: () => void;
}

// ── Subtask editor ───────────────────────────────────────────────────────────

function SubtaskEditor({
  subtasks,
  onChange,
}: {
  subtasks: Subtask[];
  onChange: (updated: Subtask[]) => void;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(
    new Set(subtasks.map((_, i) => i)),
  );

  function toggle(i: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  function update(i: number, field: keyof Subtask, value: string) {
    const updated = subtasks.map((st, idx) =>
      idx === i ? { ...st, [field]: value } : st,
    );
    onChange(updated);
  }

  function remove(i: number) {
    onChange(subtasks.filter((_, idx) => idx !== i));
  }

  function addSubtask() {
    const newSubtask: Subtask = {
      id: `st-${Date.now()}`,
      title: "New subtask",
      description: "",
      dependencies: [],
      likely_files: [],
      complexity: "medium",
    };
    const updated = [...subtasks, newSubtask];
    onChange(updated);
    setExpanded((prev) => new Set([...prev, updated.length - 1]));
  }

  const COMPLEXITY_COLORS: Record<string, string> = {
    low: "var(--complexity-low)",
    medium: "var(--complexity-medium)",
    high: "var(--complexity-high)",
  };

  return (
    <div className="flex flex-col gap-2">
      {subtasks.map((st, i) => (
        <div
          key={st.id}
          className="rounded-lg overflow-hidden"
          style={{
            border: "1px solid var(--border-dim)",
            backgroundColor: "rgba(10, 13, 8, 0.4)",
          }}
        >
          {/* Header */}
          <div
            className="flex items-center gap-2 px-3 py-2 cursor-pointer"
            style={{ backgroundColor: "var(--bg-hover)" }}
            onClick={() => toggle(i)}
          >
            <span style={{ color: "var(--text-dim)" }}>
              {expanded.has(i) ? (
                <ChevronDown size={12} />
              ) : (
                <ChevronRight size={12} />
              )}
            </span>

            <span
              className="text-xs font-mono px-1.5 py-0.5 rounded"
              style={{
                color: COMPLEXITY_COLORS[st.complexity] ?? "var(--text-dim)",
                backgroundColor: "rgba(255,255,255,0.04)",
                border: `1px solid ${COMPLEXITY_COLORS[st.complexity] ?? "var(--border-dim)"}`,
                opacity: 0.8,
              }}
            >
              {(st.complexity ?? "medium").toUpperCase()}
            </span>

            <span
              className="flex-1 text-xs font-medium truncate"
              style={{ color: "var(--text-primary)" }}
            >
              {st.title || "Untitled subtask"}
            </span>

            <button
              onClick={(e) => {
                e.stopPropagation();
                remove(i);
              }}
              className="p-1 rounded transition-colors"
              style={{ color: "var(--text-dim)" }}
            >
              <Trash2 size={11} />
            </button>
          </div>

          {/* Expanded fields */}
          {expanded.has(i) && (
            <div className="px-3 py-2 flex flex-col gap-2">
              <input
                value={st.title}
                onChange={(e) => update(i, "title", e.target.value)}
                placeholder="Subtask title"
                className="text-xs px-2 py-1.5 rounded w-full"
                style={{
                  backgroundColor: "var(--bg-input)",
                  border: "1px solid var(--border-dim)",
                  color: "var(--text-primary)",
                  outline: "none",
                  fontFamily: "inherit",
                }}
              />
              <TextareaAutosize
                value={st.description}
                onChange={(e) => update(i, "description", e.target.value)}
                placeholder="Description"
                minRows={2}
                className="text-xs px-2 py-1.5 rounded w-full resize-none"
                style={{
                  backgroundColor: "var(--bg-input)",
                  border: "1px solid var(--border-dim)",
                  color: "var(--text-secondary)",
                  outline: "none",
                  fontFamily: "inherit",
                }}
              />
              <select
                value={st.complexity}
                onChange={(e) => update(i, "complexity", e.target.value)}
                className="text-xs px-2 py-1 rounded"
                style={{
                  backgroundColor: "var(--bg-input)",
                  border: "1px solid var(--border-dim)",
                  color: "var(--text-secondary)",
                  outline: "none",
                  fontFamily: "inherit",
                }}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
          )}
        </div>
      ))}

      <button
        type="button"
        onClick={addSubtask}
        className="flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-lg transition-colors w-fit"
        style={{ color: "var(--accent-lime)", background: "transparent" }}
      >
        <Plus size={11} />
        Add subtask
      </button>
    </div>
  );
}

// ── Implementation plan editor ────────────────────────────────────────────────

function PlanEditor({
  plan,
  onChange,
}: {
  plan: ImplementationPlanItem[];
  onChange: (updated: ImplementationPlanItem[]) => void;
}) {
  const [expandedSubtask, setExpandedSubtask] = useState<Set<number>>(
    new Set([0]),
  );

  function toggleSubtask(i: number) {
    setExpandedSubtask((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  function updateStep(
    planIdx: number,
    stepIdx: number,
    field: keyof ImplementationStep,
    value: string,
  ) {
    const updated = plan.map((item, pi) => {
      if (pi !== planIdx) return item;
      return {
        ...item,
        steps: (item.steps ?? []).map((step, si) =>
          si === stepIdx ? { ...step, [field]: value } : step,
        ),
      };
    });
    onChange(updated);
  }

  return (
    <div className="flex flex-col gap-2">
      {plan.map((item, pi) => (
        <div
          key={item.subtask_id}
          className="rounded-lg overflow-hidden"
          style={{
            border: "1px solid var(--border-dim)",
            backgroundColor: "rgba(10, 13, 8, 0.4)",
          }}
        >
          <div
            className="flex items-center gap-2 px-3 py-2 cursor-pointer"
            style={{ backgroundColor: "var(--bg-hover)" }}
            onClick={() => toggleSubtask(pi)}
          >
            <span style={{ color: "var(--text-dim)" }}>
              {expandedSubtask.has(pi) ? (
                <ChevronDown size={12} />
              ) : (
                <ChevronRight size={12} />
              )}
            </span>
            <span
              className="text-xs font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {item.subtask_id}
            </span>
            <span
              className="text-xs ml-auto"
              style={{ color: "var(--text-dim)" }}
            >
              {(item.steps ?? []).length} step
              {(item.steps ?? []).length !== 1 ? "s" : ""}
            </span>
          </div>

          {expandedSubtask.has(pi) && (
            <div className="px-3 py-2 flex flex-col gap-2">
              {(item.steps ?? []).map((step, si) => (
                <div
                  key={si}
                  className="pl-3 flex flex-col gap-1.5"
                  style={{ borderLeft: "2px solid var(--border-subtle)" }}
                >
                  <div
                    className="flex items-center gap-2"
                    style={{ color: "var(--text-dim)" }}
                  >
                    <span className="text-xs font-mono">
                      Step {step.order}
                    </span>
                    <span className="text-xs truncate" style={{ color: "var(--accent-lime)" }}>
                      {step.file}
                    </span>
                  </div>
                  <TextareaAutosize
                    value={step.change_description}
                    onChange={(e) =>
                      updateStep(pi, si, "change_description", e.target.value)
                    }
                    placeholder="Change description"
                    minRows={2}
                    className="text-xs px-2 py-1.5 rounded w-full resize-none"
                    style={{
                      backgroundColor: "var(--bg-input)",
                      border: "1px solid var(--border-dim)",
                      color: "var(--text-secondary)",
                      outline: "none",
                      fontFamily: "inherit",
                    }}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main HITLCard component ───────────────────────────────────────────────────

export default function HITLCard({
  runId,
  checkpoint,
  subtasks: initialSubtasks = [],
  implementationPlan: initialPlan = [],
  pat,
  onApproved,
  onStopped,
}: HITLCardProps) {
  const [editedSubtasks, setEditedSubtasks] =
    useState<Subtask[]>(initialSubtasks);
  const [editedPlan, setEditedPlan] =
    useState<ImplementationPlanItem[]>(initialPlan);
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Prevent double-fire: if a request is already in-flight, block new ones
  const approveInFlightRef = useRef(false);

  const isHitl1 = checkpoint === "hitl_1";

  useEffect(() => {
    if (initialSubtasks.length === 0) return;
    setEditedSubtasks((prev) => (prev.length === 0 ? initialSubtasks : prev));
  }, [initialSubtasks]);

  useEffect(() => {
    if (initialPlan.length === 0) return;
    setEditedPlan((prev) => (prev.length === 0 ? initialPlan : prev));
  }, [initialPlan]);

  async function handleApprove() {
    if (approveInFlightRef.current || loading || submitted) return;
    if (!pat) {
      setError("GitHub PAT is required to approve. Re-enter it in the form.");
      return;
    }

    approveInFlightRef.current = true;
    setError(null);
    setLoading(true);

    try {
      await approveRun(runId, {
        checkpoint,
        action: "approve",
        subtasks: isHitl1 ? editedSubtasks : null,
        implementation_plan: !isHitl1 ? editedPlan : null,
        github_token: pat,
      });
      setSubmitted(true);
      onApproved(checkpoint);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve");
    } finally {
      setLoading(false);
      approveInFlightRef.current = false;
    }
  }

  async function handleStop() {
    if (approveInFlightRef.current || loading || submitted) return;
    setError(null);
    setLoading(true);
    try {
      await approveRun(runId, {
        checkpoint,
        action: "stop",
        subtasks: null,
        implementation_plan: null,
        github_token: pat,
      });
      setSubmitted(true);
      onApproved(checkpoint);
      onStopped();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop run");
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return null;
  }

  return (
    <div
      className="rounded-xl p-4 animate-lime-pulse animate-fade-slide-up"
      style={{
        backgroundColor: "var(--bg-card)",
        backdropFilter: "var(--glass-blur)",
        border: "2px solid var(--hitl-border)",
        boxShadow:
          "0 0 20px var(--hitl-glow), inset 0 0 20px rgba(163, 230, 53, 0.03)",
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <Pencil size={14} style={{ color: "var(--hitl-border)" }} />
        <span
          className="text-sm font-semibold"
          style={{ color: "var(--accent-lime)" }}
        >
          {isHitl1
            ? "Checkpoint 1 of 2 — Subtask Review"
            : "Checkpoint 2 of 2 — Implementation Plan"}
        </span>
      </div>

      <p className="text-xs mb-4" style={{ color: "var(--text-secondary)" }}>
        {isHitl1
          ? "First review gate: edit the subtask list, then approve to start file mapping. A second review happens after the implementation plan."
          : "Second review gate: this is the file-level plan, not the subtask list. Approve to run the target repository's tests."}
      </p>

      {isHitl1 && editedSubtasks.length === 0 && (
        <p className="text-xs mb-3" style={{ color: "var(--text-dim)" }}>
          Loading subtasks from the planner…
        </p>
      )}

      {/* Editor */}
      <div className="mb-4 max-h-80 overflow-y-auto">
        {isHitl1 ? (
          <SubtaskEditor
            subtasks={editedSubtasks}
            onChange={setEditedSubtasks}
          />
        ) : (
          <PlanEditor plan={editedPlan} onChange={setEditedPlan} />
        )}
      </div>

      {/* Error */}
      {error && (
        <div
          className="flex items-start gap-2 mb-3 p-2 rounded-lg text-xs w-full min-w-0 overflow-hidden"
          style={{
            backgroundColor: "rgba(248, 113, 113, 0.08)",
            border: "1px solid rgba(248, 113, 113, 0.25)",
            color: "var(--status-error)",
          }}
        >
          <AlertCircle size={12} className="flex-shrink-0 mt-0.5" />
          <p
            className="min-w-0 flex-1 whitespace-pre-wrap break-all"
            style={{ overflowWrap: "anywhere" }}
          >
            {error}
          </p>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          loading={loading}
          disabled={
            loading ||
            submitted ||
            !pat ||
            (isHitl1 && editedSubtasks.length === 0)
          }
          onClick={handleApprove}
          className="flex-1"
        >
          <CheckCheck size={13} />
          {isHitl1 ? "Approve & Continue" : "Approve & Execute Tests"}
        </Button>
        <Button
          variant="danger"
          size="sm"
          disabled={loading || submitted}
          onClick={handleStop}
        >
          <XCircle size={13} />
          Stop
        </Button>
      </div>
      {!pat && (
        <p className="text-xs mt-2" style={{ color: "var(--text-dim)" }}>
          Re-enter your GitHub PAT in the left panel to enable Approve.
        </p>
      )}
    </div>
  );
}
