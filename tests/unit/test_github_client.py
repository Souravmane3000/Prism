"""tests/unit/test_github_client.py — Tests for backend/github_client.py"""

import base64
from unittest.mock import MagicMock, patch

import pytest
from github.GithubException import GithubException, UnknownObjectException


class TestGetRepo:
    def test_parses_full_https_url(self):
        """get_repo correctly parses a full HTTPS GitHub URL."""
        from backend.github_client import get_repo

        client_mock = MagicMock()
        repo_mock = MagicMock()
        client_mock.get_repo.return_value = repo_mock

        result = get_repo(client_mock, "https://github.com/owner/repo")
        client_mock.get_repo.assert_called_once_with("owner/repo")
        assert result is repo_mock

    def test_strips_git_suffix(self):
        """get_repo strips .git suffix from URL."""
        from backend.github_client import get_repo

        client_mock = MagicMock()
        repo_mock = MagicMock()
        client_mock.get_repo.return_value = repo_mock

        get_repo(client_mock, "https://github.com/owner/repo.git")
        client_mock.get_repo.assert_called_once_with("owner/repo")

    def test_accepts_bare_slug(self):
        """get_repo accepts a bare owner/repo slug."""
        from backend.github_client import get_repo

        client_mock = MagicMock()
        client_mock.get_repo.return_value = MagicMock()

        get_repo(client_mock, "owner/repo")
        client_mock.get_repo.assert_called_once_with("owner/repo")

    def test_raises_on_github_exception(self):
        """get_repo propagates GithubException."""
        from backend.github_client import get_repo

        client_mock = MagicMock()
        client_mock.get_repo.side_effect = GithubException(404, {"message": "Not Found"}, None)

        with pytest.raises(GithubException):
            get_repo(client_mock, "https://github.com/owner/repo")


class TestGetIssue:
    def test_extracts_issue_number_from_url(self):
        """get_issue extracts the issue number from a GitHub issue URL."""
        from backend.github_client import get_issue

        issue_mock = MagicMock()
        issue_mock.number = 42
        issue_mock.title = "Bug: Something is broken"
        issue_mock.body = "Description of the bug"
        issue_mock.labels = []
        issue_mock.state = "open"
        issue_mock.html_url = "https://github.com/owner/repo/issues/42"

        repo_mock = MagicMock()
        repo_mock.get_issue.return_value = issue_mock

        result = get_issue(repo_mock, "https://github.com/owner/repo/issues/42")
        repo_mock.get_issue.assert_called_once_with(number=42)
        assert result["number"] == 42
        assert result["title"] == "Bug: Something is broken"

    def test_raises_value_error_for_malformed_url(self):
        """get_issue raises ValueError for a URL without an issue number."""
        from backend.github_client import get_issue

        repo_mock = MagicMock()
        with pytest.raises(ValueError, match="Cannot extract issue number"):
            get_issue(repo_mock, "https://github.com/owner/repo/pulls/5")


class TestGetFileTree:
    def test_returns_flat_list_excluding_directories(self):
        """get_file_tree returns only file blobs, not tree/directory entries."""
        from backend.github_client import get_file_tree

        blob_entry = MagicMock()
        blob_entry.path = "backend/main.py"
        blob_entry.type = "blob"

        tree_entry = MagicMock()
        tree_entry.path = "backend"
        tree_entry.type = "tree"

        tree_mock = MagicMock()
        tree_mock.tree = [blob_entry, tree_entry]

        branch_mock = MagicMock()
        branch_mock.commit.sha = "abc123"

        repo_mock = MagicMock()
        repo_mock.default_branch = "main"
        repo_mock.get_branch.return_value = branch_mock
        repo_mock.get_git_tree.return_value = tree_mock

        result = get_file_tree(repo_mock)
        assert "backend/main.py" in result
        assert "backend" not in result  # tree entry excluded


class TestGetFileContent:
    def test_returns_none_for_files_over_1mb(self):
        """get_file_content returns None for files exceeding 1 MB."""
        from backend.github_client import get_file_content

        file_mock = MagicMock()
        file_mock.size = 2_000_000  # 2 MB
        repo_mock = MagicMock()
        repo_mock.get_contents.return_value = file_mock

        result = get_file_content(repo_mock, "bigfile.bin")
        assert result is None

    def test_returns_none_for_binary_files(self):
        """get_file_content returns None for files that fail UTF-8 decode."""
        from backend.github_client import get_file_content

        file_mock = MagicMock()
        file_mock.size = 100
        file_mock.encoding = "base64"
        file_mock.content = base64.b64encode(b"\xff\xfe").decode()  # invalid UTF-8
        repo_mock = MagicMock()
        repo_mock.get_contents.return_value = file_mock

        result = get_file_content(repo_mock, "binary.bin")
        assert result is None

    def test_returns_none_for_404(self):
        """get_file_content returns None when file is not found."""
        from backend.github_client import get_file_content

        repo_mock = MagicMock()
        repo_mock.get_contents.side_effect = UnknownObjectException(404, {}, None)

        result = get_file_content(repo_mock, "missing.py")
        assert result is None

    def test_returns_decoded_content(self):
        """get_file_content decodes base64 content and returns UTF-8 string."""
        from backend.github_client import get_file_content

        content = b"from fastapi import FastAPI\napp = FastAPI()\n"
        file_mock = MagicMock()
        file_mock.size = len(content)
        file_mock.encoding = "base64"
        file_mock.content = base64.b64encode(content).decode()
        repo_mock = MagicMock()
        repo_mock.get_contents.return_value = file_mock

        result = get_file_content(repo_mock, "main.py")
        assert result == content.decode("utf-8")


class TestCreateBranch:
    def test_returns_false_when_branch_already_exists(self):
        """create_branch returns False when the branch already exists (422)."""
        from backend.github_client import create_branch

        repo_mock = MagicMock()
        branch_mock = MagicMock()
        branch_mock.commit.sha = "abc123"
        repo_mock.get_branch.return_value = branch_mock
        repo_mock.default_branch = "main"
        repo_mock.create_git_ref.side_effect = GithubException(422, {"message": "Reference already exists"}, None)

        result = create_branch(repo_mock, "prism/test-branch")
        assert result is False

    def test_returns_true_on_success(self):
        """create_branch returns True when the branch is successfully created."""
        from backend.github_client import create_branch

        repo_mock = MagicMock()
        branch_mock = MagicMock()
        branch_mock.commit.sha = "abc123"
        repo_mock.get_branch.return_value = branch_mock
        repo_mock.default_branch = "main"
        repo_mock.create_git_ref.return_value = MagicMock()

        result = create_branch(repo_mock, "prism/new-branch")
        assert result is True


class TestCommitFile:
    def test_calls_create_file_for_new_path(self):
        """commit_file calls create_file when the path does not exist."""
        from backend.github_client import commit_file

        repo_mock = MagicMock()
        repo_mock.get_contents.side_effect = UnknownObjectException(404, {}, None)
        commit_result = MagicMock()
        commit_result.sha = "newsha123"
        repo_mock.create_file.return_value = {"commit": commit_result}

        sha = commit_file(repo_mock, "main", "REPORT.md", "# Report", "chore: add report")
        repo_mock.create_file.assert_called_once()
        assert sha == "newsha123"

    def test_calls_update_file_for_existing_path(self):
        """commit_file calls update_file when the path already exists."""
        from backend.github_client import commit_file

        existing = MagicMock()
        existing.sha = "existingsha"

        repo_mock = MagicMock()
        repo_mock.get_contents.return_value = existing
        commit_result = MagicMock()
        commit_result.sha = "updatedsha"
        repo_mock.update_file.return_value = {"commit": commit_result}

        sha = commit_file(repo_mock, "main", "REPORT.md", "# Updated", "chore: update report")
        repo_mock.update_file.assert_called_once()
        assert sha == "updatedsha"


class TestFormatGithubWriteError:
    def test_404_does_not_echo_api_json(self):
        from backend.github_client import format_github_write_error

        exc = GithubException(
            404,
            {
                "message": "Not Found",
                "documentation_url": "https://docs.github.com/rest/git/refs#create-a-reference",
                "status": "404",
            },
            None,
        )
        message = format_github_write_error(exc)
        assert "documentation_url" not in message
        assert "create-a-reference" not in message
        assert "{" not in message
        assert "cannot write" in message.lower()

    def test_401_mentions_pat(self):
        from backend.github_client import format_github_write_error

        message = format_github_write_error(GithubException(401, {"message": "Bad credentials"}, None))
        assert "PAT" in message or "token" in message.lower()
