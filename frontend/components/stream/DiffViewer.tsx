"use client";

/**
 * DiffViewer — file-level navigation for a unified diff.
 * Line-level highlight is best-effort when finding lines exist; never fabricated.
 */

import { useMemo, useState } from "react";

import type { PatchFileOperation, ReviewerFinding } from "@/lib/types";

export interface DiffViewerProps {
  unifiedDiff: string | null | undefined;
  modifiedFiles?: PatchFileOperation[];
  findings?: ReviewerFinding[];
  activeFile?: string | null;
  onActiveFileChange?: (path: string) => void;
}

function splitDiffByFile(diff: string): Map<string, string> {
  const map = new Map<string, string>();
  if (!diff.trim()) return map;
  const chunks = diff.split(/(?=^diff --git )/m).filter(Boolean);
  for (const chunk of chunks) {
    const gitLine = chunk.split("\n").find((l) => l.startsWith("diff --git "));
    let path = "";
    if (gitLine) {
      const parts = gitLine.replace("diff --git ", "").split(" ");
      const right = parts.find((p) => p.startsWith("b/")) ?? parts[parts.length - 1];
      path = right.replace(/^b\//, "");
    }
    if (!path) {
      const plus = chunk.split("\n").find((l) => l.startsWith("+++ "));
      if (plus) path = plus.replace(/^\+\+\+\s+[ab]\//, "").trim();
    }
    if (path) map.set(path, chunk);
  }
  if (map.size === 0) map.set("(patch)", diff);
  return map;
}

export function DiffViewer({
  unifiedDiff,
  modifiedFiles = [],
  findings = [],
  activeFile = null,
  onActiveFileChange,
}: DiffViewerProps) {
  const byFile = useMemo(
    () => splitDiffByFile(unifiedDiff ?? ""),
    [unifiedDiff],
  );
  const files = useMemo(() => {
    const fromDiff = Array.from(byFile.keys());
    const fromMeta = modifiedFiles.map((f) => f.path).filter(Boolean);
    return Array.from(new Set([...fromMeta, ...fromDiff]));
  }, [byFile, modifiedFiles]);

  const [internalActive, setInternalActive] = useState<string | null>(null);
  const selected = activeFile ?? internalActive ?? files[0] ?? null;

  function selectFile(path: string) {
    setInternalActive(path);
    onActiveFileChange?.(path);
  }

  const fileFindings = findings.filter((f) => f.file_path === selected);

  if (!files.length) {
    return (
      <p className="text-sm text-zinc-500">No patch diff available.</p>
    );
  }

  return (
    <div className="grid gap-3 md:grid-cols-[220px_1fr]">
      <nav className="rounded border border-zinc-800 bg-zinc-950/60 p-2">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Modified files
        </p>
        <ul className="space-y-1">
          {files.map((path) => {
            const count = findings.filter((f) => f.file_path === path).length;
            const active = path === selected;
            return (
              <li key={path}>
                <button
                  type="button"
                  onClick={() => selectFile(path)}
                  className={`w-full rounded px-2 py-1 text-left font-mono text-xs ${
                    active
                      ? "bg-emerald-900/40 text-emerald-200"
                      : "text-zinc-300 hover:bg-zinc-900"
                  }`}
                >
                  <span className="break-all">{path}</span>
                  {count > 0 ? (
                    <span className="ml-2 text-[10px] text-amber-400">
                      {count}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="min-w-0 space-y-2">
        {fileFindings.length > 0 ? (
          <div className="rounded border border-amber-900/50 bg-amber-950/20 p-2 text-xs text-amber-100">
            {fileFindings.map((f, i) => (
              <div key={`${f.file_path}-${i}`} className="mb-1 last:mb-0">
                <span className="font-semibold uppercase">{f.severity}</span>
                {": "}
                {f.description}
                {f.line_start != null ? (
                  <span className="text-zinc-400">
                    {" "}
                    (line {f.line_start}
                    {f.line_end != null && f.line_end !== f.line_start
                      ? `-${f.line_end}`
                      : ""}
                    )
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
        <pre className="overflow-x-auto rounded border border-zinc-800 bg-black/40 p-3 font-mono text-xs leading-5 text-zinc-200">
          {(byFile.get(selected ?? "") ?? unifiedDiff ?? "").split("\n").map(
            (line, idx) => {
              const tone = line.startsWith("+")
                ? "text-emerald-300"
                : line.startsWith("-")
                  ? "text-rose-300"
                  : line.startsWith("@@")
                    ? "text-sky-300"
                    : "text-zinc-300";
              return (
                <div key={idx} className={tone}>
                  {line || " "}
                </div>
              );
            },
          )}
        </pre>
      </div>
    </div>
  );
}
