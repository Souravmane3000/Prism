# Prism V2 — Architecture (Phase 4)

Phase 4 adds an isolated apply / verify / recover loop on top of the existing LangGraph control plane. Tests never run in the Prism checkout or the FastAPI process.

## Control plane (unchanged)

- FastAPI on Modal drives a compiled `StateGraph`.
- GitHub PAT travels only in `config["configurable"]["github_token"]`.
- Checkpointer: Supabase Postgres (`AsyncPostgresSaver`).
- Visibility: Event Adapter (`backend/events.py`) writes `session_events` and mirrors a subset to `agent_outputs` for the existing stream UI.

## Verification data

Serialized LangGraph state is enough to reconstruct workspaces:

- `baseline_sha` — immutable default-branch HEAD captured by Planner
- `current_patch` — cumulative `StructuredPatch` (`baseline_sha` → desired tree)
- `verification_attempts` — Test Runner executions only
- `patch_origin` — `code_modifier` | `debugger`
- `patch_status` — GENERATED / VALIDATED / APPLIED / VERIFIED / ROLLED_BACK / INVALID
- `session_status` — RUNNING, PATCH_ERROR, FAILED_MAX_VERIFICATION, CONTEXT_OVERFLOW, LLM_ERROR, …

There is no durable sandbox handle in `PrismState`. Apply, Test Runner, Debugger, Fix Patch, and Rollback each clone at `baseline_sha` and discard the tree.

## Isolation

| Path | Where code runs |
| --- | --- |
| Production Test Runner | Modal Sandbox, 120s, hardcoded pytest |
| Dev / CI Test Runner | `LocalGitWorkspace` + argv `python -m pytest` (`shell=False`) |
| Apply / Rollback / Fix | Isolated clone; `git apply` / reset; never the Prism working tree |

Commands use argument arrays. Untrusted strings are not interpolated into a shell. Patch paths cannot touch `.git` or escape the repo.

## Debugger / Fix Patch

Debugger inspects **current applied** files (reconstruct + traceback/`target_files` via `assemble_code_modifier_context`). It emits `DebugReport` plus `can_generate_fix`. Confidence `< 0.7`, missing targets, or no proposed fix → `PATCH_ERROR` / `debugger_no_safe_fix` (no fabricated patch, remaining attempts are not burned with Debugger retries).

Fix Patch turns a safe diagnosis into a focused `ModificationSpec`, then `generate_cumulative_structured_patch`, then the existing validate → apply nodes.

## Events (metadata only)

- `verification.tests.started|completed` — `verification_attempts`, `all_tests_passed`, `failure_category`, summary
- `patch.generation.*` / `patch.validation.*` / `patch.application.*` with `patch_origin`
- `debugger.analysis.started|completed|failed`
- `rollback.started|completed|failed`

Payloads are capped at 64 KiB, reject secret keys/values, and do not include full logs, diffs, or prompts. Test logs are truncated and token-redacted before events and LLM context.

## Frontend

Minimal: additive `TestResults` fields, `can_generate_fix`, new agent names, `frontend/lib/timeline.ts` labels (attempt N, pass/fail tone, debugging, rollback). No Diff Viewer. No HITL #3 UI.

Phase 5/6/7 are not implemented.
