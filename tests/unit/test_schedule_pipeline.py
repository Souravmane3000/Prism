"""tests/unit/test_schedule_pipeline.py — Graph jobs must not block the HTTP request."""

from unittest.mock import MagicMock, patch

from backend.routers.runs import _schedule_pipeline


def test_schedule_pipeline_prefers_modal_spawn() -> None:
    fake_fn = MagicMock()
    with (
        patch("backend.routers.runs.settings.environment", "production"),
        patch("modal.Function.from_name", return_value=fake_fn),
    ):
        _schedule_pipeline("run-1", "ghp_testtoken", {"run_id": "run-1"})
    fake_fn.spawn.assert_called_once_with(
        "run-1", "ghp_testtoken", {"run_id": "run-1"}
    )


def test_schedule_pipeline_stays_local_in_development() -> None:
    loop = MagicMock()
    with (
        patch("backend.routers.runs.settings.environment", "development"),
        patch("modal.Function.from_name") as from_name,
        patch("asyncio.get_running_loop", return_value=loop),
        patch("backend.routers.runs._run_graph_background", return_value="coro"),
    ):
        _schedule_pipeline("run-1", "ghp_testtoken", {"run_id": "run-1"})
    from_name.assert_not_called()
    loop.create_task.assert_called_once()


def test_schedule_pipeline_falls_back_to_create_task() -> None:
    loop = MagicMock()
    with (
        patch("modal.Function.from_name", side_effect=RuntimeError("no modal")),
        patch("asyncio.get_running_loop", return_value=loop),
        patch("backend.routers.runs._run_graph_background", return_value="coro"),
    ):
        _schedule_pipeline("run-1", "ghp_testtoken", {"run_id": "run-1"})
    loop.create_task.assert_called_once()
