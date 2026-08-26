"""tests/agents/test_code_navigator.py — Tests for backend/agents/code_navigator.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_config(token: str = "ghp_faketoken") -> dict:
    return {"configurable": {"thread_id": "run-001", "github_token": token}}


@pytest.fixture()
def mock_nav_supabase():
    """Patch all async supabase functions used by code_navigator."""
    with (
        patch("backend.agents.code_navigator.update_run_status", new=AsyncMock()),
        patch("backend.agents.code_navigator.save_agent_output", new=AsyncMock()),
        patch("backend.agents.code_navigator.get_repo_cache", new=AsyncMock(return_value=None)),
        patch("backend.agents.code_navigator.save_code_embeddings", new=AsyncMock()),
        patch("backend.agents.code_navigator.upsert_repo_cache", new=AsyncMock()),
        patch("backend.agents.code_navigator.search_code_embeddings", new=AsyncMock(return_value=[])),
    ):
        yield


@pytest.fixture()
def mock_nav_github():
    """Patch github helpers for code_navigator."""
    repo_mock = MagicMock()
    file_content = MagicMock()
    file_content.size = 200
    file_content.encoding = "base64"
    file_content.content = "ZnJvbSBmYXN0YXBpIGltcG9ydCBGYXN0QVBJ"
    file_content.decoded_content = b"from fastapi import FastAPI\napp = FastAPI()\n"
    repo_mock.get_contents.return_value = file_content

    with (
        patch("backend.agents.code_navigator.get_github_client", return_value=MagicMock()),
        patch("backend.agents.code_navigator.get_repo", return_value=repo_mock),
        patch("backend.agents.code_navigator.get_file_content", return_value="from fastapi import FastAPI"),
    ):
        yield repo_mock


@pytest.fixture()
def mock_embeddings():
    """Patch OpenAIEmbeddings to return fake vectors."""
    embeddings_mock = MagicMock()
    embeddings_mock.aembed_documents = AsyncMock(return_value=[[0.1] * 1536])
    embeddings_mock.aembed_query = AsyncMock(return_value=[0.1] * 1536)
    with patch("backend.agents.code_navigator._get_embeddings_client", return_value=embeddings_mock):
        yield embeddings_mock


class TestChunkText:
    def test_splits_at_token_boundary(self):
        """_chunk_text produces multiple chunks for long text."""
        from backend.agents.code_navigator import _chunk_text

        long_text = "word " * 2000  # well over 400 tokens
        chunks = _chunk_text(long_text, max_tokens=400)
        assert len(chunks) > 1

    def test_single_chunk_for_short_text(self):
        """_chunk_text returns one chunk for short text."""
        from backend.agents.code_navigator import _chunk_text

        short = "def hello(): pass"
        chunks = _chunk_text(short)
        assert len(chunks) == 1


class TestKeywordMatchScore:
    def test_returns_zero_for_no_overlap(self):
        """_keyword_match_score returns 0.0 when no keywords overlap."""
        from backend.agents.code_navigator import _keyword_match_score
        from backend.state import Subtask

        subtask = Subtask(
            id="st-1", title="Add authentication", description="Implement JWT auth",
            dependencies=[], likely_files=[], complexity="medium"
        )
        score = _keyword_match_score("migrations/001_initial.sql", subtask)
        assert score == 0.0

    def test_returns_positive_for_keyword_match(self):
        """_keyword_match_score returns > 0 when path keywords overlap with subtask."""
        from backend.agents.code_navigator import _keyword_match_score
        from backend.state import Subtask

        subtask = Subtask(
            id="st-1", title="Add authentication middleware",
            description="Implement JWT authentication middleware",
            dependencies=[], likely_files=[], complexity="medium"
        )
        score = _keyword_match_score("backend/middleware/authentication.py", subtask)
        assert score > 0.0


class TestShouldSkipFile:
    def test_skips_png(self):
        from backend.agents.code_navigator import _should_skip_file
        assert _should_skip_file("assets/logo.png") is True

    def test_skips_min_js(self):
        from backend.agents.code_navigator import _should_skip_file
        assert _should_skip_file("static/app.min.js") is True

    def test_does_not_skip_python_files(self):
        from backend.agents.code_navigator import _should_skip_file
        assert _should_skip_file("backend/main.py") is False

    def test_does_not_skip_markdown(self):
        from backend.agents.code_navigator import _should_skip_file
        assert _should_skip_file("README.md") is False


class TestSelectEmbedFiles:
    def test_caps_and_skips_docs_bulk(self):
        """Large translated-docs trees are excluded; source files are kept."""
        from backend.agents.code_navigator import _select_embed_files, _MAX_EMBED_FILES

        tree = (
            [f"docs/de/docs/page-{i}.md" for i in range(200)]
            + [f"fastapi/routing_{i}.py" for i in range(20)]
            + ["README.md"]
        )
        subtasks = [
            {
                "id": "st-1",
                "title": "Fix routing",
                "description": "Update routing",
                "dependencies": [],
                "likely_files": ["fastapi/applications.py"],
                "complexity": "medium",
            }
        ]
        # likely_files not in tree is ignored; py files selected
        selected = _select_embed_files(tree, subtasks)
        assert len(selected) <= _MAX_EMBED_FILES
        assert all(not p.startswith("docs/") for p in selected)
        assert any(p.endswith(".py") for p in selected)

    def test_always_includes_likely_files_even_under_docs(self):
        from backend.agents.code_navigator import _select_embed_files

        tree = ["docs/en/docs/tutorial/dependencies.md", "fastapi/main.py"]
        subtasks = [
            {
                "id": "st-1",
                "title": "Docs",
                "description": "Update docs",
                "dependencies": [],
                "likely_files": ["docs/en/docs/tutorial/dependencies.md"],
                "complexity": "low",
            }
        ]
        selected = _select_embed_files(tree, subtasks)
        assert "docs/en/docs/tutorial/dependencies.md" in selected
        assert "fastapi/main.py" in selected


class TestCodeNavigatorNode:
    @pytest.mark.asyncio
    async def test_returns_file_map_and_required_keys(
        self, mock_nav_supabase, mock_nav_github, mock_embeddings
    ):
        """code_navigator_node returns file_map, file_contents, current_agent."""
        with patch(
            "backend.agents.code_navigator.get_repo_cache",
            new=AsyncMock(return_value={"embedding_count": 5}),
        ):
            from backend.agents.code_navigator import code_navigator_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "subtasks": [
                    {
                        "id": "st-1",
                        "title": "Add rate limiting",
                        "description": "Implement rate limiting",
                        "dependencies": [], "likely_files": [], "complexity": "medium"
                    }
                ],
                "repo_tree": ["backend/main.py"],
            }

            result = await code_navigator_node(state, _make_config())

        assert "file_map" in result
        assert "file_contents" in result
        assert result["current_agent"] == "code_navigator"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_embedding(
        self, mock_nav_supabase, mock_nav_github, mock_embeddings
    ):
        """When repo cache exists, _embed_and_cache_repo is NOT called."""
        embed_calls = []

        async def fake_embed(repo_url, file_tree, token, subtasks=None):
            embed_calls.append(True)

        with (
            patch("backend.agents.code_navigator.get_repo_cache",
                  new=AsyncMock(return_value={"embedding_count": 10})),
            patch("backend.agents.code_navigator._embed_and_cache_repo",
                  side_effect=fake_embed),
        ):
            from backend.agents.code_navigator import code_navigator_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "subtasks": [{
                    "id": "st-1", "title": "Fix bug", "description": "Fix it",
                    "dependencies": [], "likely_files": [], "complexity": "low"
                }],
                "repo_tree": ["backend/main.py"],
            }

            await code_navigator_node(state, _make_config())

        assert len(embed_calls) == 0, "Should skip embedding when cache hit"

    @pytest.mark.asyncio
    async def test_cache_miss_calls_embed(
        self, mock_nav_supabase, mock_nav_github, mock_embeddings
    ):
        """When no cache, _embed_and_cache_repo is called."""
        embed_calls = []

        async def fake_embed(repo_url, file_tree, token, subtasks=None):
            embed_calls.append(True)

        with (
            patch("backend.agents.code_navigator.get_repo_cache",
                  new=AsyncMock(return_value=None)),
            patch("backend.agents.code_navigator._embed_and_cache_repo",
                  side_effect=fake_embed),
        ):
            from backend.agents.code_navigator import code_navigator_node

            state = {
                "run_id": "run-001",
                "repo_url": "https://github.com/owner/repo",
                "subtasks": [{
                    "id": "st-1", "title": "Fix bug", "description": "Fix it",
                    "dependencies": [], "likely_files": [], "complexity": "low"
                }],
                "repo_tree": ["backend/main.py"],
            }

            await code_navigator_node(state, _make_config())

        assert len(embed_calls) == 1, "Should call embed on cache miss"

    @pytest.mark.asyncio
    async def test_returns_error_on_no_subtasks(self, mock_nav_supabase):
        """code_navigator_node returns error field when subtasks list is empty."""
        from backend.agents.code_navigator import code_navigator_node

        state = {
            "run_id": "run-001",
            "repo_url": "https://github.com/owner/repo",
            "subtasks": [],
            "repo_tree": [],
        }

        result = await code_navigator_node(state, _make_config())
        assert "error" in result
        assert result["current_agent"] == "code_navigator"
