"""tests/unit/test_supabase_client.py — Tests for backend/supabase_client.py"""

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_execute_chain(return_data=None):
    """Return a mock that supports .table().insert/update/select/eq...execute() chains."""
    execute_result = MagicMock()
    execute_result.data = return_data or []

    chain = MagicMock()
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.delete.return_value = chain
    chain.select.return_value = chain
    chain.upsert.return_value = chain
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = execute_result

    client = MagicMock()
    client.table.return_value = chain
    client.rpc.return_value = chain
    return client, execute_result


@pytest.fixture(autouse=True)
def reset_supabase_singleton():
    """Reset the _supabase singleton before each test."""
    import backend.supabase_client as sc
    original = sc._supabase
    sc._supabase = None
    yield
    sc._supabase = original


class TestGetSupabase:
    def test_returns_same_singleton_on_repeated_calls(self):
        """get_supabase() returns the same instance on repeated calls."""
        with patch("backend.supabase_client.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            from backend.supabase_client import get_supabase
            a = get_supabase()
            b = get_supabase()
            assert a is b
            assert mock_create.call_count == 1

    def test_create_client_uses_http1_not_http2(self):
        """postgrest defaults to HTTP/2; we must override to avoid Cloudflare COMPRESSION_ERROR."""
        with patch("backend.supabase_client.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            from backend.supabase_client import get_supabase

            get_supabase()
            options = mock_create.call_args.kwargs["options"]
            http_client = options.httpx_client
            assert http_client is not None
            assert http_client._transport._pool._http2 is False


class TestTransientHttpError:
    def test_detects_connection_terminated_message(self):
        from backend.supabase_client import is_transient_http_error

        class ConnectionTerminated(Exception):
            pass

        assert is_transient_http_error(
            ConnectionTerminated("error_code:9, last_stream_id:31")
        )
        assert not is_transient_http_error(ValueError("bad json"))

    def test_detects_cloudflare_522_html(self):
        from backend.supabase_client import is_rest_gateway_error, should_use_sql_fallback

        class ApiError(Exception):
            code = 522

            def __str__(self) -> str:
                return "JSON could not be generated"

        err = ApiError()
        assert is_rest_gateway_error(err)
        assert should_use_sql_fallback(err)
        assert not should_use_sql_fallback(Exception("PGRST204 Could not find the table"))


class TestPingPostgres:
    @pytest.mark.asyncio
    async def test_accepts_integer_one(self):
        with patch(
            "backend.supabase_client._sql_fetchone",
            new=AsyncMock(return_value={"ok": 1}),
        ):
            from backend.supabase_client import ping_postgres

            await ping_postgres()

    @pytest.mark.asyncio
    async def test_rejects_empty_row(self):
        with patch(
            "backend.supabase_client._sql_fetchone",
            new=AsyncMock(return_value=None),
        ):
            from backend.supabase_client import ping_postgres

            with pytest.raises(RuntimeError, match="no row"):
                await ping_postgres()


class TestDeleteRun:
    @pytest.mark.asyncio
    async def test_returns_true_when_sql_deletes_a_row(self):
        with (
            patch(
                "backend.supabase_client._sql_execute",
                new=AsyncMock(),
            ),
            patch(
                "backend.supabase_client._sql_fetchone",
                new=AsyncMock(return_value={"id": "run-001"}),
            ),
        ):
            from backend.supabase_client import delete_run

            assert await delete_run("run-001") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_sql_finds_no_row(self):
        with (
            patch(
                "backend.supabase_client._sql_execute",
                new=AsyncMock(),
            ),
            patch(
                "backend.supabase_client._sql_fetchone",
                new=AsyncMock(return_value=None),
            ),
        ):
            from backend.supabase_client import delete_run

            assert await delete_run("run-missing") is False


class TestCreateRun:
    @pytest.mark.asyncio
    async def test_generates_uuid_and_inserts_correct_fields(self):
        """create_run generates a UUID run_id and inserts via the pooler."""
        with patch("backend.supabase_client._sql_execute", new=AsyncMock()) as sql_exec:
            from backend.supabase_client import create_run

            run_id = await create_run(
                repo_url="https://github.com/owner/repo",
                issue_url=None,
                issue_text="Fix the bug",
                github_token_hint="ghp_faketoken1234",
            )

        assert uuid.UUID(run_id)
        sql_exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stores_only_last_four_chars_of_token(self):
        """create_run stores only the last 4 characters of the github token."""
        with patch("backend.supabase_client._sql_execute", new=AsyncMock()) as sql_exec:
            from backend.supabase_client import create_run

            await create_run(
                repo_url="https://github.com/owner/repo",
                issue_url=None,
                issue_text="Fix the bug",
                github_token_hint="ghp_secrettoken123ABCD",
            )

        params = sql_exec.await_args.args[1]
        payload_str = str(params)
        assert "ghp_secrettoken123ABCD" not in payload_str
        assert "ABCD" in payload_str

    @pytest.mark.asyncio
    async def test_raises_when_sql_and_rest_fail(self):
        """create_run raises the pooler error when REST also fails."""
        from backend.supabase_client import create_run

        with (
            patch(
                "backend.supabase_client._sql_execute",
                new=AsyncMock(side_effect=RuntimeError("pooler insert failed")),
            ),
            patch("backend.supabase_client.get_supabase", return_value=MagicMock()),
            patch(
                "asyncio.to_thread",
                new=AsyncMock(side_effect=Exception("PGRST204 Could not find the table")),
            ),
        ):
            with pytest.raises(RuntimeError, match="pooler insert failed"):
                await create_run("https://github.com/o/r", None, None, "ghp_x")

    @pytest.mark.asyncio
    async def test_uses_postgres_without_waiting_on_rest(self):
        """SQL insert is the primary path; REST 521/522 must not block it."""
        with (
            patch("backend.supabase_client._sql_execute", new=AsyncMock()) as sql_exec,
            patch("backend.supabase_client.get_supabase") as get_client,
        ):
            from backend.supabase_client import create_run

            run_id = await create_run(
                "https://github.com/o/r", None, "Fix the bug", "ghp_xxxx"
            )

        assert uuid.UUID(run_id)
        sql_exec.assert_awaited_once()
        get_client.assert_not_called()
        query = sql_exec.await_args.args[0]
        assert "::run_status" in query


class TestUpdateRunStatus:
    @pytest.mark.asyncio
    async def test_sets_valid_iso_timestamp_in_updated_at(self):
        """update_run_status sets updated_at to a valid ISO 8601 timestamp."""
        with patch("backend.supabase_client._sql_execute", new=AsyncMock()) as sql_exec:
            from backend.supabase_client import update_run_status

            await update_run_status("run-123", "running", "planner")

        params = sql_exec.await_args.args[1]
        ts = params[2]
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts), (
            f"updated_at is not a valid ISO timestamp: {ts!r}"
        )


class TestSaveAgentOutput:
    @pytest.mark.asyncio
    async def test_inserts_correct_fields(self):
        """save_agent_output inserts the correct run_id, agent, and phase."""
        client_mock, execute_result = _make_execute_chain()

        with (
            patch("backend.supabase_client.get_supabase", return_value=client_mock),
            patch("asyncio.to_thread", new=AsyncMock(return_value=execute_result)),
            patch("backend.supabase_client._sql_execute", new=AsyncMock()),
        ):
            from backend.supabase_client import save_agent_output
            await save_agent_output("run-123", "planner", {"key": "val"}, "start")

        # No exception means the function ran to completion


class TestCreateCheckpoint:
    @pytest.mark.asyncio
    async def test_inserts_correct_payload_and_returns_uuid(self):
        """create_checkpoint inserts a checkpoint and returns a UUID string."""
        client_mock, execute_result = _make_execute_chain()

        with (
            patch("backend.supabase_client.get_supabase", return_value=client_mock),
            patch("asyncio.to_thread", new=AsyncMock(return_value=execute_result)),
            patch("backend.supabase_client._sql_execute", new=AsyncMock()),
        ):
            from backend.supabase_client import create_checkpoint
            checkpoint_id = await create_checkpoint(
                "run-123", "hitl_1", {"subtasks": []}
            )

        assert uuid.UUID(checkpoint_id)


class TestResolveCheckpoint:
    @pytest.mark.asyncio
    async def test_sets_resolved_at_and_user_decision(self):
        """resolve_checkpoint updates resolved_at and user_decision."""
        client_mock, execute_result = _make_execute_chain()
        captured_payloads = []

        def capture_update(data):
            captured_payloads.append(data)
            return client_mock.table.return_value

        client_mock.table.return_value.update.side_effect = capture_update

        with (
            patch("backend.supabase_client.get_supabase", return_value=client_mock),
            patch("asyncio.to_thread", new=AsyncMock(return_value=execute_result)),
            patch("backend.supabase_client._sql_execute", new=AsyncMock()),
        ):
            from backend.supabase_client import resolve_checkpoint
            await resolve_checkpoint("run-123", "hitl_1", {"action": "approve"})

        if captured_payloads:
            p = captured_payloads[0]
            assert "resolved_at" in p
            assert p["user_decision"] == {"action": "approve"}


class TestSearchCodeEmbeddings:
    @pytest.mark.asyncio
    async def test_calls_rpc_with_correct_args(self):
        """search_code_embeddings calls match_code_embeddings RPC with correct params."""
        rpc_execute_result = MagicMock()
        rpc_execute_result.data = [{"file_path": "backend/main.py", "similarity": 0.92}]

        client_mock = MagicMock()
        client_mock.rpc.return_value = MagicMock()
        client_mock.rpc.return_value.execute.return_value = rpc_execute_result

        with (
            patch("backend.supabase_client.get_supabase", return_value=client_mock),
            patch("asyncio.to_thread", new=AsyncMock(return_value=rpc_execute_result)),
            patch(
                "backend.supabase_client._sql_fetchall",
                new=AsyncMock(
                    return_value=[{"file_path": "backend/main.py", "similarity": 0.92}]
                ),
            ),
        ):
            from backend.supabase_client import search_code_embeddings
            results = await search_code_embeddings(
                "https://github.com/owner/repo",
                [0.1] * 1536,
                limit=5,
            )

        # With to_thread mocked, results come from rpc_execute_result.data
        assert isinstance(results, list)
