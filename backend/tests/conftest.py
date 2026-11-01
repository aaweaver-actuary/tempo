"""Explicit policy seam for standalone harnesses without a settings database."""

import pytest


def pytest_configure():
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()


@pytest.fixture
def normal_study_mode(monkeypatch):
    """Keep unrelated mocked worker proofs in normal scheduling mode.

    Study-only regressions use real saved settings and do not request this fixture.
    """
    from app import tasks
    from app.services import study_mode

    monkeypatch.setattr(study_mode, "study_only_enabled", lambda database=None: False)
    monkeypatch.setattr(tasks, "study_only_enabled", lambda database=None: False)
