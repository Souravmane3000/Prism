"""tests/unit/test_test_results_contract.py — Phase 4.2 Test Runner result contract."""

from typing import get_type_hints

from backend.state import TestResults
from backend.verification import suite_all_passed


class TestTestResultsAdditiveFields:
    def test_required_fields_remain(self):
        keys = set(get_type_hints(TestResults))
        for required in (
            "framework",
            "passed",
            "failed",
            "passed_count",
            "failed_count",
            "exit_code",
            "stdout",
            "stderr",
        ):
            assert required in keys

    def test_additive_fields_exist(self):
        keys = set(get_type_hints(TestResults))
        for additive in ("command", "duration_ms", "truncated", "failure_category"):
            assert additive in keys

    def test_can_instantiate_without_additive_fields(self):
        results = TestResults(
            framework="pytest",
            passed=["test_ok"],
            failed=[],
            passed_count=1,
            failed_count=0,
            exit_code=0,
            stdout="1 passed",
            stderr="",
        )
        assert results["exit_code"] == 0
        assert "failure_category" not in results


class TestPassRule:
    def test_pass_requires_collection_and_zero_exit(self):
        assert suite_all_passed(["test_ok"], [], 0) is True

    def test_failed_test_is_fail(self):
        assert suite_all_passed([], [{"name": "test_x"}], 1) is False

    def test_empty_collection_is_fail(self):
        assert suite_all_passed([], [], 0) is False

    def test_nonzero_exit_is_fail_even_with_passes(self):
        assert suite_all_passed(["test_ok"], [], 1) is False

    def test_skipped_framework_is_never_pass(self):
        assert suite_all_passed([], [], 0, framework="skipped") is False
        assert suite_all_passed(["ghost"], [], 0, framework="skipped") is False
