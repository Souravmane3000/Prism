# Prism V2 — Audit notes

## §22 Verification & self-correction (Phase 4)

| Check | Status |
| --- | --- |
| Apply is wired after `VALIDATED` (no HITL #3) | Done — V2-ADR-025 |
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
| Diff Reviewer / HITL #3 / Phase 6 eval / Phase 7 polish | **Not implemented** |

DEV-02 (debugger recovery target repo) remains unassigned. EVAL-* are unused; Phase 4 uses disposable local git fixtures.
