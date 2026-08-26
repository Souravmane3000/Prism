/**
 * components/input/RunForm.tsx — Pipeline run input form.
 *
 * Fields:
 *   - Repository URL (validated: must contain github.com)
 *   - Issue input toggle: URL or paste text (at least one required)
 *   - GitHub PAT (type="password", ephemeral React state only)
 *
 * Security rules:
 *   - PAT is NEVER written to localStorage, Supabase, or any log
 *   - PAT is held only in React component state
 *   - SessionRecord stored in localStorage excludes the PAT
 *
 * On submit → calls startRun() → calls onRunStarted(runId, pat)
 */

"use client";

import { useEffect, useState, type FormEvent } from "react";
import { GitBranch, FileText, Link, Lock, Play, AlertCircle } from "lucide-react";
import Button from "@/components/ui/Button";
import { startRun } from "@/lib/api";
import type { SessionRecord } from "@/lib/types";

interface RunFormProps {
  onRunStarted: (runId: string, pat: string) => void;
  onPatChange?: (pat: string) => void;
}

type IssueMode = "url" | "text";

function inputStyle(hasError?: boolean) {
  return {
    width: "100%",
    backgroundColor: "var(--bg-input)",
    border: `1px solid ${hasError ? "var(--status-error)" : "var(--border-dim)"}`,
    borderRadius: "6px",
    padding: "8px 10px",
    fontSize: "0.8rem",
    color: "var(--text-primary)",
    outline: "none",
    transition: "border-color 0.15s",
    fontFamily: "inherit",
  } as React.CSSProperties;
}

function labelStyle() {
  return {
    display: "block",
    fontSize: "0.7rem",
    fontWeight: 500,
    color: "var(--text-secondary)",
    marginBottom: "4px",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  };
}

export default function RunForm({ onRunStarted, onPatChange }: RunFormProps) {
  const [repoUrl, setRepoUrl] = useState("");
  const [issueMode, setIssueMode] = useState<IssueMode>("url");
  const [issueUrl, setIssueUrl] = useState("");
  const [issueText, setIssueText] = useState("");
  const [pat, setPat] = useState(""); // ephemeral — never persisted
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState<string | null>(null);

  useEffect(() => {
    if (pat) onPatChange?.(pat);
    // Sync once so a PAT already typed in the form enables Create GitHub PR.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function validate(): boolean {
    const newErrors: Record<string, string> = {};

    if (!repoUrl.trim()) {
      newErrors.repoUrl = "Repository URL is required";
    } else if (!repoUrl.includes("github.com")) {
      newErrors.repoUrl = "Must be a GitHub repository URL";
    }

    if (issueMode === "url" && !issueUrl.trim()) {
      newErrors.issue = "Issue URL is required";
    } else if (issueMode === "url" && !issueUrl.includes("github.com")) {
      newErrors.issue = "Must be a GitHub issue URL";
    } else if (issueMode === "text" && !issueText.trim()) {
      newErrors.issue = "Issue text is required";
    }

    if (!pat.trim()) {
      newErrors.pat = "GitHub PAT is required";
    } else if (pat.trim().length < 10) {
      newErrors.pat = "PAT appears too short";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setApiError(null);

    if (!validate()) return;

    setLoading(true);
    try {
      const res = await startRun({
        repo_url: repoUrl.trim().replace(/\/$/, ""),
        issue_url: issueMode === "url" ? issueUrl.trim() : null,
        issue_text: issueMode === "text" ? issueText.trim() : null,
        github_token: pat.trim(), // in-flight only
      });

      // Store SessionRecord to localStorage — PAT is intentionally excluded
      const record: SessionRecord = {
        run_id: res.run_id,
        repo_url: repoUrl.trim(),
        created_at: new Date().toISOString(),
        status: res.status,
      };
      try {
        const existing = JSON.parse(
          localStorage.getItem("prism_sessions") ?? "[]",
        ) as SessionRecord[];
        localStorage.setItem(
          "prism_sessions",
          JSON.stringify([record, ...existing].slice(0, 20)),
        );
      } catch {
        // localStorage not critical — swallow silently
      }

      // Keep PAT in memory for HITL approve + Create GitHub PR. Never persist it.
      onRunStarted(res.run_id, pat.trim());
      setIssueUrl("");
      setIssueText("");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to start run";
      setApiError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      {/* Prism header */}
      <div className="mb-1">
        <h2
          className="text-base font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          New Run
        </h2>
        <p className="text-sm mt-0.5" style={{ color: "var(--text-dim)" }}>
          Connect a repo and issue to start the pipeline.
        </p>
      </div>

      {/* Repository URL */}
      <div>
        <label style={labelStyle()}>
          <GitBranch size={9} className="inline mr-1" />
          Repository URL
        </label>
        <input
          type="url"
          placeholder="https://github.com/owner/repo"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          style={inputStyle(!!errors.repoUrl)}
          onFocus={(e) => {
            (e.target as HTMLInputElement).style.borderColor =
              "var(--accent-lime)";
          }}
          onBlur={(e) => {
            (e.target as HTMLInputElement).style.borderColor = errors.repoUrl
              ? "var(--status-error)"
              : "var(--border-dim)";
          }}
        />
        {errors.repoUrl && (
          <p
            className="text-xs mt-1 flex items-center gap-1"
            style={{ color: "var(--status-error)" }}
          >
            <AlertCircle size={10} />
            {errors.repoUrl}
          </p>
        )}
      </div>

      {/* Issue input toggle */}
      <div>
        <label style={labelStyle()}>
          <FileText size={9} className="inline mr-1" />
          Issue
        </label>

        {/* Mode toggle */}
        <div
          className="flex rounded-md overflow-hidden mb-2"
          style={{ border: "1px solid var(--border-dim)" }}
        >
          {(["url", "text"] as IssueMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setIssueMode(mode)}
              className="flex-1 py-1 text-xs flex items-center justify-center gap-1 transition-all duration-150"
              style={{
                backgroundColor:
                  issueMode === mode ? "var(--bg-active)" : "transparent",
                color:
                  issueMode === mode
                    ? "var(--accent-lime)"
                    : "var(--text-dim)",
                border: "none",
                cursor: "pointer",
              }}
            >
              {mode === "url" ? <Link size={9} /> : <FileText size={9} />}
              {mode === "url" ? "URL" : "Paste"}
            </button>
          ))}
        </div>

        {issueMode === "url" ? (
          <input
            type="url"
            placeholder="https://github.com/owner/repo/issues/42"
            value={issueUrl}
            onChange={(e) => setIssueUrl(e.target.value)}
            style={inputStyle(!!errors.issue)}
            onFocus={(e) => {
              (e.target as HTMLInputElement).style.borderColor =
                "var(--accent-lime)";
            }}
            onBlur={(e) => {
              (e.target as HTMLInputElement).style.borderColor = errors.issue
                ? "var(--status-error)"
                : "var(--border-dim)";
            }}
          />
        ) : (
          <textarea
            placeholder="Paste issue body text here..."
            value={issueText}
            onChange={(e) => setIssueText(e.target.value)}
            rows={4}
            style={{
              ...inputStyle(!!errors.issue),
              resize: "none",
              minHeight: "80px",
              maxHeight: "120px",
              overflowY: "auto",
            }}
            onFocus={(e) => {
              (e.target as HTMLTextAreaElement).style.borderColor =
                "var(--accent-lime)";
            }}
            onBlur={(e) => {
              (e.target as HTMLTextAreaElement).style.borderColor =
                errors.issue ? "var(--status-error)" : "var(--border-dim)";
            }}
          />
        )}

        {errors.issue && (
          <p
            className="text-xs mt-1 flex items-center gap-1"
            style={{ color: "var(--status-error)" }}
          >
            <AlertCircle size={10} />
            {errors.issue}
          </p>
        )}
      </div>

      {/* GitHub PAT */}
      <div>
        <label style={labelStyle()}>
          <Lock size={9} className="inline mr-1" />
          GitHub PAT
        </label>
        <input
          type="password"
          placeholder="ghp_••••••••••••••••••••"
          value={pat}
          onChange={(e) => {
            const next = e.target.value;
            setPat(next);
            onPatChange?.(next);
          }}
          autoComplete="off"
          style={inputStyle(!!errors.pat)}
          onFocus={(e) => {
            (e.target as HTMLInputElement).style.borderColor =
              "var(--accent-lime)";
          }}
          onBlur={(e) => {
            (e.target as HTMLInputElement).style.borderColor = errors.pat
              ? "var(--status-error)"
              : "var(--border-dim)";
          }}
        />
        {errors.pat && (
          <p
            className="text-xs mt-1 flex items-center gap-1"
            style={{ color: "var(--status-error)" }}
          >
            <AlertCircle size={10} />
            {errors.pat}
          </p>
        )}
        <p className="text-xs mt-1" style={{ color: "var(--text-dim)" }}>
          Held in memory only — never stored or logged.
        </p>
      </div>

      {/* API error */}
      {apiError && (
        <div
          className="flex items-start gap-2 p-2.5 rounded-lg text-xs"
          style={{
            backgroundColor: "rgba(248, 113, 113, 0.08)",
            border: "1px solid rgba(248, 113, 113, 0.25)",
            color: "var(--status-error)",
          }}
        >
          <AlertCircle size={12} className="mt-0.5 flex-shrink-0" />
          {apiError}
        </div>
      )}

      {/* Submit */}
      <Button
        type="submit"
        variant="primary"
        size="md"
        loading={loading}
        disabled={loading}
        className="w-full mt-1"
      >
        <Play size={13} />
        {loading ? "Starting..." : "Start Run"}
      </Button>
    </form>
  );
}
