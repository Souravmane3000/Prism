"""
tests/unit/test_frontend_backend_contract.py

Alignment tests between:
  - backend/state.py TypedDicts
  - backend/routers/runs.py RunOutputResponse
  - backend/config.py Settings (API keys, not Modal tokens)
  - backend/llm.py (OpenAI, not Modal inference)
  - frontend/lib/types.ts
  - frontend/lib/output.ts classifyTestResults

These tests exist so Debug / PR Draft cannot drift from what GET /output returns.
"""

import re
from pathlib import Path
from typing import get_type_hints

import pytest

ROOT = Path(__file__).resolve().parents[2]
TS_TYPES = ROOT / "frontend" / "lib" / "types.ts"
TS_OUTPUT = ROOT / "frontend" / "lib" / "output.ts"
LLM_PY = ROOT / "backend" / "llm.py"


def _ts_interface_fields(source: str, name: str) -> set[str]:
    """Extract top-level field names from `export interface Name { ... }`."""
    match = re.search(
        rf"export interface {re.escape(name)}\s*\{{(.*?)\n\}}",
        source,
        re.DOTALL,
    )
    assert match, f"interface {name} not found in frontend/lib/types.ts"
    body = match.group(1)
    fields = set(re.findall(r"^\s{2}(\w+)\??:", body, re.MULTILINE))
    return fields


def _typed_dict_keys(cls: type) -> set[str]:
    return set(get_type_hints(cls).keys())


class TestSchemaAlignment:
    def test_types_ts_exists(self):
        assert TS_TYPES.is_file()
        assert TS_OUTPUT.is_file()

    def test_test_results_fields_match(self):
        from backend.state import TestResults

        ts = TS_TYPES.read_text(encoding="utf-8")
        assert _typed_dict_keys(TestResults) == _ts_interface_fields(ts, "TestResults")

    def test_debug_report_and_fix_fields_match(self):
        from backend.state import DebugFix, DebugReport

        ts = TS_TYPES.read_text(encoding="utf-8")
        assert _typed_dict_keys(DebugReport) == _ts_interface_fields(ts, "DebugReport")
        assert _typed_dict_keys(DebugFix) == _ts_interface_fields(ts, "DebugFix")

    def test_pr_draft_fields_match(self):
        from backend.state import PRDraft

        ts = TS_TYPES.read_text(encoding="utf-8")
        assert _typed_dict_keys(PRDraft) == _ts_interface_fields(ts, "PRDraft")

    def test_run_output_response_has_inspector_keys(self):
        """GET /output must include every field the right-panel tabs read."""
        from backend.routers.runs import RunOutputResponse

        fields = set(RunOutputResponse.model_fields.keys())
        required = {
            "run_id",
            "status",
            "subtasks",
            "file_map",
            "test_results",
            "all_tests_passed",
            "debug_report",
            "pr_draft",
            "pr_url",
            "error",
        }
        missing = required - fields
        assert not missing, f"RunOutputResponse missing {missing}"


class TestAuthIsApiKeysNotModalTokens:
    def test_settings_has_no_modal_credential_fields(self):
        from backend.config import Settings

        names = {name.lower() for name in Settings.model_fields}
        for banned in ("modal_token", "modal_auth_key", "modal_api_key", "modaltoken"):
            assert banned not in names

    def test_settings_requires_openai_and_supabase_keys(self):
        from backend.config import Settings

        names = set(Settings.model_fields)
        assert "openai_api_key" in names
        assert "supabase_url" in names
        assert "supabase_service_key" in names

    def test_llm_factory_source_uses_openai_not_modal_auth(self):
        src = LLM_PY.read_text(encoding="utf-8")
        assert "openai_api_key" in src
        assert "ChatOpenAI" in src
        assert "MODAL_AUTH_KEY" not in src
        assert "modal_auth" not in src.lower()
        assert "http2=False" in src
        assert "streaming=False" in src

    def test_supabase_client_forces_http1(self):
        src = (ROOT / "backend" / "supabase_client.py").read_text(encoding="utf-8")
        assert "http2=False" in src
        assert "is_transient_http_error" in src

    def test_env_example_documents_api_keys_not_modal_token(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "OPENAI_API_KEY" in example
        assert "SUPABASE_URL" in example
        assert "MODAL_TOKEN" not in example
        assert "MODAL_AUTH_KEY" not in example


class TestClassifyTestResults:
    """Python classifier must match the cases Debug tab uses."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (None, "missing"),
            (
                {
                    "framework": "skipped",
                    "passed": [],
                    "failed": [],
                    "passed_count": 0,
                    "failed_count": 0,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "local development",
                },
                "skipped",
            ),
            (
                {
                    "framework": "unknown",
                    "passed": [],
                    "failed": [],
                    "passed_count": 0,
                    "failed_count": 0,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "Token missing. Could not authenticate client.",
                },
                "did_not_run",
            ),
            (
                {
                    "framework": "unknown",
                    "passed": [],
                    "failed": [],
                    "passed_count": 0,
                    "failed_count": 0,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                },
                "did_not_run",
            ),
            (
                {
                    "framework": "pytest",
                    "passed": [],
                    "failed": [],
                    "passed_count": 0,
                    "failed_count": 0,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                },
                "passed",
            ),
            (
                {
                    "framework": "pytest",
                    "passed": ["test_ok"],
                    "failed": [],
                    "passed_count": 1,
                    "failed_count": 0,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                },
                "passed",
            ),
            (
                {
                    "framework": "pytest",
                    "passed": [],
                    "failed": [{"name": "test_x", "traceback": "err", "message": "fail"}],
                    "passed_count": 0,
                    "failed_count": 1,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "",
                },
                "failed",
            ),
        ],
    )
    def test_python_classifier(self, payload, expected):
        from backend.test_outcome import classify_test_results

        assert classify_test_results(payload) == expected

    def test_frontend_classifier_mentions_same_outcomes(self):
        src = TS_OUTPUT.read_text(encoding="utf-8")
        assert "export function classifyTestResults" in src
        for outcome in ("missing", "skipped", "did_not_run", "failed", "passed"):
            assert f'"{outcome}"' in src or f"'{outcome}'" in src

    def test_frontend_unknown_empty_suite_is_did_not_run(self):
        src = TS_OUTPUT.read_text(encoding="utf-8")
        assert '!collected && (framework === "unknown"' in src

    def test_hitl_card_stays_hidden_after_later_agents(self):
        src = (ROOT / "frontend" / "lib" / "hitlVisibility.ts").read_text(
            encoding="utf-8"
        )
        assert "export function shouldRenderHitlCard" in src
        assert "laterAgentsStarted" in src
        assert "resolvedCheckpoints.has(agent)" in src
        page = (ROOT / "frontend" / "app" / "page.tsx").read_text(encoding="utf-8")
        assert "setApprovedCheckpoint(null)" not in page
        assert "resolvedCheckpoints" in page

    def test_inspector_merges_agent_outputs_when_api_output_is_empty(self):
        src = TS_OUTPUT.read_text(encoding="utf-8")
        assert "export function materializeRunOutput" in src
        realtime = (ROOT / "frontend" / "lib" / "useSupabaseRealtime.ts").read_text(
            encoding="utf-8"
        )
        assert "hydrateFromSupabase" in realtime
        assert "setTimeout(() => unsubscribe(), 2000)" not in realtime
        vis = (ROOT / "frontend" / "lib" / "hitlVisibility.ts").read_text(
            encoding="utf-8"
        )
        assert "laterPipelineAgentCompleted" in vis

    def test_skipped_debugger_and_pr_error_are_handled_in_ui(self):
        vis = (ROOT / "frontend" / "lib" / "hitlVisibility.ts").read_text(
            encoding="utf-8"
        )
        assert "export function isDebuggerSkippedInStream" in vis
        assert "export function optimisticNextAgent" in vis
        stream = (ROOT / "frontend" / "components" / "stream" / "ActivityStream.tsx").read_text(
            encoding="utf-8"
        )
        assert "isDebuggerSkippedInStream" in stream
        assert "optimisticNextAgent" in stream
        pr_tab = (ROOT / "frontend" / "components" / "output" / "PRDraftTab.tsx").read_text(
            encoding="utf-8"
        )
        assert 'base_branch: "main"' not in pr_tab
        assert 'overflowWrap: "break-word"' in pr_tab
        assert "break-all" not in pr_tab
        assert "anywhere" not in pr_tab
        assert "formatCreatePRError" in pr_tab
        hitl = (ROOT / "frontend" / "components" / "stream" / "HITLCard.tsx").read_text(
            encoding="utf-8"
        )
        assert "Checkpoint 1 of 2" in hitl
        assert "Checkpoint 2 of 2" in hitl
        assert "break-all" not in hitl
        graph_src = (ROOT / "backend" / "graph.py").read_text(encoding="utf-8")
        assert graph_src.find("backend.config") < graph_src.find("from langgraph")
        modal_src = (ROOT / "modal_app.py").read_text(encoding="utf-8")
        assert "configure_langsmith_tracing" in modal_src
        assert "flush_langsmith_traces" in modal_src
        runs_src = (ROOT / "backend" / "routers" / "runs.py").read_text(encoding="utf-8")
        assert "def _graph_run_config" in runs_src
        assert '"run_name"' in runs_src
        assert "tracing_context" in runs_src
        assert "flush_langsmith_traces" in runs_src
        config_src = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
        assert "LANGSMITH_TRACING" in config_src
        assert "get_env_var.cache_clear" in config_src
        assert "LANGSMITH_UI_PROJECT" in config_src
        assert "flush_langsmith_traces" in config_src
        assert "LANGCHAIN_CALLBACKS_BACKGROUND" in config_src

    def test_session_record_excludes_pat_and_ci_workflow_exists(self):
        types_src = TS_TYPES.read_text(encoding="utf-8")
        match = re.search(
            r"export interface SessionRecord\s*\{(.*?)\n\}",
            types_src,
            re.DOTALL,
        )
        assert match, "SessionRecord missing from types.ts"
        body = match.group(1)
        assert "run_id" in body
        assert "github_token" not in body
        assert "pat" not in body.lower()
        workflow = ROOT / ".github" / "workflows" / "pytest.yml"
        assert workflow.is_file()
        assert "pytest tests/" in workflow.read_text(encoding="utf-8")
        assert (ROOT / "docs" / "MANUAL_TEST.md").is_file()
        runform = (ROOT / "frontend" / "components" / "input" / "RunForm.tsx").read_text(
            encoding="utf-8"
        )
        assert 'type="password"' in runform
