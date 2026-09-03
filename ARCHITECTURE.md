# PRISM — System Architecture

## 1. System Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              USER                                         │
│         (repo URL + issue URL/text + GitHub PAT)                          │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     NEXT.JS 14 FRONTEND (Vercel)                          │
│  Left: sessions + repo info                                               │
│  Center: live agent activity stream                                       │
│  Right: tabbed output (Plan | Files | PR Draft | Debug) + HITL cards     │
│                                                                           │
│  • REST → FastAPI (start / status / output / approve / create-pr)         │
│  • Realtime ← Supabase (subscribe by run_id)                              │
└───────────────┬──────────────────────────────▲───────────────────────────┘
                │ HTTPS REST                   │ Realtime
                │                              │ (runs, agent_outputs)
                ▼                              │
┌──────────────────────────────────────────────────────────────────────────┐
│                   FASTAPI ON MODAL (@modal.web_endpoint)                  │
│                                                                           │
│  Routes: start | status | output | approve | create-pr | DELETE /{id}     │
│  Holds in-flight GitHub PAT (never persisted)                             │
│  Compiles & drives LangGraph with Supabase-backed checkpointer            │
└───────────────┬──────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         LANGGRAPH PIPELINE                                │
│                                                                           │
│  Planner → HITL1 → Code Navigator → Impl Planner → HITL2                  │
│       → Test Runner → (Debugger?) → PR Summarizer → END                   │
│                                                                           │
│  LLM: OpenAI gpt-4o-mini (OPENAI_API_KEY) + text-embedding-3-small        │
│  Traces: LangSmith project "Prism"                                        │
└───────┬──────────────────┬───────────────────┬───────────────────────────┘
        │                  │                   │
        ▼                  ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌──────────────────┐
│   SUPABASE    │  │  GITHUB API   │  │  MODAL.SANDBOX   │
│  PostgreSQL   │  │  (PyGitHub)   │  │  clone + tests   │
│  + pgvector   │  │  issues, tree │  │  isolated only   │
│  + Realtime   │  │  contents, PR │  └──────────────────┘
│  checkpointer │  └───────────────┘
│  embeddings   │
│  run state    │
└───────┬───────┘
        │ Realtime push (run_id channel)
        └──────────────────────────────────────────────► Frontend
```

---

## 2. Component Responsibilities

### Next.js Frontend (Vercel)

Owns the dark premium 3-panel UI. Collects repo URL, issue input, and GitHub PAT; never stores the PAT in browser persistence beyond the active session’s in-memory use for API calls. Calls FastAPI via `lib/api.ts` only. On run start, subscribes to Supabase Realtime filtered by `run_id` and renders agent activity as nodes complete. Surfaces HITL checkpoint cards (cyan accent) for approve/edit/stop. Displays tabbed final outputs in four fixed tabs: **Plan**, **Files**, **PR Draft**, **Debug**.

### FastAPI Backend (Modal web endpoint)

HTTP control plane for the pipeline. Validates input with Pydantic, creates a `run_id`, seeds `PrismState`, starts or resumes the LangGraph graph, and exposes status/output/approve/create-pr. Applies CORS for the Vercel frontend URL from env. Passes the GitHub PAT only in-flight into graph invocation context; does not write PAT to Supabase. Wraps FastAPI with `@modal.web_endpoint` for serverless deploy.

### LangGraph Pipeline

Deterministic six-agent sequential graph plus two HITL interrupt nodes and one conditional edge after Test Runner. Each node returns partial state only, persists to Supabase before return, emits Realtime-visible progress on start and complete, and places errors in `state["error"]` rather than crashing silently. Uses a single cached LLM factory (`backend/llm.py`) for OpenAI gpt-4o-mini. Compiled with a Supabase-backed checkpointer so HITL pauses survive serverless request boundaries.

### Supabase (PostgreSQL + pgvector + Realtime)

System of record for runs, agent outputs, checkpointer state, and cached repo embeddings. pgvector powers Code Navigator semantic search. Realtime pushes inserts/updates on `runs` and `agent_outputs` to the frontend. Does not store GitHub PATs.

### GitHub API (PyGitHub + REST)

Source of issue bodies, recursive repo trees, file contents, and optional PR creation. Authenticated with the per-session PAT supplied at run start / create-pr. Used by Planner, Code Navigator, and PR creation path.

### Modal Sandbox

Isolated execution environment for Test Runner. Clones the target repository and runs the detected test suite. Never executes user code in the main FastAPI/Modal web process.

### OpenAI (LLM + embeddings)

Official OpenAI API. Chat uses `gpt-4o-mini` (`OPENAI_API_KEY`, `OPENAI_MODEL_NAME`). Code Navigator embeddings use `text-embedding-3-small`. Used by Planner, Implementation Planner, Debugger, and PR Summarizer (Code Navigator is tool-heavy / LLM-light).

### LangSmith

Observability. With LangSmith env vars set at Modal startup and project name `"Prism"`, LangGraph node runs are auto-traced. Agents do not implement custom LangSmith clients beyond standard LangChain/LangGraph instrumentation. The Modal secret named `prism-secrets` is the production vault; it is not the LangSmith project.

---

## 3. Data Flow — User Input to PR Creation

1. **Submit:** User posts repo URL, issue URL or text, and PAT to `POST /api/runs/start`.
2. **Seed:** Backend creates `run_id`, inserts a `runs` row (`status=running`, `current_agent=planner`), builds initial `PrismState` (PAT held only in invocation config / memory for this run’s requests—not in DB columns).
3. **Planner:** Fetches issue (if URL), recursive file tree, README and key configs via GitHub. Invokes LLM. Writes `repo_tree`, `subtasks`, `messages`. Persists `agent_outputs`; Realtime notifies frontend.
4. **HITL 1:** Graph interrupts with subtask payload. Frontend shows interactive card. User `POST /api/runs/{id}/approve` with approved/edited subtasks. Backend `update_state` then resumes stream.
5. **Code Navigator:** For each subtask, parallel semantic search (pgvector; embed+cache repo if needed) and GitHub path/keyword matching. Writes `file_map`, `file_contents`. Persists + Realtime.
6. **Implementation Planner:** Per subtask + files, writes engineering plan into `implementation_plan`. Persists + Realtime. No code generation.
7. **HITL 2:** Interrupt with plan payload. User approve/revise/stop via approve endpoint. Resume.
8. **Test Runner:** Spawns Modal Sandbox, clones repo, detects pytest/unittest/jest, runs suite. Writes `test_results`, `all_tests_passed`. Persists + Realtime.
9. **Route:** If `all_tests_passed` is true → PR Summarizer. Else → Debugger.
10. **Debugger (optional):** Reads failures, cross-references file map and plan, writes `debug_report` with minimal fix proposals + confidence. Persists + Realtime.
11. **PR Summarizer:** Reads full pipeline artifacts; writes `pr_draft` (title, body sections, checklist). Persists + Realtime. Run status → `completed` (or `awaiting_pr` if product chooses an intermediate state).
12. **Create PR (optional):** User `POST /api/runs/{id}/create-pr` with PAT (again in-flight). Backend uses `pr_draft` + GitHub API to open the PR; returns PR URL. PAT still never written to DB.

---

## 4. Deployment Topology

| Layer | Where it runs | What lives there |
| --- | --- | --- |
| Frontend | **Vercel** | Next.js 14 App Router, Tailwind (layout/spacing), `styles/tokens.css`, realtime client |
| API + Graph | **Modal** | FastAPI app, LangGraph assembly, agent nodes, LLM factory, GitHub client wrappers |
| Inference | **OpenAI** | gpt-4o-mini chat + text-embedding-3-small; auth via `OPENAI_API_KEY` |
| Test isolation | **Modal.Sandbox** | Clone + test execution only |
| Data + Realtime | **Supabase** | Postgres tables, pgvector embeddings, checkpointer storage, Realtime channels |
| Tracing | **LangSmith** (SaaS) | Traces for project `"Prism"`; env configured on Modal at startup |
| Secrets (prod) | **Modal Secret `prism-secrets`** | `OPENAI_API_KEY`, Supabase keys, LangSmith keys, CORS origin, etc. |

Idle Modal cost is ~$0. Frontend is static/SSR on Vercel. Database and Realtime are always Supabase-managed.

---

## 5. LangSmith Placement

LangSmith sits **outside** the request path as an observability sink:

1. Modal web app startup / container env includes LangSmith variables (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=Prism`, plus legacy `LANGCHAIN_*` aliases).
2. Once set, LangChain/LangGraph auto-instruments node invocations and LLM calls.
3. No agent node should manually push traces or invent a second tracing system.
4. Failures in tracing must not break the pipeline; missing LangSmith config degrades to “no traces,” not hard failure (document in ops notes; prefer always-on for demos).

---

## 6. Real-Time Frontend Updates

Prism does **not** require a FastAPI SSE or WebSocket endpoint for agent streaming.

### Mechanism

1. Backend nodes write to Supabase tables (`runs`, `agent_outputs`) on start and complete.
2. Supabase Realtime broadcasts those row changes.
3. On page load / run start, the frontend subscribes to a channel filtered by `run_id`.
4. As each agent starts or finishes, the UI receives inserts/updates and animates the center stream / right panel.

### Why this shape

- One-way push is enough (no bidirectional socket protocol).
- Avoids maintaining streaming connection state on serverless FastAPI.
- Reuses Supabase already required for persistence and pgvector.

REST remains for **control**: start, status poll fallback, full output fetch, HITL approve, create-pr.

---

## 7. Security Boundaries (Architecture-Level)

| Concern | Rule |
| --- | --- |
| GitHub PAT | Per request/session; in-flight only; never logged; never stored in Supabase |
| User code | Execute only in Modal Sandbox |
| Secrets | Env vars; production Modal Secret named `prism-secrets` |
| CORS | Allow only configured Vercel frontend origin |
| LLM auth | `OPENAI_API_KEY` only; gpt-4o-mini + text-embedding-3-small |

---

## 8. Consistency With Locked Stack

Do not substitute: CrewAI, Fly.io as primary API host, WebSocket-first streaming, non-GitHub VCS, a second LLM client instantiated per agent, or hex colors outside `styles/tokens.css`. All of these are rejected by project decisions (`DECISIONS.md`) and build rules (`CURSOR_RULES.md`).
