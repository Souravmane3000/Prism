# Prism V2 — Architectural decisions

Log of V2 decisions. Treat as binding unless a later ADR explicitly supersedes an entry.

---

## V2-ADR-025 — Phase 4 executable adapter wires Apply → Test Runner (no HITL #3)

**Decision:** Production routing after a validated patch (Phase 4 staging) was:

```text
code_modifier → validate_patch → apply_patch → test_runner
```

Debug recovery was:

```text
debugger → fix_patch → validate_patch → apply_patch → test_runner
```

Phase 4 did **not** implement Diff Reviewer or HITL #3. Test PASS does not mark the session `SUCCESS`.

**Context:** Canonical V2 still has Diff Reviewer → HITL #3 before the *initial* apply. Phase 4 wired Apply early so verification could run. **Superseded for the initial path by V2-ADR-026**; debugger bypass remains (V2-ADR-008).

**Reasons:**

- Test Runner must reconstruct `baseline_sha` + `current_patch`, not clone an unmodified tip.
- Debug fixes must not wait on an unimplemented review UI.
- `fix_patch` is a recovery stage, not a ninth product agent.

**Consequences (still true where not overridden):**

- Attempt accounting lives in `backend/verification.py` (`MAX_VERIFICATION_ATTEMPTS = 3`).
- Debugger-origin patches permanently bypass HITL #3.

---

## V2-ADR-026 — Phase 5 Diff Reviewer + HITL #3 on the initial path only

**Decision:** After `validate_patch`:

```text
VALIDATED + patch_origin=code_modifier → diff_reviewer → hitl_3 → (APPROVED) apply_patch
VALIDATED + patch_origin=debugger      → apply_patch   # permanent bypass
```

Apply accepts:

- `APPROVED` always (human-approved initial patch)
- `VALIDATED` **only when** `patch_origin == "debugger"`
- `REVIEWED` is blocked (human must approve)

HITL #3 actions: `approve` | `revise` | `stop`. `revise` requires non-empty stripped `human_feedback` (422 otherwise). Max **2** revisions via `hitl_revision_count` (`MAX_HITL_REVISIONS`), independent of `verification_attempts`. STOP → `USER_TERMINATED`. Over-limit → `FAILED_MAX_REVISIONS`. Neither applies nor rolls back.

Diff Reviewer structured decision is `APPROVE` | `REQUEST_CHANGES` only. Reviewer `APPROVE` never auto-applies; it only advances to HITL #3 (`patch_status=REVIEWED`, `session_status=AWAITING_HITL_3`).

**Context:** Restores the canonical human gate before Apply for Code Modifier patches without undoing Phase 4 debugger recovery.

**Reasons:**

- No unreviewed initial patch reaches Apply.
- Reviewer APPROVE is advisory for the human; never auto-applies.
- Empty revise feedback is rejected so human intent is explicit (findings alone are insufficient).
- Counters stay separate so verification and revision budgets cannot steal from each other.

**Consequences:**

- `interrupt_before=["hitl_1","hitl_2","hitl_3"]`.
- Pause uses `session_status=AWAITING_HITL_3` and existing `runs.status=awaiting_approval`.
- Events: `review.diff.*`, `hitl.review.*`, `revision.started|limit_reached` via Event Adapter only.
- After HITL #2 approve, `next_agent` is `code_modifier`.
