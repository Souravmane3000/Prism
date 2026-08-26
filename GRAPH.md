# PRISM — LangGraph Specification

This is the most critical Phase 0 document. Backend implementation must match this graph exactly.

---

## 1. Graph Overview

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │   planner   │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │   hitl_1    │  ← interrupt (subtasks)
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │code_navigator│
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │impl_planner │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │   hitl_2    │  ← interrupt (implementation_plan)
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │ test_runner │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │ route_after_tests       │
              │ reads all_tests_passed  │
              └────────────┬────────────┘
               false       │        true
                  ▼        │         ▼
           ┌──────────┐    │   (skip debugger)
           │ debugger │    │
           └────┬─────┘    │
                └──────────┤
                           ▼
                    ┌─────────────┐
                    │pr_summarizer│
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │    END      │
                    └─────────────┘
```

**Nodes (8):** `planner`, `hitl_1`, `code_navigator`, `impl_planner`, `hitl_2`, `test_runner`, `debugger`, `pr_summarizer`.

**Conditional edge:** after `test_runner` → `debugger` | `pr_summarizer` based on `all_tests_passed`.

---

## 2. PrismState TypedDict

All fields below are required in the state schema. Types are Python typing annotations as implemented in Phase 1. Writers listed are the primary producers; HITL nodes may overwrite approval and edited plan fields.

```python
from typing import Annotated, Any, Optional, TypedDict
from langgraph.graph.message import add_messages  # or equivalent append reducer
# Prefer an explicit append reducer for messages if not using LangChain Message types.

class Subtask(TypedDict):
    id: str
    title: str
    description: str
    dependencies: list[str]       # subtask ids
    likely_files: list[str]
    complexity: str               # e.g. "low" | "medium" | "high"


class FileMapEntry(TypedDict):
    path: str
    relevance_score: float
    source: str                   # "pgvector" | "github" | "both"


class ImplementationStep(TypedDict):
    order: int
    file: str
    function_or_symbol: Optional[str]
    change_description: str
    rationale: str
    tradeoffs: list[str]


class ImplementationPlanItem(TypedDict):
    subtask_id: str
    steps: list[ImplementationStep]


class TestFailure(TypedDict):
    name: str
    traceback: str
    message: str


class TestResults(TypedDict):
    framework: str                # "pytest" | "unittest" | "jest" | "unknown"
    passed: list[str]
    failed: list[TestFailure]
    passed_count: int
    failed_count: int
    exit_code: int
    stdout: str
    stderr: str


class DebugFix(TypedDict):
    failing_test: str
    root_cause: str
    proposed_fix: str
    confidence: float             # 0.0–1.0
    target_files: list[str]


class DebugReport(TypedDict):
    fixes: list[DebugFix]
    summary: str


class PRDraft(TypedDict):
    title: str
    body: str
    what_changed: str
    why: str
    testing_notes: str
    limitations: str
    review_checklist: list[str]


class PrismState(TypedDict):
    # ── Inputs (written at run start by API / graph invoker) ──
    repo_url: str                          # writer: API seed
    issue_url: Optional[str]               # writer: API seed
    issue_text: Optional[str]              # writer: API seed (and/or planner after fetch)
    github_token: str                      # writer: API seed — NEVER persist to Supabase
    run_id: str                            # writer: API seed

    # ── Planner ──
    repo_tree: list[str]                   # writer: planner
    subtasks: list[Subtask]                # writer: planner; may be edited via hitl_1 resume

    # ── HITL 1 ──
    planner_approved: bool                 # writer: hitl_1 / resume update

    # ── Code Navigator ──
    file_map: dict[str, list[FileMapEntry]]  # writer: code_navigator; key = subtask_id
    file_contents: dict[str, str]          # writer: code_navigator; key = path

    # ── Implementation Planner ──
    implementation_plan: list[ImplementationPlanItem]  # writer: impl_planner; editable via hitl_2

    # ── HITL 2 ──
    impl_approved: bool                    # writer: hitl_2 / resume update

    # ── Test Runner ──
    test_results: Optional[TestResults]    # writer: test_runner
    all_tests_passed: bool                 # writer: test_runner

    # ── Debugger ──
    debug_report: Optional[DebugReport]    # writer: debugger (absent/None if skipped)

    # ── PR Summarizer ──
    pr_draft: Optional[PRDraft]            # writer: pr_summarizer

    # ── Control / observability ──
    current_agent: str                     # writer: every node on enter
    error: Optional[str]                   # writer: any node on failure
    messages: Annotated[list[str], "append-only"]  # writer: every node; append-only log
```

### Field ownership summary

| Field | Written by |
| --- | --- |
| `repo_url`, `issue_url`, `issue_text`, `github_token`, `run_id` | API run seed |
| `repo_tree`, `subtasks` | `planner` (+ HITL1 edits to `subtasks`) |
| `planner_approved` | `hitl_1` / approve resume |
| `file_map`, `file_contents` | `code_navigator` |
| `implementation_plan` | `impl_planner` (+ HITL2 edits) |
| `impl_approved` | `hitl_2` / approve resume |
| `test_results`, `all_tests_passed` | `test_runner` |
| `debug_report` | `debugger` |
| `pr_draft` | `pr_summarizer` |
| `current_agent`, `error`, `messages` | all nodes as applicable |

**Persistence rule:** Persist agent artifacts to Supabase **without** the `github_token` field. Token remains only in graph invocation memory / request-scoped config for nodes that need GitHub.

---

## 3. Node Specifications

### 3.1 `planner`

| Aspect | Spec |
| --- | --- |
| **Reads** | `repo_url`, `issue_url`, `issue_text`, `github_token`, `run_id` |
| **Writes** | `repo_tree`, `subtasks`, `issue_text` (if fetched from URL), `current_agent`, `messages`, `error` (on failure) |
| **HITL interrupt** | No |
| **Realtime / Supabase** | On start: set `runs.current_agent=planner`, status running; insert/update `agent_outputs` progress. On complete: persist `subtasks` + `repo_tree` summary; emit complete. |
| **Behavior** | Fetch issue body if URL given. Fetch recursive file tree. Read README and key config files for context. LLM produces ordered subtasks with title, description, dependencies, likely files, complexity. Most LLM-intensive node. |
| **Errors** | Catch specific GitHub/LLM/network exceptions; set `error`; append to `messages`; do not raise out of the node. |

---

### 3.2 `hitl_1`

| Aspect | Spec |
| --- | --- |
| **Reads** | `subtasks`, `run_id` |
| **Writes** | `planner_approved`, possibly updated `subtasks`, `current_agent`, `messages` |
| **HITL interrupt** | **Yes** — `from langgraph.types import interrupt`; called with checkpoint-1 payload |
| **Realtime / Supabase** | On enter: `runs.status=awaiting_approval`, `current_agent=hitl_1`; payload available to frontend via `agent_outputs` / runs metadata. On resume after approve: status back to `running`. |
| **Behavior** | Pause until API approve path resumes graph. Reject path may set error or restart policy (product: support approve with edits; restart = new run or explicit reset). |
| **Errors** | Invalid resume payload → `error` string; do not crash process. |

**Interrupt payload shape (HITL 1):**

```json
{
  "checkpoint": "hitl_1",
  "run_id": "<uuid>",
  "type": "subtask_approval",
  "subtasks": [
    {
      "id": "st-1",
      "title": "...",
      "description": "...",
      "dependencies": [],
      "likely_files": ["src/foo.py"],
      "complexity": "medium"
    }
  ],
  "actions_allowed": ["approve", "edit", "restart"]
}
```

**Resume update (via `update_state` before stream):**

```json
{
  "planner_approved": true,
  "subtasks": [ /* possibly edited list */ ]
}
```

---

### 3.3 `code_navigator`

| Aspect | Spec |
| --- | --- |
| **Reads** | `repo_url`, `github_token`, `run_id`, `subtasks`, `repo_tree` |
| **Writes** | `file_map`, `file_contents`, `current_agent`, `messages`, `error` |
| **HITL interrupt** | No |
| **Realtime / Supabase** | Start/complete emissions; persist file map (paths + scores). Cache embeddings in pgvector keyed by repo identity so re-runs skip re-embed when cache hit. |
| **Behavior** | Per subtask, run in parallel: (1) semantic search over embedded chunks, (2) GitHub API path/keyword matching. Merge into scored `file_map`. Fetch full contents for selected paths into `file_contents`. Tool-heavy, LLM-light. |
| **Errors** | Specific exceptions for GitHub, embedding, Supabase; set `error`; continue with partial map only if safe—otherwise stop further agents by leaving error set and letting API/graph policy halt (prefer halt on empty critical map). |

---

### 3.4 `impl_planner`

| Aspect | Spec |
| --- | --- |
| **Reads** | `subtasks`, `file_map`, `file_contents`, `issue_text`, `run_id` |
| **Writes** | `implementation_plan`, `current_agent`, `messages`, `error` |
| **HITL interrupt** | No |
| **Realtime / Supabase** | Start/complete; persist full plan JSON. |
| **Behavior** | For each subtask + files, write step-by-step engineering plan: what to change, where (file + function/symbol), why, tradeoffs. **Does not write code.** |
| **Errors** | LLM/parse failures → `error`; never raise. |

---

### 3.5 `hitl_2`

| Aspect | Spec |
| --- | --- |
| **Reads** | `implementation_plan`, `run_id` |
| **Writes** | `impl_approved`, possibly updated `implementation_plan`, `current_agent`, `messages` |
| **HITL interrupt** | **Yes** — `from langgraph.types import interrupt`; last human gate before Test Runner |
| **Realtime / Supabase** | `status=awaiting_approval`, `current_agent=hitl_2`; resume → `running`. |
| **Behavior** | User approves, requests revisions (edited plan in resume state), or stops. Stop leaves run cancelled and does not proceed to tests. |

**Interrupt payload shape (HITL 2):**

```json
{
  "checkpoint": "hitl_2",
  "run_id": "<uuid>",
  "type": "implementation_plan_approval",
  "implementation_plan": [
    {
      "subtask_id": "st-1",
      "steps": [
        {
          "order": 1,
          "file": "src/foo.py",
          "function_or_symbol": "Foo.bar",
          "change_description": "...",
          "rationale": "...",
          "tradeoffs": ["..."]
        }
      ]
    }
  ],
  "actions_allowed": ["approve", "revise", "stop"]
}
```

**Resume update:**

```json
{
  "impl_approved": true,
  "implementation_plan": [ /* possibly revised */ ]
}
```

For `stop`: set run status `cancelled`; do not resume toward `test_runner`.

---

### 3.6 `test_runner`

| Aspect | Spec |
| --- | --- |
| **Reads** | `repo_url`, `github_token` (if clone needs auth for private repos), `run_id` |
| **Writes** | `test_results`, `all_tests_passed`, `current_agent`, `messages`, `error` |
| **HITL interrupt** | No |
| **Realtime / Supabase** | Start/complete; persist structured test results (truncate huge stdout/stderr if needed but keep failure tracebacks). |
| **Behavior** | Create `Modal.Sandbox`. Clone repo. Detect framework from config (`pytest.ini`, `pyproject.toml`, `setup.cfg`, `package.json`, etc.). Run full suite. Parse pass/fail. Set `all_tests_passed = (failed_count == 0 and exit_code == 0)`. Never run tests in the main web process. |
| **Errors** | Sandbox/clone/timeout failures → `error`; set `all_tests_passed=false` and empty/partial `test_results` as appropriate so routing still works or API marks failed. |

---

### 3.7 `debugger`

| Aspect | Spec |
| --- | --- |
| **Reads** | `test_results`, `file_map`, `file_contents`, `implementation_plan`, `run_id` |
| **Writes** | `debug_report`, `current_agent`, `messages`, `error` |
| **HITL interrupt** | No |
| **Realtime / Supabase** | Start/complete; persist debug report. |
| **Behavior** | Only reached when `all_tests_passed` is false. Per failing test: traceback → root cause → **minimal** targeted fix proposal + confidence. No full rewrites. |
| **Errors** | LLM failures → `error`; still allow PR summarizer to note incomplete debug if graph continues (prefer continue with partial report when possible). |

---

### 3.8 `pr_summarizer`

| Aspect | Spec |
| --- | --- |
| **Reads** | `issue_text`, `subtasks`, `implementation_plan`, `test_results`, `debug_report`, `all_tests_passed`, `run_id` |
| **Writes** | `pr_draft`, `current_agent`, `messages`, `error` |
| **HITL interrupt** | No |
| **Realtime / Supabase** | Start/complete; persist `pr_draft`; set `runs.status=completed` on success. |
| **Behavior** | Compose professional PR: action-oriented title; description; what changed / why; testing notes; limitations; concrete review checklist. Does **not** create the GitHub PR itself—that is the separate API endpoint using this draft. |
| **Errors** | Set `error`; run may be `failed` if draft cannot be produced. |

---

## 4. Edges

| From | To | Type |
| --- | --- | --- |
| `START` | `planner` | unconditional |
| `planner` | `hitl_1` | unconditional |
| `hitl_1` | `code_navigator` | unconditional (after resume with approval) |
| `code_navigator` | `impl_planner` | unconditional |
| `impl_planner` | `hitl_2` | unconditional |
| `hitl_2` | `test_runner` | unconditional (after resume with approval) |
| `test_runner` | `debugger` **or** `pr_summarizer` | **conditional** |
| `debugger` | `pr_summarizer` | unconditional |
| `pr_summarizer` | `END` | unconditional |

### Conditional routing function

```python
def route_after_tests(state: PrismState) -> str:
    if state.get("all_tests_passed") is True:
        return "pr_summarizer"
    return "debugger"
```

Wire as:

```python
graph.add_conditional_edges(
    "test_runner",
    route_after_tests,
    {
        "debugger": "debugger",
        "pr_summarizer": "pr_summarizer",
    },
)
```

---

## 5. Compilation & Checkpointer

```python
# Conceptual — implement exactly in Phase 1
builder = StateGraph(PrismState)
# ... add_node for all 8 nodes ...
# ... add edges as above ...

checkpointer = SupabaseCheckpointer(...)  # durable; survives Modal request lifecycle
app = builder.compile(checkpointer=checkpointer)
```

Requirements:

- Checkpointer **must** be Supabase-backed (or equivalent durable store in Supabase Postgres). In-memory checkpointer is forbidden for production/demo HITL.
- Thread / checkpoint id **must** equal or map 1:1 to `run_id`.
- Compile once per process; reuse the compiled graph (like the LLM factory pattern).

---

## 6. HITL Resume Protocol

After an interrupt, the FastAPI approve route:

1. Validate `run_id` and body (`action`, edited artifacts).
2. If `stop` / `restart`: update Supabase run status; do not resume toward next agent (restart may create a new run).
3. If `approve` / `edit` / `revise`:
   - Call `graph.update_state(config, values={...partial state...}, as_node="hitl_1"|"hitl_2")` as appropriate.
   - Call `graph.stream(None, config, stream_mode=...)` or `ainvoke`/`astream` to continue from checkpoint.
4. Never re-send `github_token` into Supabase; if token is required for later nodes, supply via invocation config / secured run-scoped memory that is not queryable as an `agent_outputs` column.

Config shape:

```python
config = {"configurable": {"thread_id": run_id}}
```

---

## 7. Cross-Cutting Node Rules (Mandatory)

1. Return **partial state** only — never the full state object copy as a substitute for disciplined writes.
2. Persist outputs to Supabase **before** returning from the node.
3. Emit Realtime-visible progress on **start** and **complete** (via table writes that Realtime broadcasts).
4. Log/trace via LangSmith automatically through LangGraph; append human-readable lines to `messages`.
5. On failure: set `state["error"]`; use `logging` module; **never** `print()`; **never** raise uncaught exceptions that silently kill the graph without state update.
6. Import LLM only from `backend/llm.py` factory — never instantiate chat models inline.

---

## 8. Agent → Current Agent Names

Use these exact `current_agent` string values:

| Node | `current_agent` value |
| --- | --- |
| planner | `planner` |
| hitl_1 | `hitl_1` |
| code_navigator | `code_navigator` |
| impl_planner | `impl_planner` |
| hitl_2 | `hitl_2` |
| test_runner | `test_runner` |
| debugger | `debugger` |
| pr_summarizer | `pr_summarizer` |
