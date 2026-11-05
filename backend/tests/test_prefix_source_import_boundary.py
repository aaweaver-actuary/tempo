"""Transition preparation uses source owners without importing HTTP routers."""

import os
import subprocess
import sys


def test_transition_preparation_imports_sources_without_http_routers():
    script = """
import importlib.abc
import sys
class RejectHTTP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.prefix_evaluation_api", "app.prefix_transition_api"}:
            raise AssertionError("transition preparation imported HTTP router: " + fullname)
sys.meta_path.insert(0, RejectHTTP())
from app.services import prefix_transition_application as application
from app.services import prefix_evaluation_source, prefix_transition_source
from app.contracts.prefix_errors import PrefixEvaluationForegroundDeferred
assert application.structural is prefix_evaluation_source
assert application.planner_api is prefix_transition_source
assert prefix_evaluation_source.PrefixEvaluationForegroundDeferred is PrefixEvaluationForegroundDeferred
assert application.structural.load_snapshot.__module__ == "app.services.prefix_evaluation_source"
assert application.structural.diagnostic_request.__module__ == "app.services.prefix_evaluation_source"
assert application.planner_api.calculate.__module__ == "app.services.prefix_transition_source"
assert application.planner_api.discover_memberships.__module__ == "app.services.prefix_transition_source"
assert application.planner_api.load_transition_snapshot.__module__ == "app.services.prefix_transition_source"
"""
    subprocess.run(
        [sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20
    )
