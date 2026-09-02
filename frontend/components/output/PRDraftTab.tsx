/**
 * components/output/PRDraftTab.tsx — PR draft preview + GitHub push trigger.
 *
 * Renders pr_draft fields:
 *   - Title as heading
 *   - Body via react-markdown
 *   - Collapsible sections: What Changed, Why, Testing Notes, Limitations
 *   - Review checklist (interactive checkboxes)
 *   - "Create GitHub PR" button when run is completed
 *
 * GitHub PAT is passed from parent — in-flight only, never stored.
 */

"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  GitPullRequestCreate,
  CheckSquare,
  Square,
  AlertCircle,
} from "lucide-react";
import Button from "@/components/ui/Button";
import { ApiError, createPR } from "@/lib/api";
import type { PRDraft, RunStatus } from "@/lib/types";

function formatCreatePRError(err: unknown): string {
  if (err instanceof ApiError) {
    const raw = err.message;
    const looksLikeRawGitHub =
      /documentation_url|create-a-reference/.test(raw) ||
      (/\b404\b/.test(raw) && raw.includes("{"));
    if (looksLikeRawGitHub) {
      return (
        "GitHub could not create the branch. The current PAT cannot write to this " +
        "repository (not a repo you own, or missing repo scope). The PR draft stays " +
        "here — Prism cannot open a pull request without push access."
      );
    }
    return raw;
  }
  return err instanceof Error ? err.message : "Failed to create PR";
}

interface PRDraftTabProps {
  prDraft: PRDraft | null;
  runId: string | null;
  runStatus: RunStatus | null;
  prUrl: string | null;
  pat: string;
}

function Section({
  title,
  content,
}: {
  title: string;
  content: string;
}) {
  const [open, setOpen] = useState(false);
  if (!content) return null;

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        border: "1px solid var(--border-dim)",
        backgroundColor: "rgba(10, 13, 8, 0.3)",
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-left"
        style={{ background: "transparent", border: "none", cursor: "pointer" }}
      >
        <span style={{ color: "var(--text-dim)" }}>
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
        <span
          className="text-xs font-medium uppercase tracking-wider"
          style={{ color: "var(--text-secondary)" }}
        >
          {title}
        </span>
      </button>
      {open && (
        <div
          className="px-4 pb-3"
          style={{ borderTop: "1px solid var(--border-dim)" }}
        >
          <p
            className="text-xs pt-3"
            style={{ color: "var(--text-secondary)", lineHeight: "1.7" }}
          >
            {content}
          </p>
        </div>
      )}
    </div>
  );
}

export default function PRDraftTab({
  prDraft,
  runId,
  runStatus,
  prUrl,
  pat,
}: PRDraftTabProps) {
  const [checkedItems, setCheckedItems] = useState<Set<number>>(new Set());
  const [creating, setCreating] = useState(false);
  const [createdUrl, setCreatedUrl] = useState<string | null>(prUrl);
  const [error, setError] = useState<string | null>(null);

  function toggleChecked(i: number) {
    setCheckedItems((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  async function handleCreatePR() {
    if (!runId || !pat) return;
    setError(null);
    setCreating(true);
    try {
      const res = await createPR(runId, {
        github_token: pat,
      });
      setCreatedUrl(res.pr_url);
    } catch (err) {
      setError(formatCreatePRError(err));
    } finally {
      setCreating(false);
    }
  }

  if (!prDraft) {
    return (
      <div
        className="flex items-center justify-center h-full p-6"
        style={{ color: "var(--text-dim)" }}
      >
        <p className="text-xs text-center">
          PR Summarizer has not completed yet. The draft will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-3 overflow-y-auto overflow-x-hidden min-w-0">
      {/* PR Title */}
      <div>
        <h3
          className="text-sm font-semibold leading-snug"
          style={{ color: "var(--text-primary)" }}
        >
          {prDraft.title}
        </h3>
      </div>

      {/* PR Body (markdown) */}
      <div
        className="rounded-xl p-4 text-xs"
        style={{
          backgroundColor: "var(--bg-card)",
          border: "1px solid var(--border-subtle)",
          color: "var(--text-secondary)",
          lineHeight: "1.7",
        }}
      >
        <ReactMarkdown
          components={{
            h1: ({ children }) => (
              <h1
                className="text-sm font-semibold mb-2"
                style={{ color: "var(--text-primary)" }}
              >
                {children}
              </h1>
            ),
            h2: ({ children }) => (
              <h2
                className="text-xs font-semibold mb-1.5 uppercase tracking-wider"
                style={{ color: "var(--text-dim)" }}
              >
                {children}
              </h2>
            ),
            p: ({ children }) => (
              <p className="mb-2" style={{ color: "var(--text-secondary)" }}>
                {children}
              </p>
            ),
            code: ({ children }) => (
              <code
                className="px-1 py-0.5 rounded text-xs font-mono"
                style={{
                  backgroundColor: "rgba(163, 230, 53, 0.08)",
                  color: "var(--accent-lime)",
                  border: "1px solid var(--border-subtle)",
                }}
              >
                {children}
              </code>
            ),
          }}
        >
          {prDraft.body ?? ""}
        </ReactMarkdown>
      </div>

      {/* Collapsible sections */}
      <Section title="What Changed" content={prDraft.what_changed ?? ""} />
      <Section title="Why" content={prDraft.why ?? ""} />
      <Section title="Testing Notes" content={prDraft.testing_notes ?? ""} />
      <Section title="Known Limitations" content={prDraft.limitations ?? ""} />

      {/* Review checklist */}
      {(prDraft.review_checklist?.length ?? 0) > 0 && (
        <div
          className="rounded-xl p-4"
          style={{
            border: "1px solid var(--border-subtle)",
            backgroundColor: "var(--bg-card)",
          }}
        >
          <p
            className="text-xs font-medium uppercase tracking-wider mb-3"
            style={{ color: "var(--text-dim)" }}
          >
            Review Checklist
          </p>
          <div className="flex flex-col gap-2">
            {(prDraft.review_checklist ?? []).map((item, i) => (
              <button
                key={i}
                onClick={() => toggleChecked(i)}
                className="flex items-start gap-2 text-left w-full"
                style={{ background: "transparent", border: "none", cursor: "pointer" }}
              >
                <span
                  className="mt-0.5 flex-shrink-0"
                  style={{
                    color: checkedItems.has(i)
                      ? "var(--status-complete)"
                      : "var(--text-dim)",
                  }}
                >
                  {checkedItems.has(i) ? (
                    <CheckSquare size={12} />
                  ) : (
                    <Square size={12} />
                  )}
                </span>
                <span
                  className="text-xs"
                  style={{
                    color: checkedItems.has(i)
                      ? "var(--text-dim)"
                      : "var(--text-secondary)",
                    textDecoration: checkedItems.has(i)
                      ? "line-through"
                      : "none",
                  }}
                >
                  {item}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* PR creation */}
      {error && (
        <div
          className="flex items-start gap-2 p-2.5 rounded-lg text-xs w-full min-w-0 overflow-hidden"
          style={{
            backgroundColor: "rgba(248, 113, 113, 0.08)",
            border: "1px solid rgba(248, 113, 113, 0.25)",
            color: "var(--status-error)",
          }}
        >
          <AlertCircle size={12} className="flex-shrink-0 mt-0.5" />
          <p
            className="min-w-0 flex-1 whitespace-pre-wrap"
            style={{ overflowWrap: "break-word", wordBreak: "normal" }}
          >
            {error}
          </p>
        </div>
      )}

      {createdUrl ? (
        <a
          href={createdUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-xs font-medium transition-all duration-200"
          style={{
            backgroundColor: "var(--bg-active)",
            border: "1px solid var(--border-glow)",
            color: "var(--accent-lime)",
          }}
        >
          <GitPullRequestCreate size={13} />
          View PR on GitHub
          <ExternalLink size={11} className="ml-auto" />
        </a>
      ) : (
        <Button
          variant="primary"
          size="md"
          loading={creating}
          disabled={runStatus !== "completed" || !pat}
          onClick={handleCreatePR}
          className="w-full"
        >
          <GitPullRequestCreate size={13} />
          {creating ? "Creating PR..." : "Create GitHub PR"}
        </Button>
      )}

      {runStatus === "completed" && !createdUrl && (
        <p className="text-xs text-center" style={{ color: "var(--text-dim)" }}>
          Creating a GitHub PR needs push access on this repository. A PAT without
          write permission cannot open a PR on someone else&apos;s repo; the draft
          stays here.
        </p>
      )}

      {runStatus !== "completed" && !createdUrl && (
        <p className="text-xs text-center" style={{ color: "var(--text-dim)" }}>
          PR creation available after pipeline completes.
        </p>
      )}
      {runStatus === "completed" && !createdUrl && !pat && (
        <p className="text-xs text-center" style={{ color: "var(--text-dim)" }}>
          Enter your GitHub PAT in the left panel to enable Create GitHub PR.
          No extra GitHub app or account setup is required.
        </p>
      )}
    </div>
  );
}
