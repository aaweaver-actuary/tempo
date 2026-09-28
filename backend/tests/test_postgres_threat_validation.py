"""Restart and lease regressions for PostgreSQL defensive validation."""

from __future__ import annotations

from contextlib import contextmanager

from app import tasks
from app.services import threat_pipeline
from app.services.threat_validation import ValidationResult


def test_postgres_threat_validation_commits_result_and_admission_with_lease(monkeypatch):
    assert "defensive_threat_validate" in tasks._SUPPORTED_BACKGROUND_KINDS
    task = {"id": "validation-task", "generation": 2, "lease_token": "current",
            "payload": {"candidate_id": "candidate-one"}}
    candidate = {"id": "candidate-one", "game_id": "game-one",
                 "analysis_version": 3, "current_version": 3,
                 "source_fingerprint": "source-one", "superseded_at": None,
                 "evidence_json": '{"seed":{},"anchor":{}}', "policy_json": "{}"}
    writes = []
    enqueued = []
    lease_current = True

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(())

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM threat_training_candidates c" in statement:
                return Cursor(candidate)
            if "FROM threat_training_candidates candidate" in statement:
                return Cursor(candidate)
            if statement.lstrip().startswith("UPDATE threat_training_candidates"):
                writes.append(parameters)
            return Cursor()

    @contextmanager
    def read_database():
        yield Database()

    @contextmanager
    def write_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(threat_pipeline.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(threat_pipeline.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(threat_pipeline, "background_read_connection", read_database)
    monkeypatch.setattr(threat_pipeline, "connection", write_database)
    monkeypatch.setattr(threat_pipeline, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(threat_pipeline, "_seed_from_json", lambda _value: object())
    monkeypatch.setattr(threat_pipeline, "_anchor_from_json", lambda _value: object())
    monkeypatch.setattr(threat_pipeline, "make_validation_plan", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(threat_pipeline, "validate_threat_anchor",
                        lambda *_args: ValidationResult("engine_supported", "validated"))
    monkeypatch.setattr(threat_pipeline, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))
    monkeypatch.setattr(threat_pipeline, "complete_task_slice_in_transaction",
                        lambda _database, _task: True)

    assert threat_pipeline.execute_threat_validation(task)
    assert len(writes) == 1
    assert enqueued[0][0] == "defensive_admission"
    assert enqueued[0][3] == 150

    lease_current = False
    assert not threat_pipeline.execute_threat_validation(task)
    assert len(writes) == 1
    assert len(enqueued) == 1
