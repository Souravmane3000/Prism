"""
backend/agents/hitl.py — Human-in-the-Loop checkpoint nodes.

With interrupt_before=["hitl_1", "hitl_2"] set at compile time (graph.py),
LangGraph pauses the graph BEFORE these nodes at the normal post-planner /
post-impl_planner node boundary.  The pause uses LangGraph's reliable
checkpoint write path — NOT the mid-node interrupt() exception handler.

Flow:
  1. planner_node completes → LangGraph saves state → astream ends.
  2. _run_graph_background detects snapshot.next == ("hitl_1",) and calls
     _handle_hitl_pause which sets runs.status="awaiting_approval" and
     writes the hitl_checkpoints record visible to the frontend.
  3. approve_run calls aupdate_state(as_node="hitl_1") to inject the user's
     approval decision, then fires astream(None) in _resume_graph_background.
  4. Because aupdate_state advances the checkpoint past hitl_1, astream(None)
     starts from code_navigator — hitl_1_node body is NEVER actually executed.

hitl_1_node and hitl_2_node are kept as valid registered graph nodes so the
StateGraph topology compiles correctly. Their bodies are dead code in the
aupdate_state+astream(None) pattern.
"""

import logging
from typing import Any

from backend.state import PrismState

logger = logging.getLogger(__name__)


async def hitl_1_node(state: PrismState) -> dict[str, Any]:
    """
    Passthrough node for HITL-1.

    In the interrupt_before+aupdate_state pattern used by approve_run, this
    function body is bypassed entirely — aupdate_state(as_node="hitl_1") moves
    the checkpoint past this node before astream(None) is called.
    """
    run_id: str = state["run_id"]
    logger.info("[hitl_1] Node body reached — run_id=%s (should not occur in normal flow)", run_id)
    return {
        "planner_approved": True,
        "current_agent": "hitl_1",
        "messages": ["[hitl_1] Checkpoint passthrough"],
    }


async def hitl_2_node(state: PrismState) -> dict[str, Any]:
    """
    Passthrough node for HITL-2.

    Same as hitl_1_node — bypassed by aupdate_state(as_node="hitl_2") in
    the approve_run endpoint.
    """
    run_id: str = state["run_id"]
    logger.info("[hitl_2] Node body reached — run_id=%s (should not occur in normal flow)", run_id)
    return {
        "impl_approved": True,
        "current_agent": "hitl_2",
        "messages": ["[hitl_2] Checkpoint passthrough"],
    }
