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
from typing import Annotated, Literal, NotRequired, Optional, TypedDict


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
    framework: str            # "pytest" | "unittest" | "jest" | "unknown" | "skipped"
    passed: list[str]
    failed: list[TestFailure]
    passed_count: int
    failed_count: int
    exit_code: int
    stdout: str
    stderr: str
    command: NotRequired[str]
    duration_ms: NotRequired[int]
    truncated: NotRequired[bool]
    failure_category: NotRequired[str]


class DebugFix(TypedDict):
    failing_test: str
    root_cause: str
    proposed_fix: str
    confidence: float         # 0.0–1.0
    target_files: list[str]


class DebugReport(TypedDict):
    fixes: list[DebugFix]
    summary: str
    can_generate_fix: NotRequired[bool]


class PatchFileOperation(TypedDict):
    path: str
    operation: Literal["add", "modify", "delete", "rename"]
    old_path: str | None


class StructuredPatch(TypedDict):
    schema_version: Literal["1"]
    patch_id: str
    baseline_sha: str
    unified_diff: str
    modified_files: list[PatchFileOperation]
    checksum_sha256: str


class ReviewerFinding(TypedDict):
    severity: Literal["blocking", "major", "minor"]
    category: Literal[
        "correctness",
        "regression",
        "security",
        "scope",
        "tests",
        "architecture",
        "api",
        "other",
    ]
    file_path: str
    line_start: int | None
    line_end: int | None
    description: str
    why_it_matters: str
    recommended_action: str


ReviewDecision = Literal["APPROVE", "REQUEST_CHANGES"]
ReviewRiskLevel = Literal["low", "medium", "high"]

# Human-requested revision cycles after HITL #3 REQUEST_CHANGES (not verification).
MAX_HITL_REVISIONS = 2

PatchStatus = Literal[
    "GENERATED",
    "VALIDATED",
    "REVIEWED",
    "APPROVED",
    "APPLIED",
    "VERIFIED",
    "REJECTED",
    "INVALID",
    "ROLLED_BACK",
]

PatchOrigin = Literal["code_modifier", "debugger"]

SessionStatus = Literal[
    "RUNNING",
    "AWAITING_HITL_1",
    "AWAITING_HITL_2",
    "AWAITING_HITL_3",
    "SUCCESS",
    "FAILED_MAX_VERIFICATION",
    "FAILED_MAX_REVISIONS",
    "USER_TERMINATED",
    "CONTEXT_OVERFLOW",
    "LLM_ERROR",
    "PATCH_ERROR",
]


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
    baseline_sha: Optional[str]

    # ── HITL 1 ───────────────────────────────────────────────────────────────
    planner_approved: bool

    # ── Code Navigator ────────────────────────────────────────────────────────
    file_map: dict[str, list[FileMapEntry]]   # key = subtask_id
    file_contents: dict[str, str]             # key = file path

    # ── Implementation Planner ────────────────────────────────────────────────
    implementation_plan: list[ImplementationPlanItem]  # editable via hitl_2

    # ── HITL 2 ───────────────────────────────────────────────────────────────
    impl_approved: bool

    # ── Code Modifier / patch lifecycle ───────────────────────────────────────
    current_patch: Optional[StructuredPatch]
    patch_origin: Optional[PatchOrigin]
    patch_status: Optional[PatchStatus]
    modified_files: list[PatchFileOperation]
    reviewer_findings: list[ReviewerFinding]
    review_decision: Optional[ReviewDecision]
    review_summary: Optional[str]
    reviewed_files: list[str]
    review_risk_level: Optional[ReviewRiskLevel]
    human_patch_feedback: Optional[str]
    rejected_diff: Optional[str]
    hitl_revision_count: int
    verification_attempts: int
    final_diff: Optional[str]

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
    session_status: Optional[SessionStatus]
    # Append-only message log shared across all nodes; operator.add concatenates
    # new list entries without replacing the whole field.
    messages: Annotated[list[str], operator.add]


def v2_state_defaults() -> dict[str, object]:
    """Empty V2 fields seeded at run start. Code Modifier must not invent a baseline."""
    return {
        "baseline_sha": None,
        "current_patch": None,
        "patch_origin": None,
        "patch_status": None,
        "modified_files": [],
        "reviewer_findings": [],
        "review_decision": None,
        "review_summary": None,
        "reviewed_files": [],
        "review_risk_level": None,
        "human_patch_feedback": None,
        "rejected_diff": None,
        "hitl_revision_count": 0,
        "verification_attempts": 0,
        "final_diff": None,
        "session_status": None,
    }
