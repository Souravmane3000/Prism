# Prism V2 — API notes (Phase 5)

The HTTP surface in [`API.md`](API.md) is unchanged. Phase 5 extends HITL resume and checkpoint payloads on the existing approve route.

## State fields (checkpointed)

| Field | Meaning |
| --- | --- |
| `baseline_sha` | Immutable clone point |
| `current_patch` | Cumulative `StructuredPatch` |
| `patch_origin` | `code_modifier` \| `debugger` |
| `patch_status` | Patch lifecycle (incl. `REVIEWED`, `APPROVED`, `REJECTED`) |
| `verification_attempts` | 0–3; Test Runner only |
| `hitl_revision_count` | 0–2; HITL #3 REQUEST_CHANGES only |
| `review_decision` | `APPROVE` \| `REQUEST_CHANGES` |
| `review_summary` | Reviewer prose summary |
| `reviewer_findings` | Structured findings (severity, file, remediation, …) |
| `reviewed_files` | Files considered by the reviewer |
| `review_risk_level` | `low` \| `medium` \| `high` |
| `human_patch_feedback` | Required on HITL #3 revise |
| `rejected_diff` | Unified diff superseded by REQUEST_CHANGES |
| `session_status` | Includes `AWAITING_HITL_3`, `USER_TERMINATED`, `FAILED_MAX_REVISIONS`, … |

`github_token` is never in state, events, or prompts.

## `POST /api/runs/{id}/approve`

`checkpoint` may be `hitl_1`, `hitl_2`, or `hitl_3`.

### HITL #3

| Action | Body | Result |
| --- | --- | --- |
| `approve` | — | Requires `REVIEWED` + review fields → `APPROVED` → Apply |
| `revise` | `human_feedback` non-empty (stripped) | Else **422**. Else REJECTED + revision loop / max-revisions terminal |
| `stop` | — | `USER_TERMINATED`; DB status `cancelled` |

`edit` / `restart` are rejected for `hitl_3`.

Pause payload includes: unified diff, modified files, review decision/summary/findings, `hitl_revision_count`, `max_hitl_revisions=2`, plan context, `actions_allowed`.

After HITL #2 approve, `next_agent` is `code_modifier`.

## Review events (examples)

`review.diff.completed`:

```json
{
  "summary": "Diff review completed",
  "review_decision": "APPROVE",
  "review_risk_level": "low",
  "finding_count": 0
}
```

`hitl.review.changes_requested`:

```json
{
  "summary": "Human requested patch changes",
  "hitl_revision_count": 1,
  "max_hitl_revisions": 2
}
```

No raw stdout/stderr, full prompts, or PAT.

## Run status mapping

| Graph outcome | `runs.status` |
| --- | --- |
| AWAITING_HITL_3 | `awaiting_approval` |
| USER_TERMINATED | `cancelled` |
| FAILED_MAX_REVISIONS | `failed` |
| Tests PASS (session still RUNNING) | `running` (later PR path may complete) |
| Environment failure / PATCH_ERROR / LLM_ERROR / CONTEXT_OVERFLOW / FAILED_MAX_VERIFICATION | `failed` |

Phase 5 does not add new HTTP routes.
