"""
backend/graph.py — LangGraph StateGraph assembly.

Implements the full pipeline as specified in GRAPH.md exactly:
  START → planner → hitl_1 → code_navigator → impl_planner → hitl_2
        → test_runner → (debugger?) → pr_summarizer → END

Compiled once at module level with an AsyncPostgresSaver checkpointer backed
by the Supabase PostgreSQL database. The compiled graph is imported by
backend/routers/runs.py for all pipeline interactions.

The Supabase-backed checkpointer is mandatory: Modal serverless workers are
stateless across requests, so in-memory checkpointers cannot survive the
request boundary between POST /start and POST /approve.
"""

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from psycopg_pool import AsyncConnectionPool

from backend.agents.code_navigator import code_navigator_node
from backend.agents.debugger import debugger_node
from backend.agents.hitl import hitl_1_node, hitl_2_node
from backend.agents.implementation_planner import implementation_planner_node
from backend.agents.planner import planner_node
from backend.agents.pr_summarizer import pr_summarizer_node
from backend.agents.test_runner import test_runner_node
from backend.config import postgres_connect_kwargs
from backend.state import PrismState

logger = logging.getLogger(__name__)


def route_after_tests(state: PrismState) -> str:
    """
    Conditional routing function after test_runner.

    Reads all_tests_passed from state:
    - True  → skip debugger, go directly to pr_summarizer
    - False → run debugger first

    Also routes to pr_summarizer if there is an error in the state to allow
    the pipeline to produce a partial PR summary even when testing failed
    infrastructurally (e.g. sandbox could not clone — error already set).
    """
    if state.get("all_tests_passed") is True:
        logger.info("[graph] All tests passed — routing to pr_summarizer")
        return "pr_summarizer"
    logger.info("[graph] Tests failed or error — routing to debugger")
    return "debugger"


def build_graph() -> StateGraph:
    """Construct the StateGraph with all nodes and edges per GRAPH.md §4."""
    builder: StateGraph = StateGraph(PrismState)

    # ── Register nodes ─────────────────────────────────────────────────────────
    # Node names must exactly match the current_agent strings in GRAPH.md §8
    builder.add_node("planner", planner_node)
    builder.add_node("hitl_1", hitl_1_node)
    builder.add_node("code_navigator", code_navigator_node)
    builder.add_node("impl_planner", implementation_planner_node)
    builder.add_node("hitl_2", hitl_2_node)
    builder.add_node("test_runner", test_runner_node)
    builder.add_node("debugger", debugger_node)
    builder.add_node("pr_summarizer", pr_summarizer_node)

    # ── Unconditional edges ────────────────────────────────────────────────────
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "hitl_1")
    builder.add_edge("hitl_1", "code_navigator")
    builder.add_edge("code_navigator", "impl_planner")
    builder.add_edge("impl_planner", "hitl_2")
    builder.add_edge("hitl_2", "test_runner")
    builder.add_edge("debugger", "pr_summarizer")
    builder.add_edge("pr_summarizer", END)

    # ── Conditional edge after test_runner ────────────────────────────────────
    builder.add_conditional_edges(
        "test_runner",
        route_after_tests,
        {
            "debugger": "debugger",
            "pr_summarizer": "pr_summarizer",
        },
    )

    return builder


async def get_checkpointer() -> AsyncPostgresSaver:
    """
    Create and set up an AsyncPostgresSaver backed by Supabase PostgreSQL.

    langgraph-checkpoint-postgres v2.x changed from_conn_string() to return an
    async context manager, so we use AsyncConnectionPool directly to get a real
    AsyncPostgresSaver instance that can be owned by the module-level compiled graph.
    The pool outlives this function; its lifetime is tied to _compiled_graph.
    """
    kwargs = postgres_connect_kwargs()
    logger.info(
        "[graph] Opening Postgres pool — host=%s port=%s user=%s db=%s",
        kwargs["host"],
        kwargs["port"],
        kwargs["user"],
        kwargs["dbname"],
    )

    pool = AsyncConnectionPool(
        conninfo="",
        min_size=1,
        max_size=5,
        timeout=10,
        reconnect_timeout=0,
        kwargs=kwargs,
        open=False,
    )
    try:
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        return checkpointer
    except Exception:
        await pool.close()
        raise


# ── Module-level compiled graph ────────────────────────────────────────────────
# The graph is compiled lazily on first access via get_compiled_graph() to allow
# the async checkpointer setup to complete inside an event loop. Routers call
# get_compiled_graph() at startup.

_compiled_graph: CompiledStateGraph | None = None


async def get_compiled_graph() -> CompiledStateGraph:
    """
    Return the compiled LangGraph application, initialising it on first call.

    This is called once during FastAPI startup via the lifespan handler in main.py.
    Subsequent calls return the cached instance.
    """
    global _compiled_graph
    if _compiled_graph is None:
        logger.info("[graph] Compiling StateGraph with Supabase checkpointer")
        checkpointer = await get_checkpointer()
        builder = build_graph()
        # interrupt_before is LangGraph's primary HITL mechanism.  It saves
        # state at the normal post-planner node boundary (not mid-node), which
        # is far more reliable than interrupt() inside an async node.
        _compiled_graph = builder.compile(
            checkpointer=checkpointer,
            interrupt_before=["hitl_1", "hitl_2"],
        )
        logger.info("[graph] StateGraph compiled successfully")
    return _compiled_graph
