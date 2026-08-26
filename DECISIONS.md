# PRISM — Architectural Decision Record

Log of key decisions. Treat as binding unless a later ADR explicitly supersedes an entry.

---

## ADR-001 — OpenAI gpt-4o-mini (supersedes Kimi K3 via Modal)

**Decision:** Use OpenAI `gpt-4o-mini` for all chat agents and OpenAI `text-embedding-3-small` for Code Navigator embeddings. Authenticate with `OPENAI_API_KEY` only. Calls go to the official OpenAI API (`https://api.openai.com/v1`). No custom `base_url`.

**Supersedes:** The original ADR-001 (Kimi K3 via Modal OpenAI-compatible endpoint / `MODAL_AUTH_KEY`). Modal remains for Test Runner sandbox and FastAPI deploy only — not for inference.

**Context:** Modal's shared Kimi K3 endpoint requires a payment method and Team/Enterprise billing. The Starter $30 compute credits do not cover that Shared API. OpenAI gpt-4o-mini is sufficient for Prism's JSON planning tasks and unblocks local testing.

**Reasons:**

- Official OpenAI API needs no custom endpoint or proxy token.
- `gpt-4o-mini` is cheap, good at JSON, and has a 128k context window.
- `text-embedding-3-small` (1536 dims) already matches the pgvector schema.
- Single factory in `backend/llm.py` is unchanged in shape — only the provider behind it.

**Consequences:** All agents import `get_llm()`. Code Navigator embeddings use the same `OPENAI_API_KEY`. Swapping models later means changing `OPENAI_MODEL_NAME` / `OPENAI_EMBEDDING_MODEL`, not each node.

---

## ADR-002 — LangGraph over CrewAI

**Decision:** Orchestrate agents with LangGraph (`StateGraph`, interrupt-based HITL).

**Context:** Six sequential agents, two approval gates, conditional skip of Debugger.

**Reasons:**

- Deterministic routing (explicit edges + conditional edge after Test Runner).
- Native HITL `interrupt` / resume via checkpointer — first-class pause.
- Proven in prior project **Signal**.
- Better fit than CrewAI’s role-collaboration style for a fixed pipeline.

**Consequences:** Graph specification in `GRAPH.md` is the source of truth. No CrewAI dependency.

---

## ADR-003 — Modal for Backend (and Inference)

**Decision:** Deploy FastAPI with `@modal.web_endpoint` on Modal. Run tests in `Modal.Sandbox`. Keep inference on Modal.

**Context:** Need serverless API + isolated code execution + existing Modal setup.

**Reasons:**

- Serverless: idle cost ≈ $0.
- No credit-card gate unlike some alternatives (e.g. Fly.io friction for this build).
- Single platform for API, inference, and sandboxed test runs.
- Sandbox ensures user repo code never executes in the main web process.

**Consequences:** HITL requires a **durable** Supabase-backed checkpointer (in-memory checkpoints die across requests). PAT and secrets live in Modal Secret `prism-secrets` in production.

---

## ADR-004 — Option B Full Pipeline

**Decision:** Ship the full read + execute pipeline: real Modal Sandbox tests, real GitHub PR creation, both HITL gates. No planning-only MVP.

**Context:** Portfolio impact vs. smaller scope.

**Reasons:**

- Maximum signal to senior engineers/recruiters.
- Real execution is more impressive than planning-only demos.
- Aligns with Prism’s thesis: judgment + evaluation, not just text generation.

**Consequences:** Test Runner and create-pr are mandatory. Debugger is conditional but implemented. Timeline includes sandbox and GitHub PR work in Phase 1.

---

## ADR-005 — Supabase as Data + Vector + Realtime Plane

**Decision:** Use Supabase PostgreSQL with pgvector and Supabase Realtime.

**Context:** Need run persistence, embedding search for Code Navigator, and live UI updates.

**Reasons:**

- pgvector for code chunk embeddings and semantic search.
- Realtime for pushing agent state to the frontend.
- Familiar from prior projects — faster, safer delivery.
- Can back LangGraph checkpointer storage.

**Consequences:** Frontend subscribes by `run_id`. Embeddings cached per repo to avoid re-embedding every run.

---

## ADR-006 — Supabase Realtime over FastAPI SSE/WebSocket

**Decision:** Do not implement a dedicated FastAPI SSE or WebSocket streaming endpoint. Use Supabase Realtime on `runs` and `agent_outputs`.

**Context:** `.cursorrules` originally preferred SSE over WebSocket for one-way streams. Final architecture uses Realtime as the transport for that one-way stream, avoiding extra streaming infrastructure on serverless FastAPI.

**Reasons:**

- One-way agent progress is sufficient.
- Avoids holding long-lived stream connections on Modal web workers.
- Supabase already in the stack for DB/pgvector.
- REST remains for control plane (start, approve, status, output, create-pr).

**Consequences:** `API.md` specifies no SSE route. Frontend must initialize Supabase client and channel subscription on run start.

---

## ADR-007 — GitHub PAT Per Session (No User Auth)

**Decision:** Accept GitHub PAT per start/approve/create-pr request. Do not build multi-user auth, accounts, or token vaults for MVP.

**Context:** Scope control for portfolio MVP.

**Reasons:**

- No auth system needed.
- Keeps MVP tight and demoable.
- Natural security posture: token is ephemeral and never stored in DB.

**Consequences:** Approve and create-pr must re-accept PAT because it is not persisted. Logging redaction is mandatory. Out of scope: OAuth apps, team workspaces, billing.

---

## ADR-008 — Three-Panel Premium Dark UI

**Decision:** Build a Devin-inspired but deeper, more polished dark execution UI: near-black base (`#0D0D0D`), electric violet primary (`#7C3AED`), deep cyan for HITL (`#06B6D4`), 3-panel layout.

**Context:** Visual differentiation and demo quality.

**Reasons:**

- Reads as a serious engineering tool, not a chat toy.
- Clear spatial model: sessions | live stream | outputs.
- Cyan HITL cards make approval gates unmistakable.
- All colors only in `styles/tokens.css` as CSS variables — theme changes stay localized.

**Consequences:** No Tailwind color utility classes for brand colors; no hex/rgb outside `tokens.css`. Frontend Phase 2 starts with tokens before components.

---

## ADR-009 — Prism Plans and Reviews; Does Not Implement Application Code

**Decision:** Agents produce subtasks, file maps, implementation plans, test results, debug proposals, and PR bodies. They do **not** auto-write and merge production application code.

**Context:** Product thesis and safety.

**Reasons:**

- Engineering judgment is the product.
- HITL gates reinforce human ownership.
- Still allows real GitHub PRs (e.g. report/plan commit + professional body) under Option B.

**Consequences:** PRD out-of-scope includes AI writing final code. Debugger proposes minimal fixes; it does not apply them automatically in MVP.

---

## ADR-010 — Single Cached LLM Factory

**Decision:** Exactly one factory function in `backend/llm.py`, cached, imported by all agents. Never instantiate the chat model inline in a node.

**Context:** Consistency, cost control, tracing coherence.

**Reasons:**

- One place to configure model name, auth, timeouts.
- Prevents accidental multi-client sprawl.
- Aligns with LangSmith single-project tracing.

**Consequences:** Enforced in `CURSOR_RULES.md` as a non-negotiable.

---

## ADR-011 — LangSmith Project `"prism"`

**Decision:** Enable LangSmith auto-tracing for all LangGraph runs with project name `prism` via env at Modal startup.

**Context:** Need node-level observability for demos and debugging.

**Reasons:**

- Zero custom telemetry code per agent.
- Standard for LangChain/LangGraph stacks.

**Consequences:** Secrets include LangSmith API key in `prism-secrets`. Agents must not invent a parallel logging analytics system.

---

## ADR-012 — Build Order Locked (Docs → Backend → Frontend)

**Decision:** Phase 0 docs, then Phase 1 backend, then Phase 2 frontend. Read relevant `.md` before each module.

**Context:** Prevent speculative coding and stack drift.

**Reasons:**

- Backend agent (future Cursor session) depends entirely on these docs.
- Frontend needs real `run_id` + Realtime contracts from API/graph.

**Consequences:** No application code in Phase 0. No frontend-first implementation.

---

## Supersession

None yet. To change a decision, add `ADR-0XX` with explicit “Supersedes ADR-00Y” and update the dependent doc (`ARCHITECTURE.md`, `GRAPH.md`, or `API.md`) in the same change.
