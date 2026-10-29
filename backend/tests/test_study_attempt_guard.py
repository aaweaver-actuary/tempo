"""The central conflict guard preserves R11 precedence and pre-C-schema behavior."""
import pytest
from app.contracts.review_conflicts import ReviewConflict
from app.services import study_attempt_guard


def test_current_presentation_guard_precedes_optional_conflict_reads(monkeypatch):
    monkeypatch.setattr(study_attempt_guard,'current_presentation_available',lambda *_:False)
    def forbidden(*_):raise AssertionError('unavailable presentation must stop before C reads')
    monkeypatch.setattr(study_attempt_guard,'installed',forbidden)
    with pytest.raises(ReviewConflict) as rejected:
        study_attempt_guard.guard_current_attempt(object(),1,'old-presentation',1)
    assert rejected.value.code=='current_presentation_unavailable'


def test_current_attempt_without_optional_conflict_schema_retains_existing_path(monkeypatch):
    monkeypatch.setattr(study_attempt_guard,'current_presentation_available',lambda *_:True)
    monkeypatch.setattr(study_attempt_guard,'installed',lambda *_:False)
    def forbidden(*_):raise AssertionError('absent C schema must not construct its writer')
    monkeypatch.setattr(study_attempt_guard,'PostgresConflictStore',forbidden)
    assert study_attempt_guard.guard_current_attempt(object(),1,'current-presentation',1) is None
