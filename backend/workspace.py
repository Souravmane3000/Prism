"""Isolated git workspace for patch apply, rollback, and local verification.

Never targets the Prism working tree. Commands use argv arrays (shell=False).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from backend.patch.paths import UnsafePathError, normalize_repo_path

logger = logging.getLogger(__name__)

GIT_COMMAND_TIMEOUT_SECONDS = 30
_PRISM_MARKERS = ("backend/graph.py", "backend/state.py", "pyproject.toml")


class WorkspaceError(RuntimeError):
    reason = "workspace_error"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


def run_git(args: list[str], *, cwd: str, timeout: int = GIT_COMMAND_TIMEOUT_SECONDS) -> str:
    """Run git with an argument array. Never interpolates untrusted strings into a shell."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError("git command timed out", reason="timeout") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise WorkspaceError(
            stderr or f"git {' '.join(args)} failed",
            reason="git_failure",
        ) from exc
    return (completed.stdout or "").strip()


def assert_isolated_repository(root: str) -> None:
    """Refuse to mutate the Prism checkout itself."""
    resolved = Path(root).resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or cwd in resolved.parents or resolved in cwd.parents:
        # Allow temp clones even if they sit under the project tmp dir, but
        # never the live Prism working tree (identified by source markers).
        if all((resolved / marker).exists() for marker in _PRISM_MARKERS):
            raise WorkspaceError(
                "Refusing to mutate the Prism working tree",
                reason="isolation_violation",
            )


def _authenticated_clone_url(repo_url: str, token: str) -> str:
    parsed = urlparse(repo_url)
    if parsed.scheme not in {"http", "https"}:
        raise WorkspaceError("Only HTTPS repository URLs are supported", reason="unsafe_url")
    authed = parsed._replace(netloc=f"x-token:{token}@{parsed.netloc}")
    url = authed.geturl()
    if not url.endswith(".git"):
        url += ".git"
    return url


class LocalGitWorkspace:
    def __init__(self, root: str, baseline_sha: str, *, cleanup: bool) -> None:
        assert_isolated_repository(root)
        self.root = root
        self.baseline_sha = baseline_sha
        self._cleanup = cleanup
        self._closed = False

    @classmethod
    def clone(cls, repo_url: str, token: str, baseline_sha: str) -> LocalGitWorkspace:
        tmp = tempfile.mkdtemp(prefix="prism-ws-")
        url = _authenticated_clone_url(repo_url, token)
        try:
            run_git(["clone", "--no-tags", url, tmp], cwd=tempfile.gettempdir(), timeout=120)
            run_git(["checkout", "--detach", baseline_sha], cwd=tmp)
            run_git(["reset", "--hard", baseline_sha], cwd=tmp)
            run_git(["clean", "-fd"], cwd=tmp)
            run_git(["config", "user.email", "prism-sandbox@example.com"], cwd=tmp)
            run_git(["config", "user.name", "Prism Sandbox"], cwd=tmp)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return cls(tmp, baseline_sha, cleanup=True)

    @classmethod
    def from_existing(cls, root: str, baseline_sha: str) -> LocalGitWorkspace:
        return cls(root, baseline_sha, cleanup=False)

    @classmethod
    def init_fixture(cls, files: dict[str, str], *, commit_message: str = "baseline") -> LocalGitWorkspace:
        """Create a disposable local git repo. Used by tests only."""
        tmp = tempfile.mkdtemp(prefix="prism-fixture-")
        run_git(["init"], cwd=tmp)
        run_git(["config", "user.email", "prism-test@example.com"], cwd=tmp)
        run_git(["config", "user.name", "Prism Test"], cwd=tmp)
        for rel, content in files.items():
            path = Path(tmp) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        run_git(["add", "-A"], cwd=tmp)
        run_git(["commit", "-m", commit_message], cwd=tmp)
        sha = run_git(["rev-parse", "HEAD"], cwd=tmp)
        return cls(tmp, sha, cleanup=True)

    def file_exists(self, rel: str) -> bool:
        path = Path(self.root) / normalize_repo_path(rel)
        return path.exists()

    def read_file(self, rel: str) -> str | None:
        path = Path(self.root) / normalize_repo_path(rel)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise UnsafePathError(f"Unsupported or binary file cannot be patched: {rel}") from exc

    def write_file(self, rel: str, content: str) -> None:
        path = Path(self.root) / normalize_repo_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def delete_file(self, rel: str) -> None:
        path = Path(self.root) / normalize_repo_path(rel)
        if path.is_file():
            path.unlink()

    def apply_check(self, unified_diff: str) -> None:
        self._git_apply(unified_diff, check=True)

    def apply_diff(self, unified_diff: str) -> None:
        self._git_apply(unified_diff, check=False)

    def _git_apply(self, unified_diff: str, *, check: bool) -> None:
        args = ["apply", "--check"] if check else ["apply"]
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.root,
                input=unified_diff,
                check=True,
                capture_output=True,
                text=True,
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                shell=False,
            )
            _ = completed
        except subprocess.CalledProcessError as exc:
            raise WorkspaceError(
                "Patch could not be applied in the workspace",
                reason="git_failure",
            ) from exc

    def stage_all(self) -> None:
        run_git(["add", "-A"], cwd=self.root)

    def head_sha(self) -> str:
        return run_git(["rev-parse", "HEAD"], cwd=self.root)

    def porcelain_status(self) -> str:
        return run_git(["status", "--porcelain"], cwd=self.root)

    def working_tree_clean(self) -> bool:
        return not self.porcelain_status()

    def diff_against(self, sha: str) -> str:
        """Diff index+working tree against sha. Stage new files first so adds appear."""
        cached = run_git(["diff", "--cached", sha], cwd=self.root)
        if cached.strip():
            return cached
        return run_git(["diff", sha], cwd=self.root)

    def name_status_against(self, sha: str) -> str:
        cached = run_git(["diff", "--cached", "--name-status", sha], cwd=self.root)
        if cached.strip():
            return cached
        return run_git(["diff", "--name-status", sha], cwd=self.root)

    def reset_to_baseline(self) -> None:
        run_git(["reset", "--hard", self.baseline_sha], cwd=self.root)
        run_git(["clean", "-fd"], cwd=self.root)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._cleanup:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> LocalGitWorkspace:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
