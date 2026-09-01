/**
 * lib/api.ts — FastAPI REST client for Prism backend.
 *
 * RULE: All fetch() calls in the frontend go through this module.
 *       No fetch() calls are permitted in React components.
 *       All endpoints target the FastAPI routes defined in API.md.
 *
 * Timeout: 15 seconds on most REST calls. POST /start allows 45 seconds for
 * Modal cold start; the graph itself must not run inside that request.
 * Errors: Throws ApiError with structured detail from the backend envelope.
 */

import type {
  ApproveRunRequest,
  ApproveRunResponse,
  ApiErrorDetail,
  CreatePRRequest,
  CreatePRResponse,
  RunOutputResponse,
  RunStatusResponse,
  StartRunRequest,
  StartRunResponse,
} from "@/lib/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const REQUEST_TIMEOUT_MS = 15_000;
const START_TIMEOUT_MS = 45_000;

// ── Error class ──────────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly code: string;
  readonly runId: string | null;
  readonly details: Record<string, unknown>;
  readonly httpStatus: number;

  constructor(detail: ApiErrorDetail, httpStatus: number) {
    super(detail.message);
    this.name = "ApiError";
    this.code = detail.code;
    this.runId = detail.run_id;
    this.details = detail.details;
    this.httpStatus = httpStatus;
  }
}

// ── Core request helper ──────────────────────────────────────────────────────

async function request<T>(
  path: string,
  options?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs = REQUEST_TIMEOUT_MS, ...fetchOptions } = options ?? {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...fetchOptions,
      signal: controller.signal,
    });

    if (!res.ok) {
      let errorBody: Record<string, unknown> = {};
      try {
        errorBody = await res.json();
      } catch {
        // body is not JSON — fall through to generic error below
      }
      // FastAPI raises HTTPException with detail=ErrorResponse.model_dump() which
      // results in { "detail": { "error": { code, message, ... } } }
      // We also handle direct { "error": {...} } and plain string { "detail": "..." }
      const nested =
        (errorBody?.error as ApiErrorDetail | undefined) ??
        ((errorBody?.detail as Record<string, unknown> | undefined)
          ?.error as ApiErrorDetail | undefined);

      const detail: ApiErrorDetail = nested ?? {
        code: "server_error",
        message:
          typeof errorBody?.detail === "string"
            ? errorBody.detail
            : `HTTP ${res.status}: ${res.statusText}`,
        run_id: null,
        details: {},
      };
      throw new ApiError(detail, res.status);
    }

    return res.json() as Promise<T>;
  } catch (err) {
    if (err instanceof ApiError) throw err;

    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(
        {
          code: "timeout",
          message: `Request timed out after ${timeoutMs / 1000} seconds`,
          run_id: null,
          details: {},
        },
        0,
      );
    }

    throw new ApiError(
      {
        code: "network_error",
        message:
          err instanceof Error ? err.message : "Unknown network error",
        run_id: null,
        details: {},
      },
      0,
    );
  } finally {
    clearTimeout(timer);
  }
}

const JSON_HEADERS = { "Content-Type": "application/json" } as const;

// ── Public API functions ─────────────────────────────────────────────────────

/**
 * POST /api/runs/start
 * Begins a new pipeline run. Returns run_id immediately; graph runs in background.
 * The github_token is in-flight only — never stored.
 */
export function startRun(body: StartRunRequest): Promise<StartRunResponse> {
  return request<StartRunResponse>("/api/runs/start", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
    timeoutMs: START_TIMEOUT_MS,
  });
}

/**
 * GET /api/runs/{id}/status
 * Polling fallback for status. Supabase Realtime is the primary live mechanism.
 */
export function getRunStatus(runId: string): Promise<RunStatusResponse> {
  return request<RunStatusResponse>(`/api/runs/${runId}/status`);
}

/**
 * GET /api/runs/{id}/output
 * Returns the full accumulated pipeline output for a run.
 */
export function getRunOutput(runId: string): Promise<RunOutputResponse> {
  return request<RunOutputResponse>(`/api/runs/${runId}/output`, {
    timeoutMs: START_TIMEOUT_MS,
  });
}

/**
 * POST /api/runs/{id}/approve
 * Resumes the graph after a HITL checkpoint.
 * github_token must be re-supplied because it is not persisted.
 */
export function approveRun(
  runId: string,
  body: ApproveRunRequest,
): Promise<ApproveRunResponse> {
  return request<ApproveRunResponse>(`/api/runs/${runId}/approve`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
    timeoutMs: START_TIMEOUT_MS,
  });
}

/**
 * POST /api/runs/{id}/create-pr
 * Creates a GitHub PR from the completed pr_draft.
 * github_token is in-flight only — never stored.
 */
export function createPR(
  runId: string,
  body: CreatePRRequest,
): Promise<CreatePRResponse> {
  return request<CreatePRResponse>(`/api/runs/${runId}/create-pr`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
}
