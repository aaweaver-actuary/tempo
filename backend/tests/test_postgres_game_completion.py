"""Version and lease fences for the final PostgreSQL derivation phases."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys

from app import tasks, postgres_store
from app.services import (
    postgres_game_events, postgres_game_features, postgres_game_priorities,
    postgres_priority, introduction_priorities, durable_tasks, refresh_requests, repertoire_opportunities,
)


def test_postgres_gameplay_event_import_targets_legacy_table():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        from migrate_sqlite_to_postgres import COPY_TARGETS
    finally:
        sys.path.remove(str(scripts))
    assert COPY_TARGETS["gameplay_events"] == "gameplay_events_legacy"


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


def test_postgres_priority_request_admits_matching_celery_generation(monkeypatch):
    enqueued = []
    monkeypatch.setattr(refresh_requests, 'request_refresh', lambda *_args, **_kwargs: 5)

    class Database:
        def execute(self, statement, _parameters=()):
            if "SELECT generation FROM repertoire_priority_jobs" in statement:
                return Cursor((4,))
            return Cursor()

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(durable_tasks, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority, delay_seconds=0:
                        enqueued.append((kind, key, payload, priority, delay_seconds)))

    assert introduction_priorities.enqueue_priority_refresh_in_transaction(
        Database(), "repertoire-one", quiet_seconds=5,
    ) == 4
    assert enqueued == [
        ("repertoire_priority", "repertoire-one",
         {"repertoire_id": "repertoire-one", "generation": 4}, 131, 5),
        ("priority_retention", "repertoire-one",
         {"repertoire_id": "repertoire-one"}, 200, 0),
    ]


def test_postgres_game_events_stage_then_publish_without_stale_replay(monkeypatch):
    assert "game_derivation_events" in tasks._SUPPORTED_BACKGROUND_KINDS
    event = ("event-one", "game-one", 3, 1, 12, "tactical opportunity", "fork",
             "white", "black", "missed", 0.9, 100, "e2e4", "d2d4",
             "[]", "{}", "created", "updated")
    staged = []
    updates = []
    queued = []
    advanced = []
    completed = []
    lease_current = True

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 7, "completed_phases": 4,
                               "status": "queued"})
            if "COUNT(*) FROM gameplay_events_staged" in statement:
                return Cursor((len(staged),))
            if "UPDATE game_derivation_jobs" in statement:
                updates.append(parameters)
            return Cursor()

        def execute_native(self, statement, parameters=()):
            staged.append(parameters)
            return Cursor()

    @contextmanager
    def write_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_game_events, "connection", write_database)
    monkeypatch.setattr(postgres_game_events, "refresh_gameplay_events",
                        lambda _game_id, **_kwargs: [event])
    monkeypatch.setattr(postgres_game_events, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(postgres_game_events, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append(next_payload) or True)
    monkeypatch.setattr(postgres_game_events, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    monkeypatch.setattr(postgres_game_events, "complete_task_slice_in_transaction",
                        lambda _database, _task: completed.append(True) or True)

    task = {"id": "events", "generation": 1, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 7, "cursor": 0}}
    assert postgres_game_events.execute_game_event_slice(task)
    assert len(staged) == 1
    assert staged[0][-1] == 7
    lease_current = False
    assert not postgres_game_events.execute_game_event_slice(task)
    assert len(staged) == 1
    lease_current = True
    task["payload"] = advanced[0]
    assert postgres_game_events.execute_game_event_slice(task)
    assert updates == [(7, "game-one", 7)]
    assert queued == [("game_derivation_features", "game-one",
                       {"game_id": "game-one", "derivation_version": 7}, 126)]
    assert completed == [True]


def test_postgres_game_features_publish_then_handoff_with_current_version(monkeypatch):
    assert "game_derivation_features" in tasks._SUPPORTED_BACKGROUND_KINDS
    writes = []
    queued = []
    lease_current = True

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 7, "completed_phases": 5,
                               "status": "queued"})
            writes.append((statement, parameters))
            return Cursor()

    @contextmanager
    def write_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_game_features, "connection", write_database)
    monkeypatch.setattr(postgres_game_features, "refresh_game_features",
                        lambda _game_id, **_kwargs: ("game-one", "features"))
    monkeypatch.setattr(postgres_game_features, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(postgres_game_features, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    monkeypatch.setattr(postgres_game_features, "complete_task_slice_in_transaction",
                        lambda _database, _task: True)

    task = {"id": "features", "generation": 1, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 7}}
    assert postgres_game_features.execute_game_feature_slice(task)
    assert writes[0][1] == ("game-one", "features")
    assert queued == [("game_derivation_priorities", "game-one",
                       {"game_id": "game-one", "derivation_version": 7,
                        "after_repertoire_id": ""}, 126)]
    lease_current = False
    assert not postgres_game_features.execute_game_feature_slice(task)
    assert len(writes) == 2


def test_postgres_game_priority_handoff_finishes_only_after_last_repertoire(monkeypatch):
    assert "game_derivation_priorities" in tasks._SUPPORTED_BACKGROUND_KINDS
    queued = []
    advanced = []
    completed = []
    updates = []

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM imported_games" in statement:
                return Cursor({"color": "white"})
            if "FROM repertoire_lines" in statement:
                return Cursor({"repertoire_id": "repertoire-one"}
                              if parameters[1] == "" else None)
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 7, "completed_phases": 6,
                               "status": "queued"})
            if "UPDATE game_derivation_jobs" in statement:
                updates.append(parameters)
            return Cursor()

    @contextmanager
    def database_connection(*, background=True):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_game_priorities, "background_read_connection", database_connection)
    monkeypatch.setattr(postgres_game_priorities, "connection", database_connection)
    monkeypatch.setattr(postgres_game_priorities, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(postgres_game_priorities, "enqueue_priority_refresh_in_transaction",
                        lambda _database, _id:
                        queued.append(("repertoire_priority", _id,
                                       {"repertoire_id": _id, "generation": 4,
                                        "cursor": 0})) or 4)
    monkeypatch.setattr(postgres_game_priorities,
                        "enqueue_opportunity_refresh_in_transaction",
                        lambda _database, key: queued.append(('repertoire_opportunity', key, {'repertoire_id': key})))
    monkeypatch.setattr(postgres_game_priorities, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append(next_payload) or True)
    monkeypatch.setattr(postgres_game_priorities, "complete_task_slice_in_transaction",
                        lambda _database, _task: completed.append(True) or True)

    task = {"id": "priorities", "generation": 1, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 7,
                        "after_repertoire_id": ""}}
    assert postgres_game_priorities.execute_game_priority_handoff_slice(task)
    assert [item[0] for item in queued] == ["repertoire_priority", "repertoire_opportunity"]
    assert queued[0][2]["generation"] == 4
    task["payload"] = advanced[0]
    assert postgres_game_priorities.execute_game_priority_handoff_slice(task)
    assert updates[0][1:] == ("game-one", 7)
    assert completed == [True]


def test_postgres_priority_generation_stages_and_publishes_after_replay(monkeypatch):
    assert "repertoire_priority" in tasks._SUPPORTED_BACKGROUND_KINDS
    record = {
        "ordinal": 0, "card_id": "card-one", "priority_score": 1.0,
        "evidence_json": "{}", "completed_line_ids_json": "[]",
        "frontier_decisions_json": "[]", "completion_mass": 0.0,
        "frontier_reach": 0.0,
    }
    staged = []
    advanced = []
    queued = []
    lease_current = True

    class Database:
        def execute(self, statement, parameters=()):
            if "SELECT * FROM repertoire_priority_prepared_rows" in statement:
                class Rows:
                    def __iter__(self):
                        return iter([record] if parameters[2] == 0 else [])
                return Rows()
            if "COUNT(*) FROM repertoire_priority_prepared_rows" in statement:
                return Cursor((1,))
            if "COUNT(*) FROM repertoire_card_priority_generations" in statement:
                return Cursor((len(staged),))
            return Cursor()

        def executemany(self, statement, parameters):
            if "INSERT INTO repertoire_card_priority_generations" in statement:
                staged.extend(parameters)
            return Cursor()

    @contextmanager
    def write_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_priority, "connection", write_database)
    monkeypatch.setattr(postgres_priority, "_has_current_priority_lease",
                        lambda *_args: lease_current)
    monkeypatch.setattr(postgres_priority, "_load_preparation_manifest", lambda *_args: {
        "status": "ready", "scoring_version": postgres_priority.SCORING_VERSION,
        "source_version": "0:0", "expected_count": 1, "ordering_version": 1,
    })
    monkeypatch.setattr(postgres_priority, "_priority_source_version",
                        lambda *_args, **_kwargs: "0:0")
    monkeypatch.setattr(postgres_priority, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append(next_payload) or True)
    monkeypatch.setattr(postgres_priority, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    monkeypatch.setattr(postgres_priority, "complete_task_slice_in_transaction",
                        lambda _database, _task: lease_current)
    monkeypatch.setattr(repertoire_opportunities, 'enqueue_opportunity_refresh_in_transaction',
                        lambda _database, key: queued.append(('repertoire_opportunity', key, {'repertoire_id': key}, 130)))

    task = {"id": "priority", "generation": 1, "lease_token": "live",
            "payload": {"repertoire_id": "repertoire-one", "generation": 4, "cursor": 0}}
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert len(staged) == 1
    lease_current = False
    assert not postgres_priority.execute_repertoire_priority_slice(task)
    assert len(staged) == 1
    lease_current = True
    task["payload"] = advanced[0]
    assert postgres_priority.execute_repertoire_priority_slice(task)
    assert queued == [("priority_retention", "repertoire-one",
                       {"repertoire_id": "repertoire-one"}, 200),
                      ('repertoire_opportunity', 'repertoire-one', {'repertoire_id': 'repertoire-one'}, 130)]
