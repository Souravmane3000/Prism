"""tests/unit/test_hitl_pause.py — HITL interrupt helpers used by the runs router."""

from types import SimpleNamespace

from backend.routers.runs import _paused_hitl_node


def test_paused_hitl_node_detects_hitl_1() -> None:
    snapshot = SimpleNamespace(next=("hitl_1",))
    assert _paused_hitl_node(snapshot) == "hitl_1"


def test_paused_hitl_node_detects_hitl_2() -> None:
    snapshot = SimpleNamespace(next=("hitl_2", "test_runner"))
    assert _paused_hitl_node(snapshot) == "hitl_2"


def test_paused_hitl_node_ignores_other_nodes() -> None:
    snapshot = SimpleNamespace(next=("code_navigator",))
    assert _paused_hitl_node(snapshot) is None


def test_paused_hitl_node_empty_next() -> None:
    snapshot = SimpleNamespace(next=())
    assert _paused_hitl_node(snapshot) is None


def test_paused_hitl_node_missing_next() -> None:
    snapshot = SimpleNamespace()
    assert _paused_hitl_node(snapshot) is None
