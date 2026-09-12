# Prism V2 — Audit notes

## §22 Verification & self-correction (Phase 4)

| Check | Status |
| --- | --- |
| Test Runner reconstructs `baseline_sha` + `current_patch` | Done |
| Local skip-as-pass removed | Done |
| Attempt counter 0→1→2→3, refuse 4 | Done — `backend/verification.py` |
| Independent of `hitl_revision_count` | Done |
| PASS never enters Debugger | Done |
| Attempt 3 FAIL → rollback, no Debugger | Done |
| Environment failures skip Debugger and `FAILED_MAX_VERIFICATION` | Done |
| Debug fixes skip Diff Reviewer / HITL #3 | Done |
| `fix_patch` is recovery, not a 9th agent | Done |
| Cumulative diffs vs immutable `baseline_sha` | Done |
| Test PASS does not set `SUCCESS` | Done |
| Rollback primitive does not write `FAILED_MAX_VERIFICATION` | Done |
| `max_verification_failure` after verified rollback | Done |
| Event Adapter payloads only; no new Realtime mechanism | Done |

## §23 Diff Reviewer + HITL #3 (Phase 5)

| Check | Status |
| --- | --- |
| Initial path: validate → Diff Reviewer → HITL #3 → Apply | Done — V2-ADR-026 |
| Debugger origin permanently bypasses review | Done |
| Apply: `APPROVED` always; `VALIDATED` only for debugger; `REVIEWED` blocked | Done |
| Reviewer structured `APPROVE` \| `REQUEST_CHANGES` only | Done |
| Reviewer failure never invents APPROVE / never reaches HITL #3 | Done |
| HITL #3 approve / revise / stop | Done |
| Empty revise feedback → 422 | Done |
| Max 2 revisions; `FAILED_MAX_REVISIONS` | Done |
| STOP → `USER_TERMINATED` (no apply, no rollback) | Done |
| `hitl_revision_count` independent of `verification_attempts` | Done |
| Code Modifier consumes rejected diff + findings + feedback | Done |
| Events: `review.diff.*`, `hitl.review.*`, `revision.*` | Done |
| Frontend DiffViewer + HITL #3 gate | Done |
| Phase 6 eval / Phase 7 polish | **Not implemented** |

DEV-02 (debugger recovery target repo) remains unassigned. EVAL-* are unused; fixtures remain disposable local git trees.
