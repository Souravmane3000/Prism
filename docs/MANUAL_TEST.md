# Prism — production manual matrix

Run these on the live app: [https://prism-beta-one.vercel.app](https://prism-beta-one.vercel.app).

After each new pipeline run, open LangSmith project **Prism** (case-sensitive) and filter to **Last 1 hour**. Do not rename the Modal secret `prism-secrets`; that vault name is unrelated.

Suggested fixture for rows 3 and 4 together: a **small public repo you own**, one failing `pytest` test, one GitHub issue. Approve both HITL gates quickly → Debugger runs → Create GitHub PR.

| # | Path | Pass if | Status |
|---|------|---------|--------|
| 1 | **HITL Stop** at checkpoint 1 | Header shows cancelled; no Code Navigator; Recent Runs shows cancelled | You click |
| 2 | **Edit then Approve** at HITL 1 | Changed subtask title appears on the Plan tab after Code Navigator | You click |
| 3 | **Debugger branch** (failing pytest) | Debugger card is not skipped; Debug tab shows traceback + proposed fix; LangSmith has a `debugger` span | You click |
| 4 | **Create GitHub PR** on a repo you own | “View PR on GitHub”; branch `prism/<run_id>`; file `PRISM_REPORT.md`. PAT needs `repo` or `public_repo` | You click |
| 5 | **Paste issue** (not URL) | Planner still produces subtasks | You click |
| 6 | **Reload mid-run / Recent Run** | Same `run_id`; Plan/Files/PR/Debug still filled; HITL still usable if paused | You click |
| 7 | **Wall clock** (small pytest repo, HITL approved immediately) | Start → complete under 5 minutes excluding think-time at checkpoints | You click |
| 8 | **PAT safety** | Password field; `localStorage.prism_sessions` has no token; Modal logs `token=***abcd` only | Code-verified below; spot-check logs |

Skip unless a recruiter demo needs it: invalid PAT, private repo without access, HITL 2 Stop, Jest, mobile layout.

## Row 8 — already enforced in code

- GitHub PAT input is `type="password"` in `frontend/components/input/RunForm.tsx`.
- `SessionRecord` in `frontend/lib/types.ts` has no token field; `localStorage` only stores `run_id`, `repo_url`, `created_at`, `status`.
- `github_token` is not a field on `PrismState` (`tests/unit/test_state.py`).
- DB stores at most the last four characters (`github_token_hint`).
- Logs use `_redact_token` (`***` + last four). Approve/create-pr responses must not echo the PAT.

Spot-check: after a start, `modal app logs prism --tail 50` should never contain `ghp_` / `github_pat_` in full.

## Already proven on the live app (do not re-block on these)

Start run, HITL 1/2 approve, Plan / Files / PR Draft / Debug tabs, Debugger skip when no tests were collected, LangSmith traces under **Prism**, graceful Create-PR 404 on a repo the PAT cannot write.
