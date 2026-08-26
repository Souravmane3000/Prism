# PRISM — Product Requirements Document

## 1. What Prism Is

Prism is a production-grade multi-agent Software Engineering Teammate. A user connects a GitHub repository, provides a GitHub issue (URL or pasted text), and supplies a GitHub Personal Access Token (PAT). Prism then runs a six-agent LangGraph pipeline that reasons about the codebase like a senior engineer: decomposing the problem, locating relevant code, planning implementation, running tests in isolation, debugging failures, and producing a professional pull request.

Prism is **not** an AI code generator. It emphasizes reasoning, planning, and evaluation. Code output is a byproduct. Engineering judgment is the product. The system assists the developer’s decision-making through two human-in-the-loop (HITL) checkpoints; it does not replace that judgment.

The full stack is locked: Python 3.11, LangGraph, OpenAI gpt-4o-mini (chat) and text-embedding-3-small (embeddings), FastAPI on Modal, Supabase (PostgreSQL + pgvector + Realtime), Next.js 14 on Vercel, LangSmith tracing, and GitHub via PyGitHub with a per-session PAT.

---

## 2. Why It Exists

Modern AI coding tools often jump straight to patches. Prism exists to demonstrate a different thesis: a production multi-agent system that **plans and evaluates first**, with deterministic orchestration, real sandbox test execution, and explicit human gates.

Portfolio and demonstration goals:

- Show end-to-end AI systems engineering (orchestration, HITL, sandboxing, embeddings, realtime UI).
- Prove judgment under constraints: Option B means real tests and real PRs, not a planning-only demo.
- Leave a codebase a senior engineer can inspect and trust on first pass: typed, documented, and consistent with locked architectural decisions.

---

## 3. Who It Is For

| Audience | Use |
| --- | --- |
| Individual developer | Runs Prism on their own GitHub repos and issues to get structured plans, test feedback, and PR drafts. |
| Portfolio / hiring evaluator | Inspects architecture, agent graph, and a live run to assess AI engineering capability. |
| Builder of this project | Uses the six Phase 0 docs as the source of truth for backend and frontend implementation. |

MVP assumes a single operator: no multi-user accounts, no team workspaces, no billing.

---

## 4. MVP Scope — Option B (Full Pipeline)

MVP is the complete Option B path. There is no stripped-down “planning only” variant.

### In scope

1. **Input:** GitHub repo URL + issue URL or issue text + GitHub PAT (per request/session).
2. **Planner** — Issue + repo tree + README/config context → ordered subtasks with dependencies, file hints, complexity.
3. **HITL Checkpoint 1** — User approves, edits, or restarts the subtask breakdown.
4. **Code Navigator** — pgvector semantic search + GitHub API matching → file map + file contents; embeddings cached in Supabase.
5. **Implementation Planner** — Step-by-step engineering plans per subtask (what/where/why/tradeoffs). Does **not** write implementation code.
6. **HITL Checkpoint 2** — User approves, requests revisions, or stops before any code execution.
7. **Test Runner** — Clone into `Modal.Sandbox`, detect framework (pytest / unittest / jest), run full suite, structured results.
8. **Conditional routing** — All pass → skip Debugger; any fail → Debugger.
9. **Debugger** (conditional) — Symptom → root cause → minimal fix proposals with confidence; no full rewrites.
10. **PR Summarizer** — Professional PR title/body from full pipeline output; optional real PR creation via GitHub API.
11. **Frontend** — Dark premium 3-panel UI; live agent stream via Supabase Realtime; cyan HITL cards.
12. **Observability** — LangSmith project `"prism"` auto-traces LangGraph runs when env is set.

### Explicitly out of scope for MVP

| Out of scope | Reason |
| --- | --- |
| Multi-user auth / accounts | PAT per session keeps MVP tight |
| Billing / usage metering | Demo budget on Modal; not a product SaaS yet |
| Team workspaces / sharing | Single-operator portfolio MVP |
| Non-GitHub VCS (GitLab, Bitbucket, local-only) | GitHub + PyGitHub only |
| AI writing final production code / auto-committing patches | Prism plans and reviews; it does not implement |
| Persistent storage of GitHub PATs | Security: in-flight only, never in DB or logs |
| SSE or WebSocket streaming from FastAPI | Supabase Realtime replaces dedicated streaming infra |
| Supporting arbitrary LLMs beyond OpenAI gpt-4o-mini | Single locked LLM factory |

---

## 5. User Journey (Happy Path)

1. User opens Prism on Vercel, enters repo URL, issue URL or text, and GitHub PAT.
2. Frontend calls `POST /api/runs/start`; receives `run_id`.
3. Frontend subscribes to Supabase Realtime for that `run_id`.
4. Planner runs; center panel shows agent activity; right panel accumulates output.
5. Pipeline pauses at HITL 1; cyan checkpoint card; user edits/approves subtasks.
6. Code Navigator and Implementation Planner run; pipeline pauses at HITL 2.
7. User approves the implementation plan.
8. Test Runner executes in Modal Sandbox; Debugger runs only if needed.
9. PR Summarizer produces draft; user optionally calls `POST /api/runs/{id}/create-pr`.
10. Professional PR appears on GitHub; run marked complete.

---

## 6. Success Definition

A run is considered successful for MVP when **all** of the following hold:

1. **End-to-end latency:** A full pipeline run on a real, non-trivial GitHub repository completes in **under 5 minutes** (excluding arbitrary user think-time at HITL checkpoints).
2. **HITL correctness:** Both checkpoints interrupt the graph, surface editable payloads in the UI, and resume correctly via approve/edit (or stop/restart where supported).
3. **Real execution:** Test Runner actually clones and runs tests in `Modal.Sandbox` (not mocked).
4. **Real PR:** On user request, a PR is created on GitHub with a professional body (title, description, what/why, test notes, limitations, review checklist).
5. **Live UI:** Frontend receives agent start/complete updates in real time for the active `run_id` via Supabase Realtime.
6. **Safety:** GitHub PAT is never logged and never persisted to the database.

---

## 7. Non-Functional Requirements

| Area | Requirement |
| --- | --- |
| Quality bar | Production / portfolio-grade; typed Python and TypeScript; no silent graph crashes |
| Isolation | User repo code and tests run only in Modal Sandbox, never in the main FastAPI process |
| Cost | Serverless Modal (idle ≈ $0); $30 demo budget assumed sufficient for demo traffic |
| Secrets | All keys from env; production Modal Secret named `prism-secrets` |
| Theming | All colors only in `styles/tokens.css` as CSS custom properties |
| Tracing | LangSmith project name `prism` |

---

## 8. Build Phases (Locked Order)

| Phase | Deliverable |
| --- | --- |
| **0** | These six markdown docs: `PRD.md`, `ARCHITECTURE.md`, `GRAPH.md`, `API.md`, `DECISIONS.md`, `CURSOR_RULES.md` |
| **1** | Backend: config, LLM factory, GitHub/Supabase clients, `PrismState`, 6 agents + 2 HITL nodes, graph assembly, FastAPI routes, Modal deploy |
| **2** | Frontend: `tokens.css` first, then components, pages, live backend connection |

Do not start Phase 1 until Phase 0 docs are complete and treated as source of truth. Do not start Phase 2 until Phase 1 graph and API behave correctly.

---

## 9. Acceptance Checklist (MVP Demo)

- [ ] Start run with real repo + issue + PAT
- [ ] Planner output visible; HITL 1 approve/edit works
- [ ] File map and implementation plan visible; HITL 2 approve works
- [ ] Sandbox tests run; conditional Debugger skip/run is correct
- [ ] PR draft quality is recruiter-ready
- [ ] Optional create-PR succeeds on GitHub
- [ ] Wall time under 5 minutes excluding HITL wait
- [ ] PAT absent from DB rows and application logs
