# Prism V2 — Architectural decisions

Log of V2 decisions. Treat as binding unless a later ADR explicitly supersedes an entry.

---

## V2-ADR-025 — Phase 4 executable adapter wires Apply → Test Runner (no HITL #3)

**Decision:** Production routing after a validated patch is:

```text
code_modifier → validate_patch → apply_patch → test_runner
```

Debug recovery is:

```text
debugger → fix_patch → validate_patch → apply_patch → test_runner
```

Phase 4 does **not** implement Diff Reviewer or HITL #3. `VALIDATED` is not treated as `APPROVED`. Test PASS does not mark the session `SUCCESS`.

**Context:** Canonical V2 still has Diff Reviewer → HITL #3 before the *initial* apply. To verify real applied code now, Apply must run. This supersedes only the earlier “do not wire apply” staging clause. Phase 5 will insert the review gate on the **initial** path only; debugger-origin patches permanently bypass HITL #3 (V2-ADR-008).

**Reasons:**

- Test Runner must reconstruct `baseline_sha` + `current_patch`, not clone an unmodified tip.
- Debug fixes must not wait on an unimplemented review UI.
- `fix_patch` is a recovery stage, not a ninth product agent.

**Consequences:**

- `interrupt_before=["hitl_1","hitl_2"]` only.
- Apply accepts `VALIDATED` (and `APPROVED` if a later phase sets it).
- Attempt accounting lives in `backend/verification.py` (`MAX_VERIFICATION_ATTEMPTS = 3`).
