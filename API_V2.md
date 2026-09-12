# Prism V2 — API notes (Phase 4)

The HTTP surface in [`API.md`](API.md) is unchanged. Phase 4 adds event payloads and state fields consumed via existing Realtime / `GET /api/runs/{id}/output`.

## State fields (checkpointed)

| Field | Meaning |
| --- | --- |
| `baseline_sha` | Immutable clone point |
| `current_patch` | Cumulative `StructuredPatch` |
| `patch_origin` | `code_modifier` \| `debugger` |
| `patch_status` | Patch lifecycle |
| `verification_attempts` | 0–3; Test Runner only |
| `session_status` | Includes `FAILED_MAX_VERIFICATION`, `PATCH_ERROR`, `CONTEXT_OVERFLOW`, `LLM_ERROR` |
| `test_results.command` | Additive |
| `test_results.duration_ms` | Additive |
| `test_results.truncated` | Additive |
| `test_results.failure_category` | `assertion` \| `compile` \| `import` \| `command_not_found` \| `timeout` \| `sandbox` \| `empty_collection` \| `unknown` |
| `debug_report.can_generate_fix` | Additive; false means no Fix Patch |

`github_token` is never in state, events, or prompts.

## `verification.tests.completed` payload

```json
{
  "summary": "Verification attempt 2 — tests failed",
  "verification_attempts": 2,
  "all_tests_passed": false,
  "failure_category": "assertion",
  "passed_count": 3,
  "failed_count": 1
}
```

No raw stdout/stderr, diffs, or PAT.

## `debugger.analysis.completed` / `failed`

Completed: `{ "summary": "...", "fix_count": 1 }`  
Failed: `{ "summary": "...", "reason": "debugger_no_safe_fix" }` (or `context_overflow` / `llm_error`)

## Run status mapping

| Graph outcome | `runs.status` |
| --- | --- |
| Tests PASS (session still RUNNING) | `running` (later PR path may complete) |
| Environment failure / PATCH_ERROR / LLM_ERROR / CONTEXT_OVERFLOW / FAILED_MAX_VERIFICATION | `failed` |

Phase 4 does not add new HTTP routes.
