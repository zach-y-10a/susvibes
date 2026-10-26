"""Tests for LogsCount's fail-closed reading of a run that reports no outcome."""

import logging

import pytest

from susvibes.core.constants import TestStatus
from susvibes.core.logs import LogsCount

LOGGER = logging.getLogger("test")
MOCHA = {"logs_parser": {"FAILED": r"(\d+) failing", "PASSED": r"(\d+) passing"}, "logs_checker": None}
UNITTEST = {"logs_parser": {"FAILED": r"^FAILED \(failures=(\d+)\)$", "PASSED": ""}, "logs_checker": None}


def test_summary_present_is_completed() -> None:
    pf = LogsCount.from_dict(MOCHA).handle("  3 passing (40ms)\n  2 failing\n", False, LOGGER)
    assert pf.status == TestStatus.COMPLETED and pf.failures == 2


def test_all_passing_is_completed_with_zero_failures() -> None:
    pf = LogsCount.from_dict(MOCHA).handle("  5 passing (12ms)\n", False, LOGGER)
    assert pf.completed() and pf.failures == 0


def test_no_summary_is_aborted() -> None:
    pf = LogsCount.from_dict(MOCHA).handle("Error: Cannot find module 'mocha'\n    at Function._resolveFilename\n", False, LOGGER)
    assert pf.aborted()


def test_parser_without_passed_pattern_keeps_legacy_reading() -> None:
    pf = LogsCount.from_dict(UNITTEST).handle("Ran 4 tests in 0.010s\n\nOK\n", False, LOGGER)
    assert pf.completed() and pf.failures == 0


def test_checker_still_wins() -> None:
    handler = LogsCount.from_dict({**MOCHA, "logs_checker": r"^Bail out!"})
    assert handler.handle("Bail out! boom\n  1 passing\n", False, LOGGER).aborted()


# openstack/nova's runner: failure/error-only patterns, and a "Summary:" block between unittest's
# "Ran N tests" line and its final status.
NOVA = {"logs_parser": {"FAILED": r"^Failures:\s*(\d+)", "PASSED": "", "SKIPPED": "",
                        "ERROR": r"^Errors:\s*(\d+)", "XFAIL": ""}, "logs_checker": None}
NOVA_SUMMARY = "Ran {ran} tests in 0.600s\n\nSummary:\nTests run: {ran}\nFailures: {failed}\nErrors: 0\n\n{status}\n"


def test_unittest_summary_block_before_ok_is_completed() -> None:
    logs = NOVA_SUMMARY.format(ran=54, failed=0, status="OK")
    pf = LogsCount.from_dict(NOVA).handle(logs, False, LOGGER)
    assert pf.completed() and pf.failures == 0


def test_unittest_summary_block_before_failed_counts_failures() -> None:
    logs = NOVA_SUMMARY.format(ran=54, failed=2, status="FAILED (failures=2)")
    pf = LogsCount.from_dict(NOVA).handle(logs, False, LOGGER)
    assert pf.completed() and pf.failures == 2


def test_unittest_summary_block_without_status_is_not_a_pass() -> None:
    # No final OK/FAILED: no evidence the run completed, so it can't read as passing.
    logs = "Ran 54 tests in 0.600s\n\nSummary:\nTests run: 54\nFailures: 0\nErrors: 0\n"
    with pytest.raises(RuntimeError, match="No executed test counts"):
        LogsCount.from_dict(NOVA).handle(logs, False, LOGGER)
