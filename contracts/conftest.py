"""Contract qualification never treats skipped or zero-selected work as success."""

import pytest


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if session.testscollected == 0:
        session.exitstatus = pytest.ExitCode.NO_TESTS_COLLECTED
    elif reporter is not None and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
