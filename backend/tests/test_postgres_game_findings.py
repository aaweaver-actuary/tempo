"""Regressions for separating finding preparation from database publication."""

from __future__ import annotations

from contextlib import contextmanager

from app import tasks
from app.services import game_findings, postgres_game_findings


def test_postgres_game_findings_prepare_does_not_open_writer_or_publish(monkeypatch):
    statements: list[str] = []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0]

    class Database:
        def execute(self, statement, _parameters=()):
            statements.append(statement)
            if "FROM settings" in statement:
                return Cursor([{"major_mistake_cp": 100, "engine_line_window_cp": 20}])
            return Cursor([])

    @contextmanager
    def read_database():
        yield Database()

    monkeypatch.setattr(game_findings, "background_read_connection", read_database)
    monkeypatch.setattr(
        game_findings, "connection",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("writer opened")),
    )

    assert game_findings.refresh_game_findings("missing", background=True, prepare_only=True) == (
        [], [], {}, [],
    )
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_postgres_game_findings_unseen_cards_use_short_paged_reads(monkeypatch):
    opened = 0
    card_cursors: list[str] = []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0]

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM settings" in statement:
                return Cursor([{"major_mistake_cp": 100, "engine_line_window_cp": 20}])
            if "FROM cards c" in statement:
                card_cursors.append(parameters[0])
                if not parameters[0]:
                    return Cursor([{"id": f"card-{index:03}", "start_fen": "", "moves_json": "[]"}
                                   for index in range(64)])
                return Cursor([{"id": "card-064", "start_fen": "", "moves_json": "[]"}])
            return Cursor([])

    @contextmanager
    def read_database():
        nonlocal opened
        opened += 1
        yield Database()

    monkeypatch.setattr(game_findings, "background_read_connection", read_database)
    assert game_findings.refresh_game_findings("missing", background=True, prepare_only=True) == (
        [], [], {}, [],
    )
    assert opened == 3
    assert card_cursors == ["", "card-063"]


def test_postgres_game_findings_stage_one_item_and_replay_after_restart(monkeypatch):
    assert "game_derivation_findings" in tasks._SUPPORTED_BACKGROUND_KINDS
    items = [
        ("finding", "one", {"id": "one"}),
        ("opportunity", "two", {"id": "two"}),
    ]
    stored: dict[tuple[str, str], str] = {}
    advances = []
    published = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 3, "completed_phases": 2,
                               "status": "running"})
            if "INSERT INTO game_finding_publication_items" in statement:
                stored[(parameters[2], parameters[3])] = parameters[4]
            return Cursor()

    @contextmanager
    def open_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_game_findings, "connection", open_database)
    monkeypatch.setattr(postgres_game_findings, "_prepared_items",
                        lambda _game_id: ("source-one", items))
    monkeypatch.setattr(postgres_game_findings, "lock_current_slice", lambda *_: True)
    monkeypatch.setattr(postgres_game_findings, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advances.append((next_phase, next_payload)) or True)
    monkeypatch.setattr(postgres_game_findings, "_publish_items",
                        lambda _database, _task, game_id, version, count:
                        published.append((game_id, version, count)) or True)
    task = {"id": "findings", "generation": 1, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 3,
                        "phase": "stage", "cursor": 0}}
    for cursor in (0, 0, 1):
        task["payload"] = {**task["payload"], "cursor": cursor}
        assert postgres_game_findings.execute_game_findings_slice(task)
    assert len(stored) == 2
    assert advances[-1][1]["cursor"] == 2
    task["payload"] = {**task["payload"], "cursor": 2}
    assert postgres_game_findings.execute_game_findings_slice(task)
    assert published == [("game-one", 3, 2)]


def test_postgres_game_findings_source_change_restarts_without_publication(monkeypatch):
    updates = []
    enqueued = []

    class Cursor:
        def fetchone(self):
            return {"derivation_version": 3, "completed_phases": 2,
                    "status": "running"}

    class Database:
        def execute(self, statement, parameters=()):
            updates.append((statement, parameters))
            return Cursor()

    @contextmanager
    def open_database(*, background):
        yield Database()

    monkeypatch.setattr(postgres_game_findings, "connection", open_database)
    monkeypatch.setattr(postgres_game_findings, "_prepared_items",
                        lambda _game_id: ("changed-source", []))
    monkeypatch.setattr(postgres_game_findings, "lock_current_slice", lambda *_: True)
    monkeypatch.setattr(postgres_game_findings,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))
    task = {"id": "findings", "generation": 1, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 3,
                        "phase": "stage", "cursor": 1,
                        "source_signature": "old-source"}}
    assert postgres_game_findings.execute_game_findings_slice(task)
    assert enqueued == [
        ("game_derivation_findings", "game-one",
         {"game_id": "game-one", "derivation_version": 4,
          "phase": "stage", "cursor": 0}, 126),
    ]
    assert not any("INSERT INTO game_finding_publication_items" in statement
                   for statement, _ in updates)
