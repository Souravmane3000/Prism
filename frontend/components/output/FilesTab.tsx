/**
 * components/output/FilesTab.tsx — Code file map viewer.
 *
 * Renders file_map grouped by subtask_id.
 * Each entry shows: file path, relevance score bar, source badge.
 * Source badge: pgvector | github | both
 */

"use client";

import { useState } from "react";
import { FileCode, ChevronDown, ChevronRight } from "lucide-react";
import type { FileMapEntry } from "@/lib/types";

interface FilesTabProps {
  fileMap: Record<string, FileMapEntry[]>;
}

const SOURCE_STYLES: Record<
  string,
  { label: string; color: string; bg: string }
> = {
  pgvector: {
    label: "Semantic",
    color: "var(--accent-lime)",
    bg: "rgba(163, 230, 53, 0.1)",
  },
  github: {
    label: "GitHub",
    color: "var(--status-awaiting)",
    bg: "rgba(251, 191, 36, 0.1)",
  },
  both: {
    label: "Both",
    color: "var(--status-complete)",
    bg: "rgba(52, 211, 153, 0.1)",
  },
};

function scoreWidth(score: number): string {
  return `${Math.min(100, Math.round(score * 100))}%`;
}

function scoreColor(score: number): string {
  if (score > 0.75) return "var(--status-complete)";
  if (score > 0.4) return "var(--accent-lime)";
  return "var(--status-awaiting)";
}

export default function FilesTab({ fileMap }: FilesTabProps) {
  const subtaskIds = Object.keys(fileMap);
  const [expanded, setExpanded] = useState<Set<string>>(
    new Set(subtaskIds.slice(0, 2)),
  );

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  if (subtaskIds.length === 0) {
    return (
      <div
        className="flex items-center justify-center h-full p-6"
        style={{ color: "var(--text-dim)" }}
      >
        <p className="text-xs text-center">
          Code Navigator has not completed yet. File mappings will appear here.
        </p>
      </div>
    );
  }

  const totalFiles = Object.values(fileMap).reduce(
    (acc, arr) => acc + arr.length,
    0,
  );

  return (
    <div className="p-4 flex flex-col gap-2">
      <p className="text-xs mb-2" style={{ color: "var(--text-dim)" }}>
        {totalFiles} file{totalFiles !== 1 ? "s" : ""} across{" "}
        {subtaskIds.length} subtask{subtaskIds.length !== 1 ? "s" : ""}
      </p>

      {subtaskIds.map((subtaskId) => {
        const files = fileMap[subtaskId] ?? [];
        const isOpen = expanded.has(subtaskId);

        return (
          <div
            key={subtaskId}
            className="rounded-xl overflow-hidden"
            style={{
              border: "1px solid var(--border-subtle)",
              backgroundColor: "var(--bg-card)",
              backdropFilter: "var(--glass-blur)",
            }}
          >
            {/* Header */}
            <button
              onClick={() => toggle(subtaskId)}
              className="w-full flex items-center gap-2 px-4 py-3 text-left"
              style={{ background: "transparent", border: "none", cursor: "pointer" }}
            >
              <span style={{ color: "var(--text-dim)" }}>
                {isOpen ? (
                  <ChevronDown size={13} />
                ) : (
                  <ChevronRight size={13} />
                )}
              </span>
              <span
                className="text-xs font-medium flex-1"
                style={{ color: "var(--text-primary)" }}
              >
                {subtaskId}
              </span>
              <span
                className="text-xs px-1.5 py-0.5 rounded"
                style={{
                  color: "var(--text-dim)",
                  backgroundColor: "var(--bg-hover)",
                }}
              >
                {files.length} file{files.length !== 1 ? "s" : ""}
              </span>
            </button>

            {/* File list */}
            {isOpen && files.length > 0 && (
              <div
                className="border-t"
                style={{ borderColor: "var(--border-dim)" }}
              >
                {files
                  .sort((a, b) => b.relevance_score - a.relevance_score)
                  .map((entry) => {
                    const src =
                      SOURCE_STYLES[entry.source] ?? SOURCE_STYLES.pgvector;
                    return (
                      <div
                        key={entry.path}
                        className="px-4 py-2.5 flex flex-col gap-1.5"
                        style={{
                          borderBottom: "1px solid var(--border-dim)",
                        }}
                      >
                        {/* Path + source */}
                        <div className="flex items-center gap-2">
                          <FileCode
                            size={11}
                            style={{
                              color: "var(--text-dim)",
                              flexShrink: 0,
                            }}
                          />
                          <span
                            className="text-xs font-mono flex-1 truncate"
                            style={{ color: "var(--text-secondary)" }}
                          >
                            {entry.path}
                          </span>
                          <span
                            className="text-xs px-1.5 py-0.5 rounded-full flex-shrink-0"
                            style={{
                              color: src.color,
                              backgroundColor: src.bg,
                              fontSize: "10px",
                            }}
                          >
                            {src.label}
                          </span>
                        </div>

                        {/* Relevance bar */}
                        <div className="flex items-center gap-2">
                          <div
                            className="flex-1 h-0.5 rounded-full overflow-hidden"
                            style={{ backgroundColor: "var(--border-dim)" }}
                          >
                            <div
                              className="h-full rounded-full transition-all duration-500"
                              style={{
                                width: scoreWidth(entry.relevance_score),
                                backgroundColor: scoreColor(
                                  entry.relevance_score,
                                ),
                              }}
                            />
                          </div>
                          <span
                            className="text-xs font-mono flex-shrink-0"
                            style={{ color: "var(--text-dim)", fontSize: "10px" }}
                          >
                            {(entry.relevance_score * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
