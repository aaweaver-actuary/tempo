"""Cheap feedback holds preserve the integrity owner's complete safety predicate."""

import json
from types import SimpleNamespace

import pytest

from app.services.postgres_integrity import (
    inspect_feedback_integrity_prerequisite,
    integrity_certificate_from_row,
    observe_feedback_integrity_prerequisite,
)


def facts():
    return dict(
        graph_state="ready",
        graph_generation=7,
        integrity_repertoire_id="ruy",
        integrity_status="clean",
        scan_status="idle",
        scan_generation="scan:1",
        checked_at="2026-10-16T12:00:00Z",
        scan_completed_sources=3,
        scan_total_sources=3,
        scan_error=None,
        graph_task_generation=7,
        graph_task_state="complete",
        scan_task_id="scan",
        scan_task_generation=1,
        scan_task_state="complete",
        payload_json=json.dumps(dict(repertoire_id="ruy", graph_generation=7)),
    )


@pytest.mark.parametrize("status", ["clean", "needs_repair"])
def test_current_complete_integrity_is_only_a_hint_and_accepts_unblocked_needs_repair(
    status,
):
    row = facts() | dict(integrity_status=status)
    observation = inspect_feedback_integrity_prerequisite(row, "ruy")
    assert observation.ready and observation.reason == "integrity_current"
    assert integrity_certificate_from_row(row, "ruy", 7) is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("graph_state", None),
        ("graph_generation", True),
        ("graph_task_generation", 8),
        ("graph_task_state", "queued"),
        ("integrity_repertoire_id", None),
        ("integrity_status", "unchecked"),
        ("scan_status", "running"),
        ("scan_task_state", "queued"),
        ("scan_error", "private failure"),
        ("checked_at", None),
        ("checked_at", "2026-10-16T12:00:00"),
        ("checked_at", "invalid"),
        ("scan_completed_sources", True),
        ("scan_total_sources", -1),
        ("scan_completed_sources", 2),
        ("scan_generation", "old:1"),
        ("payload_json", "{"),
        ("payload_json", "[]"),
        ("payload_json", '{"repertoire_id":"other","graph_generation":7}'),
        ("payload_json", '{"repertoire_id":"ruy","graph_generation":true}'),
    ],
)
def test_every_missing_incomplete_or_changed_prerequisite_holds_without_certifying(
    field, value
):
    row = facts() | {field: value}
    result = inspect_feedback_integrity_prerequisite(row, "ruy")
    assert not result.ready
    assert len(result.fingerprint) == 64
    assert (
        result.fingerprint
        != inspect_feedback_integrity_prerequisite(facts(), "ruy").fingerprint
    )
    assert inspect_feedback_integrity_prerequisite(row, "ruy") == result


def test_absent_joins_and_incident_newer_queued_graph_remain_distinguishable_holds():
    assert (
        inspect_feedback_integrity_prerequisite(None, "ruy").reason
        == "integrity_graph_not_ready"
    )
    row = facts() | dict(
        graph_generation=6,
        graph_task_generation=29,
        graph_task_state="queued",
        integrity_status="unchecked",
        checked_at=None,
        scan_generation=None,
    )
    assert (
        inspect_feedback_integrity_prerequisite(row, "ruy").reason
        == "integrity_graph_generation_mismatch"
    )


def test_prerequisite_observer_borrows_exactly_one_read_and_preserves_missing_joins():
    calls = []

    class ReadOnly:
        def execute_native(self, sql, parameters):
            calls.append((sql, parameters))
            assert sql.lstrip().startswith("SELECT")
            assert "LEFT JOIN" in sql and parameters == ("ruy",)
            return SimpleNamespace(fetchone=lambda: facts())

    assert observe_feedback_integrity_prerequisite(ReadOnly(), "ruy").ready
    assert len(calls) == 1
