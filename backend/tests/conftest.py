"""Explicit policy seam for standalone harnesses without a settings database."""

import pytest


@pytest.fixture
def normal_study_mode(monkeypatch):
    """Keep unrelated mocked worker proofs in normal scheduling mode.

    Study-only regressions use real saved settings and do not request this fixture.
    """
    from app import tasks
    from app.services import study_mode

    monkeypatch.setattr(study_mode, "study_only_enabled", lambda database=None: False)
    monkeypatch.setattr(tasks, "study_only_enabled", lambda database=None: False)
