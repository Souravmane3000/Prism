# PRISM — API Specification

FastAPI backend deployed on Modal via `@modal.web_endpoint`. All request/response bodies are Pydantic models. GitHub PAT is accepted on endpoints that need it, used in-flight only, and **never** persisted to the database or written to logs.

---

## 1. Conventions

### Base path

All routes are under `/api`.

### Auth model (MVP)

No user accounts. The client sends a GitHub PAT when starting a run and when creating a PR. The server holds the token only for the duration of processing that needs GitHub access (graph invocation / create-pr).

### Error response (all routes)

Consistent envelope:

```json
{
  "error": {
    "code": "string_machine_code",
    "message": "Human-readable explanation",
    "run_id": "optional-uuid-if-applicable",
    "details": {}
  }
}
```

| HTTP status | When |
| --- | --- |
| `400` | Validation failure, malformed body, invalid action |
| `404` | Unknown `run_id` |
| `409` | Illegal state transition (e.g. approve when not awaiting HITL) |
| `502` | Upstream failure (GitHub, Modal Sandbox, LLM) surfaced safely |
| `500` | Unexpected server error (message sanitized; no secrets) |

`details` may include field-level validation errors; must never include PAT or raw secret material.

### CORS

- Allow origin from env var `FRONTEND_ORIGIN` (Vercel frontend URL).
- Allow credentials only if required by design; MVP typically needs `Authorization`-free browser calls with PAT in JSON body — still restrict origin strictly.
- Allow methods: `GET`, `POST`, `DELETE`, `OPTIONS`.
- Allow headers: `Content-Type`, `Accept`.

### Logging

Use `logging` only. Redact any header/body field named like `github_token`, `token`, `pat`, `authorization`.

---

## 2. Endpoints

### 2.1 `POST /api/runs/start`

Begins a new pipeline run. Creates `run_id`, seeds Supabase `runs` row, starts LangGraph with checkpointer `thread_id=run_id`.

**Request body:**

```json
{
  "repo_url": "https://github.com/owner/repo",
  "issue_url": "https://github.com/owner/repo/issues/42",
  "issue_text": null,
  "github_token": "ghp_..."
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `repo_url` | string (URL) | yes | Must be a GitHub repo URL |
| `issue_url` | string (URL) \| null | one of issue_url or issue_text | Preferred when available |
| `issue_text` | string \| null | one of issue_url or issue_text | Used when pasting issue body |
| `github_token` | string | yes | In-flight only; never stored |

Validation: require at least one of `issue_url` or `issue_text` non-empty.

**Response `201`:**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "current_agent": "planner"
}
```

**Side effects:**

- Insert `runs` row (`status=running`, `current_agent=planner`).
- Invoke / stream graph asynchronously or in background task compatible with Modal; return quickly with `run_id` so the client can subscribe to Realtime.
- Do **not** write `github_token` to Supabase.

---

### 2.2 `GET /api/runs/{id}/status`

Returns current high-level status for polling fallback (Realtime is primary for live UI).

**Path params:** `id` — `run_id`.

**Response `200`:**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "awaiting_approval",
  "current_agent": "hitl_1",
  "error": null,
  "all_tests_passed": null,
  "updated_at": "2026-08-12T12:00:00Z"
}
```

| `status` enum | Meaning |
| --- | --- |
| `running` | Agent executing |
| `awaiting_approval` | HITL interrupt active |
| `completed` | PR draft ready; pipeline finished |
| `failed` | Terminal error in `error` |
| `cancelled` | User stopped at HITL |

---

### 2.3 `GET /api/runs/{id}/output`

Full accumulated pipeline output. Intended for final view or refresh; live incremental updates come from Realtime.

**Response `200`:**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "current_agent": "pr_summarizer",
  "repo_url": "https://github.com/owner/repo",
  "issue_url": "https://github.com/owner/repo/issues/42",
  "issue_text": "...",
  "subtasks": [],
  "planner_approved": true,
  "file_map": {},
  "implementation_plan": [],
  "impl_approved": true,
  "test_results": null,
  "all_tests_passed": true,
  "debug_report": null,
  "pr_draft": {
    "title": "...",
    "body": "...",
    "what_changed": "...",
    "why": "...",
    "testing_notes": "...",
    "limitations": "...",
    "review_checklist": ["..."]
  },
  "messages": ["..."],
  "error": null,
  "pr_url": null
}
```

Notes:

- Omit `github_token` always.
- `file_contents` may be omitted or truncated in this endpoint if large; prefer paths in `file_map` and fetch-on-demand only if a future endpoint needs it. MVP may include contents if size is acceptable—document truncation in implementation if applied.
- `pr_url` set after successful `create-pr`.

---

### 2.4 `POST /api/runs/{id}/approve`

Resumes the graph after a HITL checkpoint (or cancels/restarts per action).

**Request body:**

```json
{
  "checkpoint": "hitl_1",
  "action": "approve",
  "subtasks": null,
  "implementation_plan": null,
  "github_token": "ghp_..."
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `checkpoint` | `"hitl_1"` \| `"hitl_2"` | yes | Must match current interrupt |
| `action` | `"approve"` \| `"edit"` \| `"revise"` \| `"restart"` \| `"stop"` | yes | `edit` aliases approve-with-edits for HITL1; `revise` for HITL2 |
| `subtasks` | list \| null | required when HITL1 + edit/approve with changes | Full replacement list when provided |
| `implementation_plan` | list \| null | required when HITL2 + revise/approve with changes | Full replacement when provided |
| `github_token` | string | yes if remaining nodes need GitHub | Re-supplied because PAT is not stored |

**Response `200`:**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "current_agent": "code_navigator",
  "message": "Resumed after hitl_1"
}
```

For `stop`:

```json
{
  "run_id": "...",
  "status": "cancelled",
  "current_agent": "hitl_2",
  "message": "Run cancelled by user"
}
```

For `restart`: cancel or mark superseded; optionally return guidance to call `/start` again (MVP: return `409` or `200` with `status=cancelled` and `message` instructing new start—pick one in implementation and keep stable).

**Server behavior:**

1. Load checkpointer state for `thread_id=id`.
2. Verify run is `awaiting_approval` and checkpoint matches.
3. `update_state` with approved fields (`planner_approved` / `impl_approved` + edits).
4. Resume `stream`/`ainvoke`.
5. Update Supabase `runs` row.

**Error `409`:** Not awaiting approval or wrong checkpoint.

---

### 2.5 `POST /api/runs/{id}/create-pr`

Creates a GitHub pull request from `pr_draft` using the GitHub API.

**Request body:**

```json
{
  "github_token": "ghp_...",
  "head_branch": "prism/run-550e8400",
  "base_branch": "main",
  "commit_message": null
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `github_token` | string | yes | In-flight only |
| `head_branch` | string | no | Default derived from `run_id` |
| `base_branch` | string | no | Default `main` (or repo default branch if detectable) |
| `commit_message` | string \| null | no | Optional; MVP may create PR from draft only if branch/commits already exist—**implementation must document actual PR strategy** |

**MVP PR strategy (locked intent):**

Prism’s Option B includes **real PR creation**. For MVP, `create-pr` uses PyGitHub to open a PR with title/body from `pr_draft`. If the pipeline does not push code commits (Prism does not auto-implement), the PR may be opened as a documentation/planning PR against an existing branch the user specifies, **or** the endpoint creates a branch with a PR body-only commit containing the plan/report markdown. Choose one approach in Phase 1 and keep it consistent; prefer: create branch `prism/<run_id>`, commit a `PRISM_REPORT.md` (plan + test + debug + draft), open PR into default branch. This satisfies “real PR on GitHub with professional body” without claiming Prism wrote application code.

**Response `201`:**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "pr_url": "https://github.com/owner/repo/pull/123",
  "pr_number": 123,
  "title": "..."
}
```

**Error `409`:** Run not `completed` or `pr_draft` missing.  
**Error `502`:** GitHub API failure (sanitized message).

---

### 2.6 `DELETE /api/runs/{id}`

Removes a run so the same repo/issue can be started again from a blank pipeline.

**No request body.** GitHub PAT is not required.

**Server behavior:**

1. Validate `id` is a UUID.
2. Delete LangGraph checkpointer rows for `thread_id=id` when those tables exist.
3. Delete the `runs` row. `agent_outputs` and `hitl_checkpoints` cascade.
4. Do **not** delete `code_embeddings` or `repo_cache` (shared per repo URL).

**Response `200`:**

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "deleted": true
}
```

**Error `404`:** Unknown or malformed `run_id`.

---

## 3. Realtime Instead of SSE

### Decision

There is **no** FastAPI SSE (or WebSocket) streaming endpoint for agent events.

### How the frontend gets live updates

1. After `POST /api/runs/start`, client receives `run_id`.
2. Client opens a Supabase Realtime channel filtered by `run_id` (postgres changes on `runs` and `agent_outputs`).
3. As each agent starts/completes, backend upserts rows; Realtime pushes to subscribers.
4. HITL cards appear when `runs.status` becomes `awaiting_approval` and `current_agent` is `hitl_1` or `hitl_2`.
5. `GET /api/runs/{id}/status` and `GET /api/runs/{id}/output` remain available as HTTP fallbacks and for final hydration.

### Suggested table shapes (backend implements migrations)

**`runs`**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | = `run_id` |
| `repo_url` | text | |
| `issue_url` | text null | |
| `status` | text | enum-like |
| `current_agent` | text | |
| `error` | text null | |
| `all_tests_passed` | boolean null | |
| `pr_url` | text null | |
| `created_at` / `updated_at` | timestamptz | |

**`agent_outputs`**

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `run_id` | uuid FK | Realtime filter |
| `agent` | text | |
| `phase` | text | `start` \| `complete` |
| `payload` | jsonb | agent-specific output slice |
| `created_at` | timestamptz | |

**Never store:** `github_token`, Modal keys, LangSmith keys.

---

## 4. Pydantic Model Names (Implementation Guide)

| Model | Used by |
| --- | --- |
| `StartRunRequest` / `StartRunResponse` | `POST /api/runs/start` |
| `RunStatusResponse` | `GET /api/runs/{id}/status` |
| `RunOutputResponse` | `GET /api/runs/{id}/output` |
| `ApproveRunRequest` / `ApproveRunResponse` | `POST /api/runs/{id}/approve` |
| `CreatePRRequest` / `CreatePRResponse` | `POST /api/runs/{id}/create-pr` |
| `DeleteRunResponse` | `DELETE /api/runs/{id}` |
| `ErrorResponse` | all error paths |

All fields fully typed; no `dict[str, Any]` in public response models unless constrained (`payload` internals may be structured TypedDicts serialized to JSON).

---

## 5. Modal Deployment Notes (API Layer)

- Single FastAPI app wrapped for Modal web endpoint.
- Production secrets from Modal Secret **`prism-secrets`**.
- Env must include at minimum: `OPENAI_API_KEY` (LLM), Supabase URL + service key, `FRONTEND_ORIGIN`, LangSmith vars, and any GitHub-unrelated config.
- Request body size limits should allow pasted issue text and edited plans; reject absurdly large payloads with `400`.

---

## 6. Endpoint ↔ Graph Mapping

| Endpoint | Graph interaction |
| --- | --- |
| `POST /start` | `graph.ainvoke` / `astream` with initial state; hits interrupt at `hitl_1` |
| `POST /approve` | `update_state` + resume until next interrupt or END |
| `GET /status` | Read Supabase `runs` (not raw checkpointer) |
| `GET /output` | Join `runs` + latest `agent_outputs` / materialized output columns |
| `POST /create-pr` | No graph node; uses completed `pr_draft` + GitHub API |
| `DELETE /{id}` | No graph node; deletes run + checkpoint thread |
