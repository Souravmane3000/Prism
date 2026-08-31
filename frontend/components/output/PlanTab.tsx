/**
 * components/output/PlanTab.tsx — Subtask breakdown renderer.
 *
 * Renders planner subtasks as an accordion list.
 * Fields: ID, title, description, complexity badge, dependencies, likely_files chips.
 */

"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileCode, ArrowRight } from "lucide-react";
import type { Subtask } from "@/lib/types";

interface PlanTabProps {
  subtasks: Subtask[];
}

const COMPLEXITY_STYLES: Record<
  string,
  { color: string; bg: string; border: string }
> = {
  low: {
    color: "var(--complexity-low)",
    bg: "rgba(52, 211, 153, 0.08)",
    border: "rgba(52, 211, 153, 0.25)",
  },
  medium: {
    color: "var(--complexity-medium)",
    bg: "rgba(251, 191, 36, 0.08)",
    border: "rgba(251, 191, 36, 0.25)",
  },
  high: {
    color: "var(--complexity-high)",
    bg: "rgba(248, 113, 113, 0.08)",
    border: "rgba(248, 113, 113, 0.25)",
  },
};

export default function PlanTab({ subtasks }: PlanTabProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set([0]));

  function toggle(i: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  if (subtasks.length === 0) {
    return (
      <div
        className="flex items-center justify-center h-full p-6"
        style={{ color: "var(--text-dim)" }}
      >
        <p className="text-sm text-center">
          Planner has not completed yet. Subtasks will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-2">
      <p
        className="text-xs mb-2"
        style={{ color: "var(--text-dim)" }}
      >
        {subtasks.length} subtask{subtasks.length !== 1 ? "s" : ""} planned
      </p>

      {subtasks.map((st, i) => {
        const cs = COMPLEXITY_STYLES[st.complexity] ?? COMPLEXITY_STYLES.medium;
        const isOpen = expanded.has(i);

        return (
          <div
            key={st.id}
            className="rounded-xl overflow-hidden transition-all duration-200"
            style={{
              border: "1px solid var(--border-subtle)",
              backgroundColor: "var(--bg-card)",
              backdropFilter: "var(--glass-blur)",
            }}
          >
            {/* Header */}
            <button
              onClick={() => toggle(i)}
              className="w-full flex items-center gap-2.5 px-4 py-3 text-left"
              style={{ background: "transparent", border: "none", cursor: "pointer" }}
            >
              <span style={{ color: "var(--text-dim)", flexShrink: 0 }}>
                {isOpen ? (
                  <ChevronDown size={13} />
                ) : (
                  <ChevronRight size={13} />
                )}
              </span>

              {/* Complexity badge */}
              <span
                className="text-xs font-medium px-2 py-0.5 rounded-full flex-shrink-0"
                style={{
                  color: cs.color,
                  backgroundColor: cs.bg,
                  border: `1px solid ${cs.border}`,
                }}
              >
                {(st.complexity ?? "medium").toUpperCase()}
              </span>

              <span
                className="text-sm font-medium flex-1 truncate"
                style={{ color: "var(--text-primary)" }}
              >
                {st.title}
              </span>

              <span
                className="text-xs font-mono flex-shrink-0"
                style={{ color: "var(--text-dim)" }}
              >
                {st.id}
              </span>
            </button>

            {/* Body */}
            {isOpen && (
              <div
                className="px-4 pb-4 flex flex-col gap-3"
                style={{ borderTop: "1px solid var(--border-dim)" }}
              >
                {/* Description */}
                <p
                  className="text-xs pt-3"
                  style={{
                    color: "var(--text-secondary)",
                    lineHeight: "1.6",
                  }}
                >
                  {st.description}
                </p>

                {/* Dependencies */}
                {(st.dependencies?.length ?? 0) > 0 && (
                  <div>
                    <p
                      className="text-xs font-medium mb-1.5 uppercase tracking-wider"
                      style={{ color: "var(--text-dim)" }}
                    >
                      Depends on
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {(st.dependencies ?? []).map((dep) => (
                        <span
                          key={dep}
                          className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full"
                          style={{
                            color: "var(--accent-lime)",
                            backgroundColor: "var(--accent-glow)",
                            border: "1px solid var(--border-subtle)",
                          }}
                        >
                          <ArrowRight size={9} />
                          {dep}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Likely files */}
                {(st.likely_files?.length ?? 0) > 0 && (
                  <div>
                    <p
                      className="text-xs font-medium mb-1.5 uppercase tracking-wider"
                      style={{ color: "var(--text-dim)" }}
                    >
                      Likely files
                    </p>
                    <div className="flex flex-col gap-1">
                      {(st.likely_files ?? []).map((file) => (
                        <span
                          key={file}
                          className="flex items-center gap-1.5 text-xs font-mono"
                          style={{ color: "var(--text-secondary)" }}
                        >
                          <FileCode size={10} style={{ flexShrink: 0, color: "var(--text-dim)" }} />
                          {file}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
