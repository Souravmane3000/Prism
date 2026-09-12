# Prism

**A multi-agent software engineering teammate.**

Give Prism a GitHub repository and an issue. It reads the codebase, breaks the work into subtasks, finds the relevant files, writes a step-by-step implementation plan, generates a structured patch, reviews the diff, waits for your approval, applies it in an isolated clone, runs the real test suite, and can diagnose and repair failing tests up to three verification attempts before rolling back. You stay in control at three approval gates.

Prism plans and evaluates first. It does not silently rewrite your application.

[![Live Demo](https://img.shields.io/badge/Live_Demo-prism--beta--one.vercel.app-a3e635?labelColor=0a0d08)](https://prism-beta-one.vercel.app)
[![pytest](https://github.com/Souravmane3000/Prism/actions/workflows/pytest.yml/badge.svg)](https://github.com/Souravmane3000/Prism/actions/workflows/pytest.yml)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white&labelColor=0a0d08)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph-1C3C3C?labelColor=0a0d08)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/FastAPI-Modal-009688?logo=fastapi&logoColor=white&labelColor=0a0d08)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-App_Router-000000?logo=nextdotjs&logoColor=white&labelColor=0a0d08)](https://nextjs.org/)
[![Supabase](https://img.shields.io/badge/Supabase-Postgres%20%2B%20pgvector-3FCF8E?logo=supabase&logoColor=white&labelColor=0a0d08)](https://supabase.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-7C3AED?labelColor=0a0d08)](LICENSE)

**[Live demo](https://prism-beta-one.vercel.app)** · **[How the graph works](GRAPH.md)** · **[Phase 5 graph](GRAPH_V2.md)** · **[Architecture](ARCHITECTURE.md)** · **[API](API.md)**

<p align="center">
  <img src="docs/assets/prism-workspace.png" alt="Prism workspace: left panel to start a run, center live agent stream, right tabbed Plan / Files / PR Draft / Debug output" width="100%" />
</p>

---

## Why this exists

Most AI coding tools jump straight to a patch. That looks fast in a demo and is hard to trust in a real repo.

Prism is built around a different thesis: **engineering judgment is the product.** A senior engineer does not start by writing code. They decompose the issue, locate the files that matter, plan the change, run tests, and only then describe the work in a pull request. Prism automates that loop — and pauses at three gates so a human can edit the plan, approve the reviewed diff, or stop before anything expensive is applied.

That makes this repo useful in two ways:

| If you are… | What to look at |
| --- | --- |
| A recruiter or hiring manager | The live demo, the Phase 5 pipeline below, and the tech stack. This is an end-to-end AI systems project, not a chatbot wrapper. |
| An engineer reviewing the code | Deterministic LangGraph routing, interrupt-based human-in-the-loop, a durable Postgres checkpointer, PAT never stored in the database, and tests that never run in the API process. |

---

## What a run looks like

1. Open the [live app](https://prism-beta-one.vercel.app) and paste a GitHub repo URL, an issue URL (or the issue text), and a GitHub personal access token.
2. **Planner** reads the issue, the file tree, and key config files, then proposes ordered subtasks with dependencies and likely files.
3. **You approve or edit** that breakdown (checkpoint 1).
4. **Code Navigator** maps each subtask to files using pgvector semantic search plus the GitHub API.
5. **Implementation Planner** writes a step-by-step plan: what to change, where, why, and the tradeoffs. It does not generate application code.
6. **You approve or revise** that plan (checkpoint 2).
7. **Code Modifier** generates a structured patch. **Validate** checks it, then **Diff Reviewer** produces an independent review (`APPROVE` | `REQUEST_CHANGES`).
8. **You approve, request changes (max 2 revisions), or stop** the reviewed diff (checkpoint 3). Only Approve reaches Apply for initial patches.
9. **Apply** materializes the approved patch in an isolated clone at `baseline_sha` (not the Prism checkout).
10. **Test Runner** reconstructs that applied workspace and runs pytest. Passing tests mark the patch `VERIFIED` but do **not** mark the session `SUCCESS`.
11. If tests fail, **Debugger** may propose a focused fix (`fix_patch`) that is validated and applied **without** HITL #3. Maximum **three** Test Runner attempts. A third failure rolls back to `baseline_sha` (`FAILED_MAX_VERIFICATION`). Environment/infra failures end the run without Debugger.
12. **PR Summarizer** writes a professional PR body from the pipeline. Optionally, Prism opens a real GitHub PR with a `PRISM_REPORT.md` on a `prism/<run_id>` branch.

Typical wall time for a non-trivial repo is under five minutes, not counting how long you spend at the approval screens.

---

## The pipeline (Phase 5)

```mermaid
flowchart LR
  A[Planner] --> H1[You approve subtasks]
  H1 --> B[Code Navigator]
  B --> C[Implementation Planner]
  C --> H2[You approve the plan]
  H2 --> CM[Code Modifier]
  CM --> V[Validate]
  V --> DR[Diff Reviewer]
  DR --> H3[You approve the diff]
  H3 --> AP[Apply]
  AP --> D[Test Runner]
  D --> R{Tests}
  R -->|PASS| F[PR Summarizer]
  R -->|FAIL attempts under 3| E[Debugger then Fix Patch]
  E --> V
  R -->|FAIL attempt 3| RB[Rollback]
```

Debugger-origin patches skip Diff Reviewer and HITL #3 after validate. See [GRAPH_V2.md](GRAPH_V2.md).

| Agent | Job | LLM-heavy? |
| --- | --- | --- |
| **Planner** | Turns the issue + repo tree into ordered subtasks with dependencies, file hints, and complexity. | Yes |
| **Code Navigator** | Finds the files that actually matter. Semantic search over cached embeddings, plus GitHub path/keyword matching. | No — tools first |
| **Implementation Planner** | Writes an engineering plan per subtask: file, symbol, change, rationale, tradeoffs. | Yes |
| **Code Modifier** | Generates a structured patch from the approved plan (and revision feedback when HITL #3 requests changes). | Yes |
| **Diff Reviewer** | Independent review of the validated patch before human approval. Does not apply code. | Yes |
| **Test Runner** | Reconstructs the applied workspace and runs pytest (Modal in production, isolated local clone in dev). | No |
| **Debugger** | Only if tests fail with attempts remaining. Symptom → root cause → targeted fix; Fix Patch applies a cumulative diff. | Yes |
| **PR Summarizer** | Composes title, what/why, test notes, limitations, and a review checklist. | Yes |

Three interrupt nodes — `hitl_1`, `hitl_2`, and `hitl_3` — are not agents. They are **LangGraph interrupts**. The pipeline pauses, the UI shows an approval card, and FastAPI resumes the same `run_id` from a Postgres-backed checkpointer. That is required because the API runs on serverless Modal workers that do not keep memory between HTTP requests.

---

## How the system is put together

```mermaid
flowchart TB
  U[You] --> FE[Next.js on Vercel]
  FE -->|REST: start / approve / create-pr| API[FastAPI on Modal]
  FE -->|live updates by run_id| RT[Supabase Realtime]
  API --> G[LangGraph pipeline]
  G --> LLM[OpenAI gpt-4o-mini]
  G --> GH[GitHub API]
  G --> SB[Modal Sandbox — tests only]
  G --> DB[(Supabase Postgres + pgvector)]
  DB --> RT
```

| Layer | Role |
| --- | --- |
| **Next.js (Vercel)** | Dark three-panel workspace. Left: sessions + run form. Center: live agent stream. Right: Plan / Files / PR Draft / Debug. |
| **FastAPI (Modal)** | Control plane. Creates a `run_id`, drives the graph, exposes status/output/approve/create-pr. |
| **LangGraph** | Phase 5 `StateGraph`: HITL #1/#2, Code Modifier → validate → Diff Reviewer → HITL #3 → Apply → Test Runner, with an attempt-capped debug/fix loop that bypasses HITL #3. Single compiled graph per process. |
| **Supabase** | System of record: runs, agent outputs, HITL payloads, embedding cache, and the LangGraph checkpointer. Realtime pushes agent start/complete events to the UI. |
| **OpenAI** | `gpt-4o-mini` for chat agents. `text-embedding-3-small` for Code Navigator. One cached factory in `backend/llm.py` — agents never instantiate their own client. |
| **LangSmith** | Auto-traces every graph run under project `Prism`. |

REST is for **control**. Realtime is for **live UI**. There is no WebSocket or SSE endpoint on FastAPI, by design — serverless workers should not hold streaming connections.

---

## What this project is meant to demonstrate

These are the engineering choices a reviewer can verify in the repo:

- **Deterministic multi-agent orchestration** with LangGraph (`StateGraph`, explicit edges, conditional Debugger skip) rather than a loosely coupled agent chat.
- **Human-in-the-loop that survives serverless.** Interrupts + `AsyncPostgresSaver`. Approve resumes the same thread; it does not restart from scratch.
- **Real execution, not mocks.** Test Runner clones and runs tests in Modal Sandbox. Create-PR opens a real GitHub pull request.
- **Security boundaries.** The GitHub PAT is request-scoped (`config["configurable"]`), never a column in `PrismState`, never logged, never written to Supabase. User code never runs inside the FastAPI process.
- **Live product UI.** Typed Next.js client (`lib/api.ts` only — no `fetch()` in components), CSS tokens as the only color source, Supabase Realtime with HTTP polling fallback.
- **Observability.** LangSmith traces + structured `agent_outputs` rows the frontend already knows how to render.

---

## Tech stack

| | |
| --- | --- |
| Language | Python 3.11 (backend), TypeScript strict (frontend) |
| Agents | LangGraph, LangChain, OpenAI gpt-4o-mini / text-embedding-3-small |
| API | FastAPI + Pydantic, deployed with `@modal.asgi_app()` |
| Data | Supabase PostgreSQL, pgvector, Supabase Realtime, `langgraph-checkpoint-postgres` |
| Tests in isolation | Modal Sandbox (`pytest` / `unittest` / Jest) |
| Frontend | Next.js App Router, Tailwind for layout, CSS custom properties for color |
| GitHub | PyGitHub, PAT per session (no user accounts in MVP) |
| Tracing | LangSmith project `Prism` |

---

## Repository map

```
Prism/
├── backend/                 FastAPI app, graph, agents, clients
│   ├── agents/              planner, navigator, impl planner, modifier, reviewer, tests, debugger, PR, HITL
│   ├── graph.py             StateGraph assembly + Postgres checkpointer
│   ├── llm.py               Single cached ChatOpenAI factory
│   ├── routers/runs.py      start / status / output / approve / create-pr
│   └── state.py             PrismState TypedDict
├── frontend/                Next.js workspace UI
├── supabase/migrations/     Schema: runs, agent_outputs, hitl_checkpoints, embeddings
├── tests/                   Agent, graph, API, and contract tests
├── docs/MANUAL_TEST.md      Production click matrix before a demo
├── .github/workflows/       pytest on push/PR to main
├── LICENSE                  MIT
├── modal_app.py             Modal image, secrets, ASGI entrypoint
├── PRD.md                   Product requirements
├── ARCHITECTURE.md          System design
├── GRAPH.md                 Node/edge/state spec the backend follows
├── API.md                   HTTP contract
└── DECISIONS.md             ADRs (LangGraph, Modal, Realtime, PAT policy, …)
```

---

## API at a glance

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/runs/start` | Start a pipeline. Returns `run_id` immediately. |
| `GET` | `/api/runs/{id}/status` | Polling fallback for the live UI. |
| `GET` | `/api/runs/{id}/output` | Full artifacts (subtasks, file map, plan, tests, PR draft). |
| `POST` | `/api/runs/{id}/approve` | Resume after HITL 1, 2, or 3 (approve / revise / stop; HITL #3 revise requires feedback). |
| `POST` | `/api/runs/{id}/create-pr` | Open a GitHub PR from the finished draft. |
| `DELETE` | `/api/runs/{id}` | Remove a run so the same issue can be demoed again. |
| `GET` | `/health` | Liveness. |

PAT is sent in the JSON body on start, approve, and create-pr, used in-flight, and discarded. Responses never include it.

---

## Run it locally

You need **Python 3.11+**, **Node 18+**, a **Supabase** project, an **OpenAI** API key, and (for sandbox tests and deploy) a **Modal** account.

### 1. Backend

```bash
git clone https://github.com/Souravmane3000/Prism.git
cd Prism
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill `.env` from [`.env.example`](.env.example). Run [`supabase/migrations/001_initial.sql`](supabase/migrations/001_initial.sql) in the Supabase SQL editor — it creates the tables, pgvector index, and Realtime publication.

```bash
uvicorn backend.main:app --reload --port 8000
```

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

```bash
pytest tests/ -q
```

GitHub Actions runs that suite on every push and pull request to `main` (see [`.github/workflows/pytest.yml`](.github/workflows/pytest.yml)). Live integration tests under `backend/tests_integration/` stay local — they need real API keys.

Interview re-run: [docs/MANUAL_TEST.md](docs/MANUAL_TEST.md).

### 2. Frontend

Create `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### 3. Production-shaped deploy

```bash
modal deploy modal_app.py
```

Point `NEXT_PUBLIC_API_BASE_URL` at the Modal web URL and `FRONTEND_ORIGIN` at the Vercel origin. Production secrets live in a Modal Secret named `prism-secrets`.

**GitHub PAT (classic):** `repo` (private repos) or `public_repo`, plus the ability to read issues and open pull requests. Paste it in the UI per run. Do not commit it.

---

## Security

- GitHub PAT is never a field on `PrismState` (so the checkpointer cannot serialize it into Postgres).
- Agents that need GitHub read the token from LangGraph run config, not from the database.
- Only the last four characters may be stored as a hint for the UI.
- CORS is an allow-list of frontend origins, not `*`.
- Sandbox clones and test execution are isolated from the web worker.

---

## Docs

| Doc | Contents |
| --- | --- |
| [PRD.md](PRD.md) | Scope, user journey, success criteria |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data flow, deployment topology |
| [GRAPH.md](GRAPH.md) | Exact nodes, edges, state schema, HITL resume protocol |
| [API.md](API.md) | Request/response contracts |
| [DECISIONS.md](DECISIONS.md) | Why LangGraph, Modal, Realtime, per-session PAT, … |
| [docs/MANUAL_TEST.md](docs/MANUAL_TEST.md) | Production click-path matrix before a demo |

---

Built by [Sourav Mane](https://github.com/Souravmane3000) as a production-shaped portfolio system — orchestration, HITL, sandboxing, embeddings, and a live UI in one repo.
