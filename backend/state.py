"""
backend/state.py — PrismState TypedDict and all supporting TypedDicts.

This is the single source of truth for all data flowing through the LangGraph
pipeline. Every field is defined exactly as specified in GRAPH.md §2.
No other module should define state shapes — import from here.

Security: github_token is NOT stored in PrismState. It is carried only via
LangGraph config["configurable"]["github_token"] so the AsyncPostgresSaver
checkpointer never serialises it to PostgreSQL.
"""

import operator
from typing import Annotated, Optional, TypedDict


class Subtask(TypedDict):
    id: str
    title: str
    description: str
    dependencies: list[str]   # subtask ids this task depends on
    likely_files: list[str]   # file paths from the repo tree
    complexity: str           # "low" | "medium" | "high"


class FileMapEntry(TypedDict):
    path: str
    relevance_score: float
    source: str               # "pgvector" | "github" | "both"


class ImplementationStep(TypedDict):
    order: int
    file: str
    function_or_symbol: Optional[str]
    change_description: str
    rationale: str
    tradeoffs: list[str]


class ImplementationPlanItem(TypedDict):
    subtask_id: str
    steps: list[ImplementationStep]


class TestFailure(TypedDict):
    name: str
    traceback: str
    message: str


class TestResults(TypedDict):
    framework: str            # "pytest" | "unittest" | "jest" | "unknown"
    passed: list[str]
    failed: list[TestFailure]
    passed_count: int
    failed_count: int
    exit_code: int
    stdout: str
    stderr: str


class DebugFix(TypedDict):
    failing_test: str
    root_cause: str
    proposed_fix: str
    confidence: float         # 0.0–1.0
    target_files: list[str]


class DebugReport(TypedDict):
    fixes: list[DebugFix]
    summary: str


class PRDraft(TypedDict):
    title: str
    body: str
    what_changed: str
    why: str
    testing_notes: str
    limitations: str
    review_checklist: list[str]


class PrismState(TypedDict):
    # ── Inputs (written at run start by the API seeder) ──────────────────────
    repo_url: str
    issue_url: Optional[str]
    issue_text: Optional[str]
    # github_token is intentionally absent — carried via config["configurable"]
    run_id: str

    # ── Planner ──────────────────────────────────────────────────────────────
    repo_tree: list[str]
    subtasks: list[Subtask]    # may be edited by hitl_1 on resume

    # ── HITL 1 ───────────────────────────────────────────────────────────────
    planner_approved: bool

    # ── Code Navigator ────────────────────────────────────────────────────────
    file_map: dict[str, list[FileMapEntry]]   # key = subtask_id
    file_contents: dict[str, str]             # key = file path

    # ── Implementation Planner ────────────────────────────────────────────────
    implementation_plan: list[ImplementationPlanItem]  # editable via hitl_2

    # ── HITL 2 ───────────────────────────────────────────────────────────────
    impl_approved: bool

    # ── Test Runner ───────────────────────────────────────────────────────────
    test_results: Optional[TestResults]
    all_tests_passed: bool

    # ── Debugger ──────────────────────────────────────────────────────────────
    debug_report: Optional[DebugReport]       # absent/None when skipped

    # ── PR Summarizer ─────────────────────────────────────────────────────────
    pr_draft: Optional[PRDraft]

    # ── Control / observability ───────────────────────────────────────────────
    current_agent: str
    error: Optional[str]
    # Append-only message log shared across all nodes; operator.add concatenates
    # new list entries without replacing the whole field.
    messages: Annotated[list[str], operator.add]
