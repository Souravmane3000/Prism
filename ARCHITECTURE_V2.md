# Prism V2 — Architecture (Phase 5)

Phase 5 adds Diff Reviewer + HITL #3 on top of the Phase 4 apply / verify / recover loop. Tests still never run in the Prism checkout or the FastAPI process.

## Control plane (unchanged)

- FastAPI on Modal drives a compiled `StateGraph`.
- GitHub PAT travels only in `config["configurable"]["github_token"]`.
- Checkpointer: Supabase Postgres (`AsyncPostgresSaver`).
- Visibility: Event Adapter (`backend/events.py`) writes `session_events` and mirrors a subset to `agent_outputs` for the existing stream UI.

## Patch lifecycle (initial vs debugger)

Initial (Code Modifier):

```text
GENERATED → VALIDATED → REVIEWED → APPROVED → APPLIED → (tests) VERIFIED
```

Debugger recovery (bypass HITL #3):

```text
GENERATED → VALIDATED → APPLIED → (tests) VERIFIED
```

Apply reconstructs a clean clone at immutable `baseline_sha` and materializes the cumulative `current_patch`.

## Review gate

- `diff_reviewer` uses structured LLM output (`APPROVE` | `REQUEST_CHANGES` only). Never invents APPROVE on failure.
- On success: `patch_status=REVIEWED`, `session_status=AWAITING_HITL_3`, plus `review_decision`, `review_summary`, `reviewer_findings`, `reviewed_files`, `review_risk_level`.
- HITL #3: Approve / Request Changes (required `human_feedback`) / Stop; revision budget `hitl_revision_count` max 2 (`MAX_HITL_REVISIONS`).
- Code Modifier revision turns consume `rejected_diff`, `reviewer_findings`, and `human_patch_feedback`.

## Verification data

Serialized LangGraph state is enough to reconstruct workspaces:

- `baseline_sha` — immutable default-branch HEAD captured by Planner
- `current_patch` — cumulative `StructuredPatch` (`baseline_sha` → desired tree)
- `verification_attempts` — Test Runner executions only
- `hitl_revision_count` — HITL #3 REQUEST_CHANGES only
- `patch_origin` — `code_modifier` | `debugger`
- `patch_status` — GENERATED / VALIDATED / REVIEWED / APPROVED / APPLIED / VERIFIED / REJECTED / ROLLED_BACK / INVALID
- Review fields: `review_decision`, `review_summary`, `reviewer_findings`, `reviewed_files`, `review_risk_level`
- `session_status` — RUNNING, AWAITING_HITL_3, USER_TERMINATED, FAILED_MAX_REVISIONS, FAILED_MAX_VERIFICATION, PATCH_ERROR, CONTEXT_OVERFLOW, LLM_ERROR, …

There is no durable sandbox handle in `PrismState`. Apply, Test Runner, Debugger, Fix Patch, and Rollback each clone at `baseline_sha` and discard the tree.

## Isolation

| Path | Where code runs |
| --- | --- |
| Production Test Runner | Modal Sandbox, 120s, hardcoded pytest |
| Dev / CI Test Runner | `LocalGitWorkspace` + argv `python -m pytest` (`shell=False`) |
| Apply / Rollback / Fix | Isolated clone; `git apply` / reset; never the Prism working tree |

Commands use argument arrays. Untrusted strings are not interpolated into a shell. Patch paths cannot touch `.git` or escape the repo. Reviewer prompts never include `github_token`.

## Events (metadata only)

Phase 4 events plus:

- `review.diff.started|completed|failed`
- `hitl.review.presented|approved|changes_requested|stopped`
- `revision.started|limit_reached`

Payloads are capped at 64 KiB, reject secret keys/values, and do not include full logs, diffs, or prompts.

## Frontend

Additive HITL #3 gate: DiffViewer (file list + unified diff), review findings panel, Approve / Request Changes / Stop, revision `N / 2`. Pipeline order includes `diff_reviewer` and `hitl_3`. No product redesign.

Phase 6/7 are not implemented.
