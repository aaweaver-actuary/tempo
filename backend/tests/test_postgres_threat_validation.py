"""Restart and lease regressions for PostgreSQL defensive validation."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import chess

from app import tasks
from app.services import threat_pipeline, threat_training
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


def test_postgres_threat_backfill_advances_one_game_and_completes_after_repertoires(monkeypatch):
    assert "defensive_threat_backfill" in tasks._SUPPORTED_BACKGROUND_KINDS
    game = {"id": "game-one", "analysis_version": 4}
    queued = []
    advanced = []
    completed = []
    lease_current = True

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, _parameters=()):
            if "FROM imported_games" in statement:
                return Cursor(game if _parameters[0] == "" else None)
            if "FROM repertoires" in statement:
                return Cursor(None)
            raise AssertionError(statement)

    @contextmanager
    def database_connection(*, background=True):
        assert background
        yield Database()

    monkeypatch.setattr(threat_pipeline.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(threat_pipeline.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(threat_pipeline, "background_read_connection", database_connection)
    monkeypatch.setattr(threat_pipeline, "connection", database_connection)
    monkeypatch.setattr(threat_pipeline, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(threat_pipeline, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    monkeypatch.setattr(threat_pipeline, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append((next_phase, next_payload)) or True)
    monkeypatch.setattr(threat_pipeline, "complete_task_slice_in_transaction",
                        lambda _database, _task: completed.append(True) or True)

    claimed = {"id": "backfill", "generation": 1, "lease_token": "live",
               "payload": {"phase": "games", "cursor": ""}}
    assert threat_pipeline.execute_threat_backfill_slice(claimed)
    assert queued == [("defensive_threat_scan", "game-one",
                       {"game_id": "game-one", "analysis_version": 4, "cursor": 0}, 145)]
    assert advanced == [("games", {"phase": "games", "cursor": "game-one"})]

    lease_current = False
    assert not threat_pipeline.execute_threat_backfill_slice(claimed)
    assert len(queued) == 1

    lease_current = True
    claimed["payload"] = {"phase": "repertoires", "cursor": ""}
    assert threat_pipeline.execute_threat_backfill_slice(claimed)
    assert completed == [True]


def test_postgres_defense_admission_handoffs_queue_only_under_current_lease(monkeypatch):
    assert "defensive_admission" in tasks._SUPPORTED_BACKGROUND_KINDS
    today = date.today().isoformat()
    candidate = {
        "id": "candidate-one", "incident_id": "incident-one", "card_id": None,
        "validation_state": "engine_supported", "approved_at": None, "paused_at": None,
        "evidence_json": '{"anchor":{},"seed":{}}',
    }
    reports = [
        {"role": role, "request_json": "{}", "report_json": "{}"}
        for role in ("best", "historical")
    ]
    admitted = []
    queued = []
    advanced = []
    lease_current = True

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row = row
            self.rows = rows

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute(self, statement, _parameters=()):
            if "defense_new_cards_per_day" in statement:
                return Cursor((5,))
            if "COUNT(*)" in statement:
                return Cursor((0,))
            if "FROM threat_training_candidates c JOIN imported_games" in statement:
                return Cursor(candidate)
            if "FROM threat_candidate_requests relation" in statement:
                return Cursor(rows=reports)
            if "FROM threat_training_candidates" in statement and "WHERE c.id=?" in statement:
                return Cursor(candidate)
            return Cursor()

    @contextmanager
    def database_connection(*, background=True):
        assert background
        yield Database()

    monkeypatch.setattr(threat_training.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(threat_training.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(threat_training, "background_read_connection", database_connection)
    monkeypatch.setattr(threat_training, "connection", database_connection)
    monkeypatch.setattr(threat_training, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(threat_training, "_anchor_from_json", lambda _value:
                        SimpleNamespace(position=SimpleNamespace(
                            start_fen=chess.STARTING_FEN, prefix_uci=())))
    monkeypatch.setattr(threat_training, "_seed_from_json", lambda _value: object())
    monkeypatch.setattr(threat_training, "_request_from_json", lambda _value: object())
    monkeypatch.setattr(threat_training, "report_from_json", lambda _value:
                        SimpleNamespace(lines=(object(),)))
    monkeypatch.setattr(threat_training, "validate_analysis_report", lambda *_args: None)
    monkeypatch.setattr(threat_training, "recognition_preview", lambda *_args: object())
    monkeypatch.setattr(threat_training, "_candidate", lambda _database, _id: candidate)
    monkeypatch.setattr(threat_training, "_approve_in_transaction",
                        lambda _database, _candidate, **_kwargs: admitted.append(_candidate["id"]))
    monkeypatch.setattr(threat_training, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    monkeypatch.setattr(threat_training, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append((next_phase, next_payload)) or True)

    task = {"id": "defense-admission", "generation": 1, "lease_token": "live",
            "payload": {"queue_date": today, "phase": "threat", "cursor": 0}}
    assert threat_training.execute_defense_admission_slice(task)
    assert admitted == ["candidate-one"]
    assert queued == [("daily_queue", "current", {"queue_date": today}, 10)]
    assert advanced[0][1]["cursor"] == 0

    lease_current = False
    assert not threat_training.execute_defense_admission_slice(task)
    assert admitted == ["candidate-one"]
    assert len(queued) == 1
