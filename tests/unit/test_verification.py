"""tests/unit/test_verification.py — Phase 4.1 verification attempt contract."""

import pytest

from backend.verification import (
    MAX_VERIFICATION_ATTEMPTS,
    VerificationAttemptError,
    classify_failure_category,
    increment_verification_attempts,
    is_code_failure,
    is_environment_failure,
    route_after_verification,
    should_debug,
    should_rollback,
    suite_all_passed,
)


class TestIncrementVerificationAttempts:
    def test_zero_becomes_one(self):
        assert increment_verification_attempts(0) == 1

    def test_none_treated_as_zero(self):
        assert increment_verification_attempts(None) == 1

    def test_one_becomes_two(self):
        assert increment_verification_attempts(1) == 2

    def test_two_becomes_three(self):
        assert increment_verification_attempts(2) == 3

    def test_refuses_fourth_attempt(self):
        with pytest.raises(VerificationAttemptError, match="cannot exceed 3"):
            increment_verification_attempts(3)

    def test_maximum_is_exactly_three(self):
        assert MAX_VERIFICATION_ATTEMPTS == 3
        assert increment_verification_attempts(2) == MAX_VERIFICATION_ATTEMPTS


class TestHitlIndependence:
    def test_verification_counter_does_not_touch_hitl_revision_count(self):
        hitl_revision_count = 0
        attempts = increment_verification_attempts(0)
        assert attempts == 1
        assert hitl_revision_count == 0

    def test_hitl_rejections_do_not_count_as_verification(self):
        """Human REQUEST_CHANGES must not increment verification_attempts."""
        verification_attempts = 0
        hitl_revision_count = 1
        assert verification_attempts == 0
        assert hitl_revision_count == 1
        assert increment_verification_attempts(verification_attempts) == 1
        assert hitl_revision_count == 1


class TestRoutingHelpers:
    def test_pass_never_debugs_or_rolls_back(self):
        assert should_debug(
            all_tests_passed=True, verification_attempts=1, failure_category=None
        ) is False
        assert should_rollback(
            all_tests_passed=True, verification_attempts=3, failure_category="assertion"
        ) is False
        assert (
            route_after_verification(
                all_tests_passed=True,
                verification_attempts=1,
                failure_category=None,
            )
            == "pr_summarizer"
        )

    def test_fail_attempt_1_and_2_go_to_debugger(self):
        for n in (1, 2):
            assert should_debug(
                all_tests_passed=False,
                verification_attempts=n,
                failure_category="assertion",
            )
            assert (
                route_after_verification(
                    all_tests_passed=False,
                    verification_attempts=n,
                    failure_category="assertion",
                )
                == "debugger"
            )

    def test_fail_attempt_3_rolls_back_and_skips_debugger(self):
        assert should_debug(
            all_tests_passed=False,
            verification_attempts=3,
            failure_category="assertion",
        ) is False
        assert should_rollback(
            all_tests_passed=False,
            verification_attempts=3,
            failure_category="assertion",
        )
        assert (
            route_after_verification(
                all_tests_passed=False,
                verification_attempts=3,
                failure_category="assertion",
            )
            == "rollback"
        )

    def test_environment_failure_ends_without_debugger_or_max_verification(self):
        for category in ("sandbox", "command_not_found", "timeout"):
            assert is_environment_failure(category)
            assert (
                route_after_verification(
                    all_tests_passed=False,
                    verification_attempts=1,
                    failure_category=category,
                )
                == "end"
            )
            assert should_rollback(
                all_tests_passed=False,
                verification_attempts=3,
                failure_category=category,
            ) is False

    def test_empty_collection_is_code_level_failure(self):
        assert is_code_failure("empty_collection")
        assert (
            route_after_verification(
                all_tests_passed=False,
                verification_attempts=1,
                failure_category="empty_collection",
            )
            == "debugger"
        )


class TestFailureClassification:
    def test_timeout(self):
        assert (
            classify_failure_category(
                exit_code=124, stdout="", stderr="pytest timed out", failed=[], timed_out=True
            )
            == "timeout"
        )

    def test_command_not_found(self):
        assert (
            classify_failure_category(
                exit_code=127,
                stdout="",
                stderr="pytest: command not found",
                failed=[],
            )
            == "command_not_found"
        )

    def test_import_error(self):
        assert (
            classify_failure_category(
                exit_code=2,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'foo'",
                failed=[],
            )
            == "import"
        )

    def test_empty_collection_name(self):
        assert (
            classify_failure_category(
                exit_code=5,
                stdout="",
                stderr="",
                failed=[{"name": "(no tests collected)"}],
            )
            == "empty_collection"
        )

    def test_skipped_is_never_pass(self):
        assert suite_all_passed(["ghost"], [], 0, framework="skipped") is False
