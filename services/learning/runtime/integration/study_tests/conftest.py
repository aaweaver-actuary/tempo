"""Reuse the regular Learning fixture's database, roles and cleanup."""
import importlib.util
from pathlib import Path
import sys

regular_tests = Path(__file__).resolve().parents[2] / "tests"
sys.path.insert(0, str(regular_tests))
specification = importlib.util.spec_from_file_location("learning_study_fixtures", regular_tests / "conftest.py")
fixtures = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = fixtures
specification.loader.exec_module(fixtures)
case = fixtures.case
from test_game_evidence_runtime import producer
