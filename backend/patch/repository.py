"""Repository inspection for apply/rollback safety."""

from __future__ import annotations

from typing import TypedDict

from backend.workspace import LocalGitWorkspace, run_git


class MissingBaselineError(ValueError):
    reason = "missing_baseline"


class RepositoryMismatchError(ValueError):
    reason = "stale_repository_state"


class RepositoryInspection(TypedDict):
    root: str
    head_sha: str
    baseline_sha: str
    working_tree_clean: bool
    branch: str
    dirty_paths: list[str]


def require_baseline_sha(baseline_sha: str | None) -> str:
    if not baseline_sha or not str(baseline_sha).strip():
        raise MissingBaselineError("baseline_sha is required")
    return str(baseline_sha).strip()


def inspect_repository_state(
    root: str,
    *,
    baseline_sha: str,
    require_baseline: bool = False,
) -> RepositoryInspection:
    workspace = LocalGitWorkspace.from_existing(root, baseline_sha)
    head = workspace.head_sha()
    if require_baseline and head != baseline_sha:
        raise RepositoryMismatchError("Workspace HEAD does not match session baseline_sha")
    porcelain = workspace.porcelain_status()
    dirty = [line[3:] for line in porcelain.splitlines() if line.strip()]
    try:
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    except Exception:
        branch = "HEAD"
    return RepositoryInspection(
        root=root,
        head_sha=head,
        baseline_sha=baseline_sha,
        working_tree_clean=not porcelain,
        branch=branch,
        dirty_paths=dirty,
    )
