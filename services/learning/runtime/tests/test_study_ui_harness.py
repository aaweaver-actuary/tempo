"""Infrastructure-free startup regression for the production Study harness."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest


def test_native_study_harness_initializes_fixture_key_before_starting_servers(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "integration/study_tests/test_study_ui.py"
    specification = importlib.util.spec_from_file_location("native_study_harness_under_test", source)
    harness = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(harness)
    monkeypatch.setattr(harness, "activate", lambda *_: None)
    monkeypatch.setattr(harness, "publication_for", lambda _: {})
    keys = []

    class ServersReadyToStart(Exception):
        pass

    def capture_fixture_key(control, key):
        keys.append(key)
        raise ServersReadyToStart

    monkeypatch.setattr(harness, "study_assets", capture_fixture_key)
    case = SimpleNamespace(query=lambda *_: None)
    for _ in range(2):
        with pytest.raises(ServersReadyToStart):
            harness.test_production_study_learning_native(case, tmp_path, "normal")
    assert all(UUID(hex=key).version == 4 for key in keys)
    assert len(set(keys)) == 2
