"""Thin-wrapper regressions reuse the existing Learning inventory checker."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize("options", ["-k partial", "-k no_matching_test", "--maxfail=1"])
def test_normal_suite_rejects_partial_selection(options):
    result = subprocess.run([sys.executable, str(HERE.parent / "run_suite.py"), "pure"],
        env={**os.environ, "PYTEST_ADDOPTS": options}, capture_output=True, text=True)
    assert result.returncode == 2 and "inherited PYTEST_ADDOPTS" in result.stderr


@pytest.mark.parametrize("form", ["--junitxml", "--junit-xml"])
def test_normal_suite_routes_complete_junit_inventory(tmp_path, form):
    destination = tmp_path / "results with spaces.xml"
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run([sys.executable, str(HERE.parent / "run_suite.py"), "pure", form, str(destination)],
        env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert destination.is_file() and '"complete": true' in result.stdout
