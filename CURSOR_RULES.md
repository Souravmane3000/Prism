# PRISM — Cursor Agent Build Rules

These are **imperatives**, not suggestions. Every future Cursor session that builds Prism must follow this file and `.cursorrules`. If a request conflicts with these rules, follow these rules and tell the user what cannot be done.

---

## 1. Build Order

1. **Phase 0 — Docs only.** The six markdown files (`PRD.md`, `ARCHITECTURE.md`, `GRAPH.md`, `API.md`, `DECISIONS.md`, `CURSOR_RULES.md`) are complete. Do not regenerate them casually; update them deliberately when decisions change.
2. **Phase 1 — Backend.** Implement in this order: `config` → `backend/llm.py` → `backend/github_client.py` → `backend/supabase_client.py` → `backend/state.py` → agents (in pipeline order: planner, code_navigator, impl_planner, test_runner, debugger, pr_summarizer) → HITL nodes (hitl_1, hitl_2) → `backend/graph.py` → FastAPI routes → Modal deployment.
3. **Phase 2 — Frontend.** Implement in this order: `styles/tokens.css` → `styles/animations.css` → `lib/types.ts` → `lib/api.ts` → `lib/sse.ts` → layout components → agent stream components → output tabs → input form → pages → Vercel config.

Do not start Phase 2 until Phase 1 graph + API behave correctly against the contracts in `GRAPH.md` and `API.md`.  
Do not skip Phase documentation reads.

---

## 2. Read Before Build

- Before building any module, read the relevant source-of-truth doc:
  - State / nodes / edges / HITL → `GRAPH.md`
  - HTTP routes / errors / PAT / Realtime → `API.md`
  - Components / deployment / data flow → `ARCHITECTURE.md`
  - Scope / out-of-scope / success → `PRD.md`
  - Why a choice exists → `DECISIONS.md`
- If code would contradict a doc, **update the doc first** (or stop and ask), then code.

---

## 3. Color and Theming

- Put **every** color value in `styles/tokens.css` as CSS custom properties.
- Everywhere else, use only `var(--color-*)` (or equivalent token vars).
- Do **not** use Tailwind color utility classes for brand/UI colors (e.g. no `bg-purple-600`, `text-cyan-400`).
- Do **not** write hex, `rgb()`, or `hsl()` outside `tokens.css`.
- Locked palette anchors (define as tokens, do not scatter):
  - Base near-black: `#0D0D0D`
  - Primary electric violet: `#7C3AED`
  - HITL / secondary deep cyan: `#06B6D4`
- Tailwind is for layout/spacing/typography scale only.

---

## 4. LangGraph Nodes

For every node:

1. Return **partial state only** — never treat “return full state” as acceptable style.
2. Persist outputs to Supabase **before** returning.
3. Emit progress on **start** and **complete** via Supabase writes that Realtime broadcasts.
4. Rely on LangSmith auto-tracing (project `Prism`); append human-readable lines to `messages`.
5. On failure, set `state["error"]` with a clear string; log with `logging`; **do not** raise uncaught exceptions that kill the graph without updating state.
6. Never execute user repository code outside `Modal.Sandbox`.
7. Never put `github_token` into Supabase rows or Realtime payloads.

HITL pauses use `interrupt_before=["hitl_1", "hitl_2"]` at compile time and a Supabase-backed checkpointer. Resume via `graph.update_state` then continue streaming. In-memory checkpoints are forbidden for deployed HITL.

---

## 5. LLM Usage

- Implement exactly one factory in `backend/llm.py`.
- Cache the client/model; never re-instantiate per call/agent carelessly.
- All agents import that factory.
- Do **not** instantiate ChatOpenAI (or equivalent) inline inside a node.
- Auth is `OPENAI_API_KEY` only. Chat model is `gpt-4o-mini`. Embeddings are `text-embedding-3-small`.
- Do not add a second LLM provider in MVP.

---

## 6. Python Standards

- Python **3.11**.
- Full type hints on all functions and methods.
- Pydantic models for **all** FastAPI request and response bodies.
- `try` / `except` with **specific** exception types on every external call (GitHub, Supabase, Modal, LLM). No bare `except:`.
- Use the `logging` module only. **Never** `print()`.
- Snake_case for modules, functions, variables, and JSON field names in Python-owned API contracts unless an explicit public API says otherwise (API field names stay snake_case per `API.md`).

---

## 7. TypeScript / Frontend Standards

- TypeScript **strict** mode.
- **No** `any`.
- **No** `fetch()` inside React components — all backend calls go through `lib/api.ts`.
- Keep components presentational; put data fetching, Realtime subscription setup, and orchestration in lib/hooks modules — not ad-hoc inside JSX files as business logic dumps.
- camelCase for TypeScript variables and functions.
- kebab-case for frontend component file names and CSS-oriented file names.
- Next.js 14 App Router only.

---

## 8. Security

- GitHub PAT: never log, never store in DB, never put in Realtime payloads, never commit to git.
- All secrets from environment variables.
- Production: Modal Secret object named **`prism-secrets`**.
- CORS origin from env (`FRONTEND_ORIGIN`); do not use `*` in production.
- Redact token-like fields in logs.

---

## 9. Naming Conventions

| Layer | Convention |
| --- | --- |
| Python modules / funcs / vars | `snake_case` |
| Python classes | `PascalCase` |
| TypeScript types / components | `PascalCase` |
| TypeScript funcs / vars | `camelCase` |
| Frontend component files | `kebab-case.tsx` |
| CSS variables | `--color-*` kebab after prefix |
| LangGraph node names / `current_agent` | exact strings from `GRAPH.md` |
| LangSmith project | `Prism` (case-sensitive; Modal secret `prism-secrets` is unrelated) |

---

## 10. What NOT To Do

- Do **not** write color values outside `styles/tokens.css`.
- Do **not** call `fetch()` in components.
- Do **not** put business logic or agent prompts inside graph assembly wiring beyond node registration and edges.
- Do **not** use `print()`.
- Do **not** hardcode secrets, URLs, model names duplicated per file, or magic environment values — centralize config.
- Do **not** leave `TODO` comments in shipped code — open/track an issue or fix it now.
- Do **not** substitute CrewAI, Fly.io-as-primary-API, WebSocket-first streaming, or a second LLM stack.
- Do **not** implement multi-user auth, billing, or non-GitHub repos in MVP.
- Do **not** auto-write and merge application feature code as if Prism were a codegen product — plan, test, debug-propose, PR-summarize.
- Do **not** add a FastAPI SSE endpoint for agent streaming; use Supabase Realtime.
- Do **not** run tests in the main FastAPI process.
- Do **not** commit `.env` files or PATs.

---

## 11. Graph Assembly Discipline

- Edges and conditional routing must match `GRAPH.md` exactly.
- Conditional route after Test Runner reads `all_tests_passed` only.
  `all_tests_passed` is true only when at least one test ran and none failed.
  Zero collected tests is not a pass — route to Debugger.
- Compile with Supabase-backed checkpointer; `thread_id` maps to `run_id`.
- Resume path: `update_state` then `stream` / `ainvoke` — never “restart from planner” for a normal approve.

---

## 12. API Discipline

- Implement at minimum the five routes in `API.md`.
- Use the shared error envelope.
- Re-accept `github_token` on approve/create-pr because it is not stored.
- Keep response models free of secrets.

---

## 13. UI Discipline (Phase 2)

- 3-panel layout: left sessions/repo, center live agent stream, right tabbed output.
- Right panel tabs are exactly: **Plan**, **Files**, **PR Draft**, **Debug**.
- Agent cards animate into the center stream with `fadeSlideUp` as each agent completes.
- HITL cards use cyan token and pulse while awaiting input.
- Brand denser/darker than generic “AI dashboard” templates; follow tokens.

---

## 14. When Uncertain

1. Read `PRD.md` / `ARCHITECTURE.md` / `GRAPH.md` / `API.md` / `DECISIONS.md`.
2. Prefer the locked stack over a “better” new library.
3. Prefer failing with `state["error"]` and a logged exception over silent catch-and-continue corruption.
4. Ask the user only when a product choice is truly unspecified (e.g. PR branch strategy details already sketched in `API.md` — implement that sketch rather than inventing a third approach).

---

## 15. Definition of Done (Any Backend Module)

- Types complete; Pydantic where I/O crosses HTTP.
- Specific exception handling on external calls.
- Supabase persist + Realtime-visible start/complete for nodes.
- No PAT leakage.
- Matches the relevant `.md` contract.
- No `print()`, no `TODO`, no hardcoded secrets.
