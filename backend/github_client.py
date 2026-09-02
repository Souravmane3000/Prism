"""
backend/github_client.py — All GitHub API operations via PyGitHub.

All functions accept a token explicitly — the token is never read from
the module's own state, ensuring it stays in-flight only and is never
accidentally persisted.
"""

import base64
import logging
import re
from typing import Optional

import github
from github import Github
from github.ContentFile import ContentFile
from github.GithubException import GithubException, RateLimitExceededException, UnknownObjectException
from github.Repository import Repository

logger = logging.getLogger(__name__)

# Files larger than this are skipped rather than decoded (avoids OOM on large blobs).
_MAX_FILE_BYTES = 1_048_576  # 1 MB


def get_github_client(token: str) -> Github:
    """Instantiate an authenticated PyGitHub client."""
    return Github(login_or_token=token)


def get_repo(client: Github, repo_url: str) -> Repository:
    """
    Resolve a GitHub repository URL to a PyGitHub Repository object.

    Accepts HTTPS URL (https://github.com/owner/repo) or owner/repo slug.
    """
    try:
        # Strip trailing slash / .git suffix
        cleaned = repo_url.rstrip("/").removesuffix(".git")
        match = re.search(r"github\.com/([^/]+/[^/]+)$", cleaned)
        if match:
            slug = match.group(1)
        else:
            slug = cleaned  # already a slug
        return client.get_repo(slug)
    except RateLimitExceededException as exc:
        logger.error("GitHub rate limit exceeded while fetching repo %s", repo_url)
        raise exc
    except UnknownObjectException as exc:
        logger.error("GitHub repo not found: %s", repo_url)
        raise exc
    except GithubException as exc:
        logger.error("GitHub error fetching repo %s: %s", repo_url, exc)
        raise exc


def get_issue(repo: Repository, issue_url: str) -> dict[str, object]:
    """
    Fetch an issue from GitHub and return a clean dict with key fields.

    Returns: {number, title, body, labels, state, url}
    """
    try:
        # Extract issue number from URL
        match = re.search(r"/issues/(\d+)", issue_url)
        if not match:
            raise ValueError(f"Cannot extract issue number from URL: {issue_url}")
        issue_number = int(match.group(1))
        issue = repo.get_issue(number=issue_number)
        return {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body or "",
            "labels": [label.name for label in issue.labels],
            "state": issue.state,
            "url": issue.html_url,
        }
    except RateLimitExceededException as exc:
        logger.error("GitHub rate limit exceeded fetching issue %s", issue_url)
        raise exc
    except UnknownObjectException as exc:
        logger.error("Issue not found: %s", issue_url)
        raise exc
    except GithubException as exc:
        logger.error("GitHub error fetching issue %s: %s", issue_url, exc)
        raise exc


def get_file_tree(repo: Repository, branch: str = "HEAD") -> list[str]:
    """
    Return a flat list of all file paths in the repository via the Git tree API.

    Uses recursive=True to get the full tree in a single API call.
    Directories are excluded — only blobs (files) are returned.
    """
    try:
        # Resolve to a commit SHA — get_git_tree requires a SHA, not a branch name
        if branch == "HEAD":
            ref_name = repo.default_branch
        else:
            ref_name = branch
        sha = repo.get_branch(ref_name).commit.sha
        tree = repo.get_git_tree(sha=sha, recursive=True)
        return [item.path for item in tree.tree if item.type == "blob"]
    except RateLimitExceededException as exc:
        logger.error("GitHub rate limit exceeded fetching file tree for %s", repo.full_name)
        raise exc
    except GithubException as exc:
        logger.error("GitHub error fetching file tree for %s: %s", repo.full_name, exc)
        raise exc


def get_file_content(repo: Repository, path: str, ref: str = "HEAD") -> Optional[str]:
    """
    Fetch and decode a single file's content from GitHub.

    Returns None for:
    - Binary files (detected by failed UTF-8 decode)
    - Files exceeding _MAX_FILE_BYTES
    - Files not found at the given ref
    """
    try:
        file_content: ContentFile = repo.get_contents(path)  # type: ignore[assignment]
        if isinstance(file_content, list):
            logger.warning("Path %s is a directory, not a file", path)
            return None
        if file_content.size and file_content.size > _MAX_FILE_BYTES:
            logger.info("Skipping %s — size %d exceeds limit", path, file_content.size)
            return None
        if file_content.encoding == "base64" and file_content.content:
            raw = base64.b64decode(file_content.content)
        else:
            raw = file_content.decoded_content
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.info("Skipping %s — binary content", path)
            return None
    except UnknownObjectException:
        logger.warning("File not found in repo: %s", path)
        return None
    except RateLimitExceededException as exc:
        logger.error("GitHub rate limit exceeded fetching file %s", path)
        raise exc
    except GithubException as exc:
        logger.error("GitHub error fetching file %s: %s", path, exc)
        return None


def create_branch(repo: Repository, branch_name: str, base: str = "") -> bool:
    """
    Create a new branch from the given base ref (defaults to repo default branch).

    Returns True on success, False if branch already exists.
    """
    try:
        source_branch = base or repo.default_branch
        source_sha = repo.get_branch(source_branch).commit.sha
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=source_sha)
        logger.info("Created branch %s from %s (%s)", branch_name, source_branch, source_sha[:8])
        return True
    except GithubException as exc:
        if exc.status == 422:
            logger.info("Branch %s already exists", branch_name)
            return False
        logger.error("GitHub error creating branch %s: %s", branch_name, exc)
        raise exc


def commit_file(
    repo: Repository,
    branch: str,
    file_path: str,
    content: str,
    commit_message: str,
) -> str:
    """
    Create or update a file in the repository on the given branch.

    Returns the commit SHA.
    """
    try:
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        try:
            existing = repo.get_contents(file_path, ref=branch)
            if isinstance(existing, list):
                raise ValueError(f"Expected file, got directory at {file_path}")
            result = repo.update_file(
                path=file_path,
                message=commit_message,
                content=encoded,
                sha=existing.sha,
                branch=branch,
            )
        except UnknownObjectException:
            result = repo.create_file(
                path=file_path,
                message=commit_message,
                content=encoded,
                branch=branch,
            )
        sha: str = result["commit"].sha
        logger.info("Committed %s to branch %s — sha=%s", file_path, branch, sha[:8])
        return sha
    except RateLimitExceededException as exc:
        logger.error("GitHub rate limit exceeded while committing %s", file_path)
        raise exc
    except GithubException as exc:
        logger.error("GitHub error committing file %s: %s", file_path, exc)
        raise exc


def create_pull_request(
    repo: Repository,
    title: str,
    body: str,
    head: str,
    base: str = "",
) -> str:
    """
    Open a pull request and return its HTML URL.

    Uses the repo's default branch as base when none is specified.
    """
    try:
        target_base = base or repo.default_branch
        pr = repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=target_base,
        )
        logger.info(
            "Created PR #%d '%s' → %s/%s",
            pr.number,
            title,
            head,
            target_base,
        )
        return pr.html_url
    except RateLimitExceededException as exc:
        logger.error("GitHub rate limit exceeded creating PR")
        raise exc
    except GithubException as exc:
        logger.error("GitHub error creating PR: %s", exc)
        raise exc


def format_github_write_error(exc: GithubException) -> str:
    """
    Short operator-facing message for branch/PR write failures.

    GitHub returns 404 (not 403) when a PAT cannot create refs on a repo the
    caller does not own. Never echo the raw API JSON — it overflows the UI
    and is not actionable.
    """
    status = exc.status
    if status in (404, 403):
        return (
            "GitHub could not create the branch or pull request. "
            "The current PAT cannot write to this repository — Prism cannot open a PR "
            "on a repo you do not have push access to. Use a repository you own or a "
            "fork, with a token that has the repo scope. The PR draft is still available here."
        )
    if status == 401:
        return (
            "GitHub rejected the PAT. Check that the token is valid and has the repo scope."
        )
    if status == 422:
        return (
            "GitHub rejected the branch or pull request "
            "(the ref may already exist, or the base branch is invalid)."
        )
    return (
        "GitHub could not create the pull request. "
        "Try again, or open the PR manually from the draft in this panel."
    )
