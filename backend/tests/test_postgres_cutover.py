"""Named regressions for the PostgreSQL command and admission boundaries."""

from __future__ import annotations

import threading
import asyncio
from datetime import date
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import json
import sqlite3
import chess
from types import SimpleNamespace

from pathlib import Path

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import HTTPException
from kombu.exceptions import OperationalError as BrokerUnavailable
import pytest

from app import command_dispatch
from app.command_gateway import CommandConflict, request_digest
from app.postgres_store import TempoRow, postgres_sql
from app.services import redis_admission_gate


def test_postgres_pgn_import_dispatches_parsed_payload_with_idempotency(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    observed = []

    class SettingsDatabase:
        def execute(self, *_args):
            return SimpleNamespace(fetchone=lambda: (6,))

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(SettingsDatabase()))
    from fastapi.responses import JSONResponse
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key: observed.append(
            (name, payload, idempotency_key)
        ) or JSONResponse(status_code=202, content={
            "operation_id": idempotency_key, "state": "pending"}),
    )
    response = TestClient(main.app).post(
        "/api/imports/pgn",
        headers={"Idempotency-Key": "import-one"},
        files={"file": ("opening.pgn", b"[Event \"Test\"]\n\n1. e4 e5 *", "application/x-chess-pgn")},
        data={"trained_color": "white", "initial_depth": "4"},
    )
    assert response.status_code == 202, response.text
    assert observed[0][0] == "imports.pgn.admit"
    assert observed[0][1]["source_name"] == "opening.pgn"
    assert observed[0][1]["depth"] == 4
    assert observed[0][1]["lines"][0]["moves"] == ["e2e4", "e7e5"]
    assert observed[0][2] == "import-one"


def test_postgres_integrity_repair_dispatches_prepared_plan_with_idempotency(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.services import repertoire_integrity

    observed = []
    prepared = {
        "repertoire_id": "rep", "issue_id": "issue", "signature": "signature",
        "changed_lines": {}, "changed_cards": {}, "expected_line_moves": {},
        "expected_card_moves": {}, "unsupported_card_ids": [],
    }
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(repertoire_integrity, "prepare_issue_resolution",
                        lambda *_args: prepared)
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key:
            observed.append((name, payload, idempotency_key)) or {
                "task_id": "graph", "repertoire_id": "rep",
                "issue_id": "issue", "state": "queued",
            },
    )
    response = TestClient(main.app).post(
        "/api/repertoires/rep/integrity/issues/issue/resolve",
        headers={"Idempotency-Key": "repair-one"},
        json={"signature": "signature", "selected_move_uci": "e2e4"},
    )
    assert response.status_code == 200, response.text
    assert observed == [("integrity.issue.resolve", prepared, "repair-one")]


def test_postgres_integrity_repair_rejects_stale_issue_signature(monkeypatch):
    from app import integrity_repair_commands

    observed = []
    monkeypatch.setattr(
        integrity_repair_commands, "request_graph_rebuild_in_transaction",
        lambda *_args: observed.append("graph-task") or {"id": "graph", "state": "queued"},
    )

    class IssueChanged:
        def execute_native(self, statement, *_args):
            observed.append(statement)
            if "FROM repertoires" in statement:
                return SimpleNamespace(fetchone=lambda: (1,))
            if "FROM repertoire_integrity_issues" in statement:
                return SimpleNamespace(fetchone=lambda: ("new-signature",))
            raise AssertionError("A stale repair must not write source rows")

    with pytest.raises(HTTPException) as error:
        integrity_repair_commands.resolve_integrity_issue(IssueChanged(), {
            "repertoire_id": "rep", "issue_id": "issue",
            "signature": "old-signature", "changed_lines": {},
            "changed_cards": {}, "expected_line_moves": {},
            "expected_card_moves": {}, "unsupported_card_ids": [],
        })
    assert error.value.status_code == 409
    assert observed[0] == "graph-task"
    assert "FROM repertoires" in observed[1]


def test_postgres_integrity_repair_copies_line_training_depth_before_delete():
    from app.integrity_repair_commands import _replace_repertoire_line

    statements = []
    class RecordingDatabase:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))

    assert _replace_repertoire_line(RecordingDatabase(), {
        "id": "old", "repertoire_id": "rep", "name": "Opening",
        "trained_color": "white", "start_fen": chess.STARTING_FEN,
        "created_at": "2026-09-27",
    }, ["e2e4", "e7e5", "g1f3"])
    assert "INSERT INTO repertoire_line_training_depths" in statements[1][0]
    assert "GREATEST" in statements[1][0]
    assert statements[2] == ("DELETE FROM repertoire_lines WHERE id=%s", ("old",))


def test_postgres_repertoire_edit_invalidates_old_clean_integrity_in_same_transaction():
    from app.services.postgres_integrity import invalidate_integrity_in_transaction

    statements = []

    class RecordingDatabase:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))

    invalidate_integrity_in_transaction(RecordingDatabase(), "opening-1")
    assert len(statements) == 1
    assert "status='unchecked'" in statements[0][0]
    assert "scan_status='idle'" in statements[0][0]
    assert "checked_at=NULL" in statements[0][0]
    assert statements[0][1] == ("opening-1",)


def test_postgres_integrity_source_closes_read_transaction_before_chess_scan(monkeypatch):
    from app.services import postgres_integrity

    connection_open = False

    class ReadDatabase:
        def execute_native(self, statement, _parameters):
            if "SELECT trained_color FROM repertoire_lines" in statement:
                return SimpleNamespace(fetchone=lambda: ("white",))
            return SimpleNamespace(fetchone=lambda: {
                "id": "line-1", "repertoire_id": "opening-1", "name": "line",
                "trained_color": "white",
                "start_fen": chess.STARTING_FEN,
                "moves_json": '["e2e4","e7e5"]', "created_at": "2026-09-27",
            })

    @contextmanager
    def read_source():
        nonlocal connection_open
        connection_open = True
        try:
            yield ReadDatabase()
        finally:
            connection_open = False

    original_scan = postgres_integrity._scan_source

    def scan_after_close(source, color):
        assert not connection_open
        return original_scan(source, color)

    monkeypatch.setattr(postgres_integrity, "background_read_connection", read_source)
    monkeypatch.setattr(postgres_integrity, "_scan_source", scan_after_close)
    prepared = postgres_integrity.prepare_next_integrity_source("opening-1", "line", "")
    assert prepared is not None
    assert prepared.source_id == "line-1"
    assert prepared.positions
    assert not prepared.invalid


def test_postgres_integrity_source_stages_one_restartable_slice(monkeypatch):
    from app.services import postgres_integrity

    statements = []
    advances = []

    class RecordingDatabase:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))

    task = {
        "id": "integrity-task", "generation": 3, "lease_token": "lease",
        "payload": {"repertoire_id": "opening-1", "source_type": "line",
                    "after_source_id": "", "source_offset": 0},
    }
    prepared = postgres_integrity.PreparedIntegritySource(
        "line", "line-1", ({"fen_key": "fen", "moves": ["e2e4"], "sources": []},), (),
    )
    monkeypatch.setattr(postgres_integrity, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_integrity, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
            advances.append((next_phase, next_payload)) or True,
    )
    assert postgres_integrity.stage_integrity_source_in_transaction(
        RecordingDatabase(), task, prepared,
    )
    assert statements[0][1][:2] == ("integrity-task:3", 0)
    assert json.loads(statements[0][1][2])[0]["fen_key"] == "fen"
    assert advances == [("scan", {
        "repertoire_id": "opening-1", "source_type": "line",
        "after_source_id": "line-1", "source_offset": 1,
    })]

    statements.clear()
    monkeypatch.setattr(postgres_integrity, "lock_current_slice", lambda *_args: False)
    assert not postgres_integrity.stage_integrity_source_in_transaction(
        RecordingDatabase(), task, prepared,
    )
    assert statements == []


def test_postgres_integrity_aggregation_merges_two_positions_per_lease(monkeypatch):
    from app.services import postgres_integrity

    batches = []
    advances = []

    class RecordingCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def executemany(self, statement, rows):
            batches.append((statement, rows))

    class RecordingDatabase:
        raw = SimpleNamespace(cursor=RecordingCursor)

    monkeypatch.setattr(postgres_integrity, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_integrity, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
            advances.append((next_phase, next_payload)) or True,
    )
    task = {
        "id": "integrity-task", "generation": 3, "lease_token": "lease",
        "payload": {"repertoire_id": "opening-1", "source_offset": 0,
                    "position_offset": 0},
    }
    positions = tuple({"fen_key": f"fen-{index}", "fen": f"fen-{index}",
                       "moves": ["e2e4"], "sources": []} for index in range(3))
    prepared = postgres_integrity.PreparedIntegrityRun(0, positions, ())
    assert postgres_integrity.aggregate_integrity_run_in_transaction(
        RecordingDatabase(), task, prepared,
    )
    assert len(batches[1][1]) == 2
    assert advances[0][1]["position_offset"] == 2
    task["payload"]["position_offset"] = 2
    assert postgres_integrity.aggregate_integrity_run_in_transaction(
        RecordingDatabase(), task, prepared,
    )
    assert len(batches[-1][1]) == 1
    assert advances[-1][1]["source_offset"] == 1
    assert advances[-1][1]["position_offset"] == 0


def test_postgres_integrity_evaluation_stages_only_conflicting_positions(monkeypatch):
    from app.services import postgres_integrity

    rows = iter([
        {"fen_key": "fen-a", "fen": "fen-a", "trained_color": "white",
         "moves_json": '["e2e4"]', "sources_json": "[]"},
        {"fen_key": "fen-b", "fen": "fen-b", "trained_color": "white",
         "moves_json": '["e2e4","d2d4"]', "sources_json": "[]"},
    ])

    class ReadDatabase:
        def execute_native(self, *_args):
            return SimpleNamespace(fetchone=lambda: next(rows))

    monkeypatch.setattr(
        postgres_integrity, "background_read_connection",
        lambda: nullcontext(ReadDatabase()),
    )
    clean = postgres_integrity.prepare_next_integrity_position("rep", "run", "")
    conflict = postgres_integrity.prepare_next_integrity_position("rep", "run", "fen-a")
    assert clean is not None and clean.issue is None
    assert conflict is not None and conflict.issue is not None
    assert conflict.issue["kind"] == "multiple_responses"
    assert conflict.issue["signature"]

    statements = []
    advances = []

    class RecordingDatabase:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))

    task = {"id": "scan", "generation": 1, "lease_token": "lease",
            "payload": {"repertoire_id": "rep", "after_fen_key": ""}}
    monkeypatch.setattr(postgres_integrity, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_integrity, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
            advances.append((next_phase, next_payload)) or True,
    )
    assert postgres_integrity.stage_integrity_issue_in_transaction(
        RecordingDatabase(), task, clean,
    )
    assert statements == []
    assert advances[-1][1]["after_fen_key"] == "fen-a"
    assert postgres_integrity.stage_integrity_issue_in_transaction(
        RecordingDatabase(), task, conflict,
    )
    assert statements[0][1][4] == "fen-b"
    assert advances[-1][1]["after_fen_key"] == "fen-b"


def test_postgres_graph_finalization_requests_integrity_before_queue_refresh(monkeypatch):
    from app.services import postgres_opening_graph, postgres_integrity

    requested = []
    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_opening_graph, "complete_task_slice_in_transaction",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        postgres_integrity, "request_integrity_scan_in_transaction",
        lambda _database, repertoire_id, generation, local_day:
            requested.append((repertoire_id, generation, local_day)),
    )
    task = {"generation": 7, "payload": {
        "repertoire_id": "opening-1", "local_day": "2026-09-27",
    }}
    assert not postgres_opening_graph.finalize_graph_in_transaction(object(), task)
    assert requested == [("opening-1", 7, "2026-09-27")]


def test_postgres_integrity_publication_discards_stale_graph_generation(monkeypatch):
    from app.services import postgres_integrity

    completed = []
    monkeypatch.setattr(postgres_integrity, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_integrity, "_graph_generation_is_current", lambda *_args: False,
    )
    monkeypatch.setattr(
        postgres_integrity, "complete_task_slice_in_transaction",
        lambda _database, task: completed.append(task["id"]) or True,
    )
    class NoWrites:
        def execute_native(self, *_args):
            raise AssertionError("A stale integrity scan cannot publish issues")

    task = {"id": "scan", "generation": 1, "lease_token": "lease",
            "payload": {"repertoire_id": "opening-1", "graph_generation": 4}}
    assert postgres_integrity.publish_integrity_issues_in_transaction(NoWrites(), task)
    assert completed == ["scan"]


def test_postgres_integrity_failure_updates_visible_scan_state(monkeypatch):
    from app.services import durable_tasks

    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE background_tasks(
            id TEXT PRIMARY KEY,generation INTEGER,state TEXT,phase TEXT,
            attempt_count INTEGER,max_attempts INTEGER,next_attempt_at TEXT,
            lease_token TEXT,lease_expires_at TEXT,last_error TEXT,
            completed_at TEXT,updated_at TEXT,kind TEXT,payload_json TEXT
        );
        CREATE TABLE background_task_events(
            id INTEGER PRIMARY KEY,task_id TEXT,generation INTEGER,event TEXT,
            phase TEXT,detail TEXT,created_at TEXT
        );
        CREATE TABLE repertoire_integrity_state(
            repertoire_id TEXT PRIMARY KEY,scan_generation TEXT,
            scan_status TEXT,scan_error TEXT
        );
        INSERT INTO background_tasks VALUES(
            'scan',3,'leased','publish',5,5,NULL,'lease',NULL,NULL,NULL,NULL,
            'integrity_scan','{"repertoire_id":"opening-1"}'
        );
        INSERT INTO repertoire_integrity_state VALUES(
            'opening-1','scan:3','running',NULL
        );
    """)
    monkeypatch.setattr(
        durable_tasks, "submit_background_write",
        lambda operation, *, label: operation(database),
    )
    assert durable_tasks.fail_task("scan", 3, "lease", RuntimeError("too many issues"))["state"] == "failed"
    assert tuple(database.execute(
        "SELECT scan_status,scan_error FROM repertoire_integrity_state"
    ).fetchone()) == ("failed", "too many issues")
    database.close()


def test_postgres_integrity_foreground_contention_restart_and_stale_replay(monkeypatch):
    from app.services import postgres_integrity

    actual_stage = postgres_integrity.stage_integrity_source_in_transaction
    foreground_released = threading.Event()
    background_started = threading.Event()
    writes = []
    prepared = postgres_integrity.PreparedIntegritySource("line", "line-1", (), ())

    @contextmanager
    def wait_for_foreground():
        background_started.set()
        if not foreground_released.wait(timeout=2):
            raise TimeoutError("Foreground admission did not release")
        yield

    monkeypatch.setattr(postgres_integrity, "prepare_next_integrity_source",
                        lambda *_args: prepared)
    monkeypatch.setattr(postgres_integrity, "background_lease", wait_for_foreground)
    monkeypatch.setattr(postgres_integrity.postgres_store, "connection",
                        lambda **_kwargs: nullcontext(object()))
    monkeypatch.setattr(
        postgres_integrity, "stage_integrity_source_in_transaction",
        lambda _database, task, _source: writes.append(task["lease_token"]) or True,
    )
    task = {"id": "scan", "generation": 2, "lease_token": "current",
            "payload": {"repertoire_id": "opening-1", "source_type": "line"}}
    result = []
    worker = threading.Thread(
        target=lambda: result.append(postgres_integrity.execute_integrity_source_slice(task)),
    )
    worker.start()
    assert background_started.wait(timeout=1)
    assert writes == []
    foreground_released.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [True]
    assert writes == ["current"]

    statements = []
    class RecordingDatabase:
        def execute_native(self, statement, _parameters):
            statements.append(statement)

    monkeypatch.setattr(postgres_integrity, "lock_current_slice", lambda *_args: False)
    assert not actual_stage(
        RecordingDatabase(), task, prepared,
    )
    assert statements == []


def test_postgres_startup_accepts_latest_checked_in_schema(monkeypatch):
    from app import database
    from app.schema_version import POSTGRES_SCHEMA_VERSION

    migration_directory = Path(__file__).resolve().parents[1] / "migrations"
    newest_migration = max(int(path.name[:3]) for path in migration_directory.glob("[0-9][0-9][0-9]_*.sql"))
    assert POSTGRES_SCHEMA_VERSION == newest_migration

    class ReadDatabase:
        def execute(self, *_args):
            return SimpleNamespace(fetchone=lambda: (newest_migration,))

    monkeypatch.setattr(database.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(database, "read_connection", lambda: nullcontext(ReadDatabase()))
    database.initialize()


def test_postgres_game_accounts_update_dispatches_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    observed = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key: observed.append(
            (name, payload, idempotency_key)
        ) or payload,
    )
    response = TestClient(main.app).put(
        "/api/games/accounts",
        headers={"Idempotency-Key": "accounts-1"},
        json={"lichess_username": "alice", "chesscom_username": "bob"},
    )
    assert response.status_code == 200, response.text
    assert observed == [(
        "games.accounts.update",
        {"lichess_username": "alice", "chesscom_username": "bob"},
        "accounts-1",
    )]


def test_postgres_game_accounts_update_reconciles_provider_rows():
    from app.account_commands import update_game_accounts

    statements = []

    class RecordingDatabase:
        def execute(self, statement, parameters):
            statements.append((statement, parameters))
            return SimpleNamespace(rowcount=1)

    response = update_game_accounts(
        RecordingDatabase(),
        {"lichess_username": " alice ", "chesscom_username": ""},
    )
    assert response == {"lichess_username": "alice", "chesscom_username": ""}
    assert statements[0][1] == ("alice", "")
    assert statements[1][1] == ("lichess", "alice")
    assert statements[2][1] == ("chess.com",)


def test_postgres_endgame_probe_uses_read_only_tablebase_path(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())

    async def fake_tablebase(fen):
        assert fen == "8/8/8/8/8/8/4K3/7k w - - 0 1"
        return {"category": "draw", "moves": []}

    monkeypatch.setattr(main, "tablebase", fake_tablebase)
    response = TestClient(main.app).post(
        "/api/endgames/probe", json={"fen": "8/8/8/8/8/8/4K3/7k w - - 0 1"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"category": "draw", "moves": []}


def test_postgres_card_validation_remains_available_without_a_write_worker(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    response = TestClient(main.app).post(
        "/api/cards/validate",
        json={"starting_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
              "moves": ["e2e4"], "history_mode": "preserve"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["moves"] == ["e2e4"]


def test_postgres_analysis_paste_preview_uses_query_only_reader_without_write_worker(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    observed = []

    @contextmanager
    def query_only_scope():
        observed.append("query_only_entered")
        yield

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "query_only_request", query_only_scope)
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(main, "connection", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("preview must not open a writer")))
    monkeypatch.setattr(
        main, "build_paste_preview",
        lambda database, text, starting_fen, source_gap_id:
            observed.append((text, starting_fen, source_gap_id)) or {"lines": []},
    )
    response = TestClient(main.app).post(
        "/api/repertoire/paste/preview", json={"text": "1. e4"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"lines": []}
    assert observed == ["query_only_entered", ("1. e4", None, None)]


def test_postgres_analysis_paste_commit_dispatches_foreground_command_after_read_only_preview(monkeypatch):
    from fastapi.testclient import TestClient
    from app import command_gateway, main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(main, "connection", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("API must not open a writer")))
    monkeypatch.setattr(command_gateway, "read_operation", lambda *_args, **_kwargs: {"state": "pending"})
    monkeypatch.setattr(
        main, "build_paste_preview",
        lambda *_args: {"preview_token": "token", "lines": [{"moves": ["e2e4"]}]},
    )
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key:
            dispatched.append((name, payload, idempotency_key)) or {"saved": []},
    )
    request = {
        "text": "1. e4", "preview_token": "token",
        "selections": [{"index": 0, "repertoire_id": "white"}],
    }
    response = TestClient(main.app).post(
        "/api/repertoire/paste/commit", json=request,
        headers={"Idempotency-Key": "paste-white-1"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [(
        "analysis.paste.commit",
        {"request": {**request, "starting_fen": None, "source_gap_id": None,
                     "selections": [{**request["selections"][0], "acknowledge_conflict": False}]},
         "prepared_preview": {"preview_token": "token", "lines": [{"moves": ["e2e4"]}]}},
        "paste-white-1",
    )]


def test_postgres_analysis_paste_replay_uses_stable_user_request_after_snapshot_changes():
    from app.command_gateway import request_digest

    request = {"text": "1. e4", "preview_token": "preview-1",
               "selections": [{"index": 0, "repertoire_id": "white"}]}
    original = request_digest("analysis.paste.commit", {
        "request": request, "prepared_preview": {"lines": [], "snapshot": "before"},
    })
    after_save = request_digest("analysis.paste.commit", {
        "request": request, "prepared_preview": {"lines": ["new line"], "snapshot": "after"},
    })
    changed_request = request_digest("analysis.paste.commit", {
        "request": {**request, "text": "1. d4"},
        "prepared_preview": {"lines": ["new line"], "snapshot": "after"},
    })
    assert original == after_save
    assert original != changed_request


def test_postgres_analysis_paste_completed_replay_returns_receipt_before_rebuilding_preview(monkeypatch):
    from fastapi.testclient import TestClient
    from app import command_gateway, main

    completed = {"saved": [{"index": 0, "repertoire_id": "white",
                            "duplicate": False, "conflict": False}],
                 "affected_repertoire_ids": ["white"], "gap_resolved": False}
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_gateway, "read_operation",
                        lambda *_args, **_kwargs: {"state": "complete", "response": completed})
    monkeypatch.setattr(main, "build_paste_preview",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("replay rebuilt preview")))
    response = TestClient(main.app).post(
        "/api/repertoire/paste/commit",
        json={"text": "1. e4", "preview_token": "old",
              "selections": [{"index": 0, "repertoire_id": "white"}]},
        headers={"Idempotency-Key": "completed-paste"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == completed


def test_postgres_branch_removal_dispatches_foreground_command_with_idempotency(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                            dispatched.append((name, payload, idempotency_key)) or {"deleted_line_count": 1})
    request = {"repertoire_id": "white", "starting_fen": chess.STARTING_FEN,
               "moves": ["e2e4"]}
    response = TestClient(main.app).post(
        "/api/repertoire/branches/remove", json=request,
        headers={"Idempotency-Key": "remove-white-e4"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("repertoire.branch.remove", request, "remove-white-e4")]


def test_postgres_branch_removal_queues_coverage_with_changed_lines(monkeypatch):
    from app import branch_commands

    class QueryResult:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class RecordingDatabase:
        def __init__(self):
            self.deleted = []

        def execute_native(self, statement, parameters=()):
            if statement.startswith("SELECT id FROM repertoires"):
                return QueryResult([("white",)])
            if statement.startswith("SELECT id,start_fen,moves_json"):
                return QueryResult([
                    {"id": "match", "start_fen": chess.STARTING_FEN,
                     "moves_json": '["e2e4", "e7e5"]'},
                    {"id": "other", "start_fen": chess.STARTING_FEN,
                     "moves_json": '["d2d4", "d7d5"]'},
                    {"id": "other-position", "start_fen": "8/8/8/8/8/8/4K3/7k w - - 0 1",
                     "moves_json": '["e2e4"]'},
                ])
            if statement.startswith("DELETE FROM repertoire_lines"):
                self.deleted.extend(parameters[0])
                return QueryResult([])
            raise AssertionError(statement)

    database = RecordingDatabase()
    scheduled = []
    monkeypatch.setattr(branch_commands, "invalidate_integrity_in_transaction",
                        lambda db, repertoire_id: scheduled.append(("integrity", db, repertoire_id)))
    monkeypatch.setattr(branch_commands, "request_graph_rebuild_in_transaction",
                        lambda db, repertoire_id, day: scheduled.append(("graph", db, repertoire_id)))
    monkeypatch.setattr(branch_commands, "request_coverage_seed_in_transaction",
                        lambda db, repertoire_id, *, automatic, supersede_active: scheduled.append(
                            ("coverage", db, repertoire_id, automatic, supersede_active)))
    monkeypatch.setattr(branch_commands, "integrity_summary",
                        lambda db, repertoire_id: {"status": "unchecked"})
    result = branch_commands.remove_repertoire_branch(database, {
        "repertoire_id": "white", "starting_fen": chess.STARTING_FEN,
        "moves": ["e2e4"],
    })
    assert database.deleted == ["match"]
    assert result == {"deleted_line_count": 1, "deleted_card_count": 0,
                      "retained_line_count": 2, "integrity": {"status": "unchecked"}}
    assert [event[0] for event in scheduled] == ["integrity", "graph", "coverage"]
    assert all(event[1] is database for event in scheduled)
    assert scheduled[-1][2:] == ("white", True, True)


def test_postgres_prefix_split_decisions_dispatch_foreground_commands_with_idempotency(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                            dispatched.append((name, payload, idempotency_key)) or
                            JSONResponse(status_code=202, content={"operation_id": idempotency_key}))
    client = TestClient(main.app)
    for action, command in (("", "cards.prefix_split.accept"),
                            ("/reject", "cards.prefix_split.reject")):
        response = client.post(
            f"/api/cards/prefix-card/prefix-split{action}",
            json={"expected_revision": 3},
            headers={"Idempotency-Key": f"prefix-card-3-{command}"},
        )
        assert response.status_code == 202, response.text
        assert response.json() == {"operation_id": f"prefix-card-3-{command}"}
    assert dispatched == [
        ("cards.prefix_split.accept", {"card_id": "prefix-card", "request": {"expected_revision": 3}},
         "prefix-card-3-cards.prefix_split.accept"),
        ("cards.prefix_split.reject", {"card_id": "prefix-card", "request": {"expected_revision": 3}},
         "prefix-card-3-cards.prefix_split.reject"),
    ]


def test_postgres_card_revision_dispatches_expected_revision_and_idempotency(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or
                        {"card_id": "edited", "replaced": True, "history_mode": "preserve"})
    request = {"starting_fen": chess.STARTING_FEN, "moves": ["d2d4"],
               "history_mode": "preserve", "expected_revision": 3}
    response = TestClient(main.app).put(
        "/api/cards/original", json=request,
        headers={"Idempotency-Key": "edit-original-3"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("cards.revise", {"card_id": "original", "request": {
        **request, "title": "Corrected card", "source_fen": None,
    }}, "edit-original-3")]


@pytest.mark.parametrize("stored_revision,archived,superseded_by", [
    (4, 0, None), (3, 1, "replacement"),
])
def test_postgres_card_revision_rejects_stale_edit_before_mutating_cards(
    stored_revision, archived, superseded_by,
):
    from app.card_commands import revise_card

    statements = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append(statement)
            if statement.startswith("SELECT * FROM cards WHERE id=%s FOR UPDATE"):
                return SimpleNamespace(fetchone=lambda: {
                    "revision": stored_revision, "archived": archived,
                    "superseded_by": superseded_by,
                })
            return SimpleNamespace(fetchone=lambda: None)

    with pytest.raises(HTTPException) as error:
        revise_card(Database(), {"card_id": "original", "request": {
            "starting_fen": chess.STARTING_FEN, "moves": ["d2d4"],
            "history_mode": "preserve", "expected_revision": 3,
        }})
    assert error.value.status_code == 409
    assert all(not statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)


def test_postgres_card_revision_reports_existing_target_revision():
    from app.card_commands import revise_card

    statements = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(())

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append(statement)
            if statement.startswith("SELECT * FROM cards WHERE id=%s FOR UPDATE"):
                return Cursor({"revision": 2, "archived": 0, "superseded_by": None,
                               "start_fen": chess.STARTING_FEN, "moves_json": '["e2e4"]'})
            if statement.startswith("SELECT id,revision FROM cards WHERE id=%s FOR UPDATE"):
                return Cursor({"id": "existing", "revision": 7})
            return Cursor()

    result = revise_card(Database(), {"card_id": "source", "request": {
        "starting_fen": chess.STARTING_FEN, "moves": ["d2d4"],
        "history_mode": "preserve", "expected_revision": 2,
    }})
    assert result["replaced"] is True
    assert result["revision"] == 7
    assert any("UPDATE daily_queue SET status='complete'" in statement for statement in statements)


def test_postgres_card_archive_dispatches_idempotent_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or
                        {"archived": True, "integrity": {}})
    response = TestClient(main.app).delete(
        "/api/cards/card-1", headers={"Idempotency-Key": "archive-card-1"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("cards.archive", {"card_id": "card-1"}, "archive-card-1")]


def test_postgres_card_archive_returns_unchecked_integrity_after_scan_intent(monkeypatch):
    from app import card_commands

    statements = []
    scan_requests = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row = row
            self.rows = rows

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append(statement)
            if statement.startswith("SELECT id FROM cards WHERE id=%s FOR UPDATE"):
                return Cursor(("card-1",))
            if statement.startswith("SELECT repertoire_id FROM repertoire_cards"):
                return Cursor(rows=[("rep",)])
            return Cursor()

    monkeypatch.setattr(card_commands, "_request_current_integrity_scan",
                        lambda database, repertoire_id, local_day:
                        scan_requests.append(repertoire_id))
    monkeypatch.setattr(card_commands, "integrity_summary",
                        lambda database, repertoire_id: {
                            "status": "unchecked" if scan_requests else "clean",
                        })
    result = card_commands.archive_card(Database(), {"card_id": "card-1"})
    assert result == {"archived": True, "integrity": {"rep": {"status": "unchecked"}}}
    assert scan_requests == ["rep"]
    assert any("UPDATE daily_queue SET status='complete'" in statement for statement in statements)


@pytest.mark.parametrize("action", ["dismiss", "acknowledge", "snooze", "train"])
def test_postgres_opportunity_state_actions_dispatch_idempotent_commands(monkeypatch, action):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "train": {"card_id": "card", "queued": True, "idempotent": False},
                            "dismiss": {"dismissed": True},
                            "acknowledge": {"acknowledged": True},
                            "snooze": {"snoozed": True},
                        }[action])
    response = TestClient(main.app).post(
        f"/api/repertoires/white/opportunities/discovery-1/{action}",
        headers={"Idempotency-Key": f"discovery-1-{action}"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [(f"opportunities.{action}", {
        "repertoire_id": "white", "opportunity_id": "discovery-1",
    }, f"discovery-1-{action}")]


def test_postgres_discovery_training_checkpoints_prefix_graph_intent(monkeypatch):
    from app import opportunity_commands

    events = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row = row
            self.rows = rows

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute_native(self, statement, parameters=()):
            if statement.startswith("SELECT card_id FROM repertoire_opportunities"):
                return Cursor(("source",))
            if statement.startswith("SELECT kind FROM cards"):
                return Cursor(("prefix",))
            if statement.startswith("SELECT shortened_card_id"):
                return Cursor(("parent", "continuation"))
            if statement.startswith("SELECT repertoire_id FROM repertoire_cards"):
                return Cursor(rows=[("white",), ("black",)])
            raise AssertionError(statement)

    database = Database()
    monkeypatch.setattr(opportunity_commands, "lock_queue_date_for_position",
                        lambda db, day: events.append(("queue-lock", db, day)))
    monkeypatch.setattr(opportunity_commands, "admit_existing_decision",
                        lambda db, repertoire_id, opportunity_id:
                        events.append(("admit", db, repertoire_id, opportunity_id)) or
                        {"card_id": "continuation", "queued": True, "idempotent": False})
    monkeypatch.setattr(opportunity_commands, "invalidate_integrity_in_transaction",
                        lambda db, repertoire_id:
                        events.append(("invalidate", db, repertoire_id)))
    monkeypatch.setattr(opportunity_commands, "request_graph_rebuild_in_transaction",
                        lambda db, repertoire_id, day:
                        events.append(("graph", db, repertoire_id, day)))
    result = opportunity_commands.train_opportunity(database, {
        "repertoire_id": "white", "opportunity_id": "discovery-1",
    })
    assert result["card_id"] == "continuation"
    assert events[0][0] == "queue-lock" and events[1] == (
        "admit", database, "white", "discovery-1",
    )
    assert [(event[0], event[2]) for event in events[2:]] == [
        ("invalidate", "black"), ("graph", "black"),
        ("invalidate", "white"), ("graph", "white"),
    ]


def test_postgres_annotation_route_dispatches_idempotent_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "repertoireId": "rep", "fenKey": "key", "comment": "Plan",
                            "arrows": [], "squares": [], "updatedAt": "2026-09-27T00:00:00Z",
                        })
    response = TestClient(main.app).put(
        "/api/repertoires/rep/annotations",
        headers={"Idempotency-Key": "annotation-1"},
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
              "comment": "Plan", "arrows": [], "squares": []},
    )
    assert response.status_code == 200, response.text
    assert dispatched[0][0] == "repertoires.annotation.save"
    assert dispatched[0][1]["repertoire_id"] == "rep"
    assert dispatched[0][2] == "annotation-1"


def test_postgres_annotation_command_clears_empty_position_note():
    from app.annotation_commands import save_position_annotation

    statements = []

    class Database:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: (1,))

    result = save_position_annotation(Database(), {
        "repertoire_id": "rep",
        "annotation": {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                       "comment": "   ", "arrows": [], "squares": []},
    })
    assert result["comment"] == ""
    assert statements[1][0].startswith("DELETE FROM position_annotations")


def test_postgres_repertoire_rename_dispatches_idempotent_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "id": "rep", "name": "New name",
                        })
    response = TestClient(main.app).patch(
        "/api/repertoires/rep", headers={"Idempotency-Key": "rename-1"},
        json={"name": "New name"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("repertoires.rename", {
        "repertoire_id": "rep", "name": "New name",
    }, "rename-1")]


def test_postgres_repertoire_delete_dispatches_idempotent_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or
                        {"deleted": True, "id": "rep"})
    response = TestClient(main.app).delete(
        "/api/repertoires/rep", headers={"Idempotency-Key": "delete-rep-1"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("repertoires.delete", {"repertoire_id": "rep"}, "delete-rep-1")]


def test_postgres_repertoire_delete_preserves_shared_cards_and_queues_game_refresh(monkeypatch):
    from app import repertoire_commands

    statements = []
    queued = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            if "SELECT id FROM repertoires WHERE id=%s FOR UPDATE" in statement:
                return SimpleNamespace(fetchone=lambda: ("old",))
            if "ORDER BY created_at DESC LIMIT 1" in statement:
                return SimpleNamespace(fetchone=lambda: ("new",))
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(repertoire_commands, "enqueue_task_in_transaction",
                        lambda database, kind, key, payload, *, priority:
                        queued.append((database, kind, key, payload, priority)))
    database = Database()
    assert repertoire_commands.delete_repertoire(database, {
        "repertoire_id": "old",
    }) == {"deleted": True, "id": "old"}
    assert any("MIN(link.repertoire_id) AS replacement" in sql and
               parameters == ("old", "old") for sql, parameters in statements)
    assert any(sql == "DELETE FROM repertoires WHERE id=%s" and
               parameters == ("old",) for sql, parameters in statements)
    assert any("SET is_main=CASE WHEN id=%s THEN 1 ELSE 0 END" in sql
               for sql, _ in statements)
    assert queued == [(database, "repertoire_game_refresh", "all",
                       {"after_game_id": ""}, 90)]


def test_postgres_branch_edit_dispatches_foreground_command_with_idempotency(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "id": "line", "duplicate": False, "moves": ["e2e4"],
                            "integrity": {"status": "unchecked", "issue_count": 0,
                                          "first_issue_id": None, "checked_at": None,
                                          "scan_status": "idle", "scan_generation": None,
                                          "scan_progress": {"completed": 0, "total": 0},
                                          "last_scan_error": None},
                        })
    response = TestClient(main.app).post(
        "/api/repertoire/branches", headers={"Idempotency-Key": "branch-1"},
        json={"repertoire_id": "rep", "starting_fen": chess.STARTING_FEN,
              "moves": ["e2e4"], "trained_color": "white"},
    )
    assert response.status_code == 200, response.text
    assert dispatched[0][0] == "repertoire.branch.add"
    assert dispatched[0][1]["repertoire_id"] == "rep"
    assert dispatched[0][2] == "branch-1"


def test_postgres_branch_edit_checkpoints_graph_and_coverage_with_line(monkeypatch):
    from app import branch_commands

    events = []

    class Database:
        def execute_native(self, statement, _parameters=()):
            if "FROM repertoires" in statement:
                return SimpleNamespace(fetchone=lambda: (1,))
            if "INSERT INTO repertoire_lines" in statement:
                events.append("line")
                return SimpleNamespace(fetchone=lambda: ("line",))
            if "FROM settings" in statement:
                return SimpleNamespace(fetchone=lambda: (2,))
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(branch_commands, "request_graph_rebuild_in_transaction",
                        lambda *_args: events.append("graph"))
    monkeypatch.setattr(branch_commands, "request_coverage_seed_in_transaction",
                        lambda *_args, **_kwargs: events.append("coverage"))
    monkeypatch.setattr(branch_commands, "integrity_summary",
                        lambda *_args: {"status": "unchecked"})
    result = branch_commands.add_repertoire_branch(Database(), {
        "repertoire_id": "rep", "starting_fen": chess.STARTING_FEN,
        "moves": ["e2e4", "e7e5"], "trained_color": "white",
    })
    assert result["duplicate"] is False
    assert result["moves"] == ["e2e4", "e7e5"]
    assert events == ["line", "graph", "coverage"]


def test_postgres_endgame_template_admission_dispatches_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "id": "template", "card_id": "card", "sample_fen": "fen",
                            "already_exists": False,
                        })
    response = TestClient(main.app).post(
        "/api/endgames/templates", headers={"Idempotency-Key": "endgame-1"},
        json={"name": "King and rook", "white_material": "KR",
              "black_material": "K", "trained_color": "white"},
    )
    assert response.status_code == 200, response.text
    assert dispatched[0][0] == "endgames.template.create"
    assert dispatched[0][1]["name"] == "King and rook"
    assert dispatched[0][2] == "endgame-1"


def test_postgres_tablebase_probe_never_writes_from_read_only_api(monkeypatch):
    import asyncio
    from app import main

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "connection", lambda: (_ for _ in ()).throw(
        AssertionError("API must not open a writable database connection")))

    class CacheRead:
        def execute(self, *_args):
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(CacheRead()))

    class Response:
        status_code = 200
        is_success = True

        def json(self):
            return {"category": "win", "moves": []}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: Client())
    assert asyncio.run(main.tablebase("8/8/8/8/8/8/4k3/4K3 w - - 0 1"))["category"] == "win"


def test_postgres_tablebase_redis_cache_hit_and_failure_fallback(monkeypatch):
    import asyncio
    from redis import RedisError
    from app import main

    cached = []
    fetches = []

    class Cache:
        async def get(self, _key):
            if cached:
                return cached[0]
            raise RedisError("cache unavailable")

        async def setex(self, _key, _ttl, value):
            cached.append(value)

        async def aclose(self):
            return None

    class Database:
        def execute(self, *_args):
            return SimpleNamespace(fetchone=lambda: None)

    class Response:
        status_code = 200
        is_success = True

        def json(self):
            return {"category": "draw", "moves": []}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            fetches.append(True)
            return Response()

    monkeypatch.setenv("TEMPO_REDIS_URL", "redis://rehearsal")
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.AsyncRedis, "from_url", lambda _url: Cache())
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(Database()))
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: Client())
    fen = "8/8/8/8/8/8/4k3/4K3 w - - 0 1"
    assert asyncio.run(main.tablebase(fen))["category"] == "draw"
    assert asyncio.run(main.tablebase(fen))["category"] == "draw"
    assert len(fetches) == 1


def test_postgres_endgame_attempt_prepares_outside_worker_and_dispatches_idempotently(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    seeds = []

    class TemplateRead:
        def execute(self, *_args):
            return SimpleNamespace(fetchone=lambda: {
                "white_material": "KR", "black_material": "K",
                "trained_color": "white", "goal_mix": "both",
            })

    async def tablebase(_fen):
        return {"category": "win", "moves": []}

    def position(*_args, seed):
        seeds.append(seed)
        return "8/8/8/8/8/8/4k3/4K3 w - - 0 1"

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "read_operation", lambda *_args, **_kwargs: {"state": "pending"})
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext(TemplateRead()))
    monkeypatch.setattr(main, "tablebase", tablebase)
    monkeypatch.setattr(main, "generate_position", position)
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "id": "attempt", "fen": payload["fen"],
                            "target": payload["target"], "moves": payload["moves"],
                        })
    client = TestClient(main.app)
    for _ in range(2):
        response = client.post("/api/endgames/templates/template/attempt",
                               headers={"Idempotency-Key": "attempt-1"})
        assert response.status_code == 200, response.text
    assert len(set(seeds)) == 1
    assert [item[0] for item in dispatched] == ["endgames.attempt.create"] * 2
    assert [item[2] for item in dispatched] == ["attempt-1"] * 2


def test_postgres_settings_update_dispatches_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    observed = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key: observed.append(
            (name, payload, idempotency_key)
        ) or payload["settings"],
    )
    response = TestClient(main.app).put(
        "/api/settings", headers={"Idempotency-Key": "settings-1"},
        json={"new_cards_per_day": 12},
    )
    assert response.status_code == 200, response.text
    assert response.json()["new_cards_per_day"] == 12
    assert observed[0][0] == "settings.update"
    assert observed[0][1]["supplied_fields"] == ["new_cards_per_day"]
    assert observed[0][2] == "settings-1"


def test_postgres_settings_update_refreshes_queue_and_preserves_omitted_defense_flag(monkeypatch):
    from app.models import Settings
    from app import settings_commands

    settings = Settings(new_cards_per_day=12)
    existing = settings.model_dump(mode="json")
    existing["include_defensive_cards_in_daily_stack"] = 0
    statements = []
    refresh_dates = []

    class RecordingDatabase:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            if statement.startswith("SELECT * FROM settings"):
                return SimpleNamespace(fetchone=lambda: existing)
            return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(settings_commands, "request_queue_refresh_in_transaction",
                        lambda database, queue_date: refresh_dates.append(queue_date))
    response = settings_commands.update_settings(
        RecordingDatabase(),
        {"settings": settings.model_dump(mode="json"), "supplied_fields": ["new_cards_per_day"]},
    )
    assert response["include_defensive_cards_in_daily_stack"] is False
    update_statement, update_parameters = statements[1]
    assert "include_defensive_cards_in_daily_stack=?" in update_statement
    assert update_parameters[list(settings_commands._SETTINGS_COLUMNS).index(
        "include_defensive_cards_in_daily_stack"
    )] == 0
    assert refresh_dates == [date.today().isoformat()]


def test_postgres_settings_update_rejects_unsupported_coverage_refresh(monkeypatch):
    from app.models import Settings
    from app import settings_commands

    existing = Settings().model_dump(mode="json")
    existing["coverage_maia_elo"] = 1100
    statements = []

    class RecordingDatabase:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            return SimpleNamespace(fetchone=lambda: existing)

    with pytest.raises(HTTPException) as error:
        settings_commands.update_settings(
            RecordingDatabase(),
            {"settings": Settings().model_dump(mode="json"), "supplied_fields": ["coverage_maia_elo"]},
        )
    assert error.value.status_code == 503
    assert statements == ["SELECT * FROM settings WHERE id=1 FOR UPDATE"]


def test_postgres_game_sync_admission_serializes_and_checkpoints_job(monkeypatch):
    from app import game_sync_commands

    statements = []
    tasks = []

    class RecordingConnection:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: (None,) if "MAX(played_at)" in statement else None)

    database = RecordingConnection()
    database.raw = database
    monkeypatch.setattr(game_sync_commands, "enqueue_task_in_transaction",
                        lambda connection, kind, key, payload, **options:
                        tasks.append((kind, key, payload, options)))
    result = game_sync_commands.enqueue_game_sync(
        database, {"lichess_username": " alice ", "chesscom_username": ""},
    )
    assert result["status"] == "queued"
    assert statements[0][0].startswith("SELECT pg_advisory_xact_lock")
    assert "FOR UPDATE" in statements[1][0]
    saved_request = json.loads(statements[2][1][1])
    assert saved_request["lichess_username"] == "alice"
    assert len(tasks) == 1
    assert tasks[0][0] == "game_sync_window"
    assert tasks[0][2]["job_id"] == result["job_id"]
    assert tasks[0][2]["window_id"] in tasks[0][1]
    assert tasks[0][3] == {"priority": 80}
    assert any("INSERT INTO game_sync_windows" in statement for statement, _ in statements)


def test_postgres_game_sync_admission_coalesces_existing_active_job(monkeypatch):
    from app import game_sync_commands

    class ExistingJobConnection:
        def execute(self, statement, parameters=()):
            if "FROM game_sync_jobs" in statement:
                return SimpleNamespace(fetchone=lambda: {"id": "active-sync", "status": "running"})
            if "FROM game_sync_windows" in statement:
                return SimpleNamespace(fetchone=lambda: (1,))
            if statement.startswith("INSERT INTO game_sync_jobs"):
                pytest.fail("Existing sync was duplicated")
            return SimpleNamespace()

    database = ExistingJobConnection()
    database.raw = database
    monkeypatch.setattr(game_sync_commands, "enqueue_task_in_transaction",
                        lambda *args, **kwargs: pytest.fail("Existing sync was requeued"))
    result = game_sync_commands.enqueue_game_sync(
        database, {"lichess_username": "alice"},
    )
    assert result == {"imported": 0, "job_id": "active-sync", "status": "running", "providers": {}}


def test_postgres_game_sync_admission_recovers_imported_active_job_without_windows(monkeypatch):
    from app import game_sync_commands

    planned = []
    statements = []

    class ImportedJobConnection:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            if "FROM game_sync_jobs" in statement:
                return SimpleNamespace(fetchone=lambda: {
                    "id": "imported-sync", "status": "running",
                    "request_json": json.dumps({"lichess_username": "alice"}),
                    "created_at": "2026-09-27T00:00:00+00:00",
                })
            if "FROM game_sync_windows" in statement:
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace()

    database = ImportedJobConnection()
    database.raw = database
    monkeypatch.setattr(game_sync_commands, "_plan_job_windows",
                        lambda connection, job_id, request, created_at:
                        planned.append((job_id, request.lichess_username)))
    result = game_sync_commands.enqueue_game_sync(database, {"lichess_username": "alice"})
    assert result["job_id"] == "imported-sync"
    assert result["status"] == "queued"
    assert planned == [("imported-sync", "alice")]
    assert any("UPDATE game_sync_jobs SET status='queued'" in statement for statement in statements)


def test_postgres_game_sync_route_dispatches_celery_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {
                            "imported": 0, "job_id": "sync-job", "status": "queued",
                            "providers": {},
                        })
    response = TestClient(main.app).post(
        "/api/games/sync", headers={"Idempotency-Key": "sync-command-1"},
        json={"lichess_username": " alice ", "chesscom_username": ""},
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == "sync-job"
    assert dispatched[0][0] == "games.sync.enqueue"
    assert dispatched[0][1]["lichess_username"] == "alice"
    assert dispatched[0][2] == "sync-command-1"


def test_postgres_game_sync_admission_preserves_provider_rate_limit(monkeypatch):
    from app import game_sync_commands

    class RateLimitedDatabase:
        def execute(self, statement, parameters=()):
            if "FROM game_sync_jobs" in statement:
                return SimpleNamespace(fetchone=lambda: None)
            if "FROM game_sync_state" in statement:
                return SimpleNamespace(fetchone=lambda: {
                    "username": "alice", "retry_after": "2099-01-01T00:00:00+00:00",
                })
            return SimpleNamespace(fetchone=lambda: None)

    database = RateLimitedDatabase()
    database.raw = database
    monkeypatch.setattr(game_sync_commands, "enqueue_task_in_transaction",
                        lambda *args, **kwargs: pytest.fail("Rate-limited sync was queued"))
    with pytest.raises(HTTPException) as error:
        game_sync_commands.enqueue_game_sync(database, {"lichess_username": "alice"})
    assert error.value.status_code == 429


def test_postgres_game_sync_publishes_one_game_and_followup_intents_atomically():
    from app.services.game_record import GameRecord
    from app.services.postgres_game_sync import persist_game_record_in_transaction

    statements = []

    class RecordingConnection:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: None)

    database = RecordingConnection()
    database.raw = database
    record = GameRecord(
        provider="lichess", username="alice", provider_game_id="game-1",
        played_at="2026-09-27T12:00:00+00:00", speed="rapid", rated=True,
        color="white", result="1-0",
        start_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        uci_moves=["e2e4"], content_hash="hash-1",
    )
    assert persist_game_record_in_transaction(database, record) == (
        "inserted", "lichess:game-1",
    )
    assert statements[0][0].startswith("SELECT pg_advisory_xact_lock")
    assert any("INSERT INTO imported_games" in statement for statement, _ in statements)
    assert any("INSERT INTO game_analysis_jobs" in statement for statement, _ in statements)
    assert any("INSERT INTO game_derivation_jobs" in statement for statement, _ in statements)
    assert any("DELETE FROM daily_chess_snapshots" in statement for statement, _ in statements)


def test_postgres_game_sync_record_waits_for_foreground_and_discards_stale_replay(monkeypatch):
    from contextlib import contextmanager
    from app.services import postgres_game_sync

    foreground_finished = threading.Event()
    connection_opened = threading.Event()
    publications = []
    active_lease = {"token": "lease-1"}
    claimed_task = {
        "id": "sync-record", "generation": 1, "lease_token": "lease-1",
        "payload": {"job_id": "sync-job", "record": {
            "provider": "lichess", "username": "alice", "provider_game_id": "game-1",
            "played_at": "2026-09-27T12:00:00+00:00", "speed": "rapid",
            "rated": True, "color": "white", "result": "1-0",
            "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "uci_moves": ["e2e4"],
        }},
    }

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM game_sync_jobs" in statement:
                return SimpleNamespace(fetchone=lambda: {"status": "queued", "result_json": None})
            return SimpleNamespace(rowcount=1)

    @contextmanager
    def wait_for_foreground():
        foreground_finished.wait()
        yield

    @contextmanager
    def test_connection(*, read_only, background):
        assert not read_only and background
        connection_opened.set()
        yield Database()

    monkeypatch.setattr(postgres_game_sync, "background_lease", wait_for_foreground)
    monkeypatch.setattr(postgres_game_sync, "connection", test_connection)
    monkeypatch.setattr(postgres_game_sync, "lock_current_slice",
                        lambda database, task: task["lease_token"] == active_lease["token"])
    monkeypatch.setattr(postgres_game_sync, "persist_game_record_in_transaction",
                        lambda database, record: publications.append(record.provider_game_id)
                        or ("inserted", "lichess:game-1"))
    monkeypatch.setattr(postgres_game_sync, "complete_task_slice_in_transaction",
                        lambda database, task: active_lease.update(token="complete") or True)
    monkeypatch.setattr(postgres_game_sync, "finish_game_sync_if_complete",
                        lambda database, job_id: False)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(postgres_game_sync.execute_game_sync_record_slice(claimed_task)),
    )
    worker.start()
    assert not connection_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [True]
    assert publications == ["game-1"]
    assert postgres_game_sync.execute_game_sync_record_slice(claimed_task) is False
    assert publications == ["game-1"]


def test_postgres_api_startup_requests_todays_queue_through_foreground_command(monkeypatch):
    from app import main

    calls = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    monkeypatch.setattr(main, "initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key: calls.append(
                            (name, payload, idempotency_key)))

    async def open_application():
        async with main.lifespan(main.app):
            assert calls == ["initialize", (
                "queue.ensure_current", {"queue_date": date.today().isoformat()}, None,
            )]

    asyncio.run(open_application())


def test_postgres_browser_activity_extends_cross_process_foreground_admission(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    observed = []
    class RecordingRedis:
        def eval(self, script, key_count, key, timestamp, token, duration):
            observed.append((key_count, key, token, duration))

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)
    monkeypatch.setattr(redis_admission_gate, "client", lambda: RecordingRedis())
    response = TestClient(main.app).post("/api/system/browser-activity")
    assert response.status_code == 200, response.text
    assert observed == [(1, "tempo:admission:foreground", "browser-activity", 3000)]


def test_postgres_daily_queue_rollover_requests_refresh_until_ready(monkeypatch):
    from app import tasks

    projection = None
    submitted = []
    class QueueDatabase:
        def execute(self, statement, parameters):
            assert parameters == (date.today().isoformat(),)
            return self
        def fetchone(self):
            return projection

    @contextmanager
    def queue_connection():
        yield QueueDatabase()

    monkeypatch.setattr(tasks, "read_connection", queue_connection)
    monkeypatch.setattr(tasks.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(tasks, "execute_command",
                        lambda operation_id, name, payload:
                        submitted.append((name, payload)) or {"refresh_pending": True})
    assert tasks.ensure_daily_queue.run()
    assert submitted == [("queue.ensure_current", {"queue_date": date.today().isoformat()})]
    projection = {"state": "ready", "refresh_pending": 0}
    assert not tasks.ensure_daily_queue.run()
    assert len(submitted) == 1


def test_postgres_priority_opening_plan_preserves_gameplay_breadth_and_shared_cards():
    from app import main

    def candidate(card_id, repertoire_id, *, reason=None, score=0, lines="[]"):
        return {"id": card_id, "repertoire_id": repertoire_id, "moves_json": "[]",
                "gameplay_priority_reason": reason, "priority_date": "2026-09-27",
                "priority_score": score, "completed_line_ids_json": lines,
                "frontier_decisions_json": "[]"}

    candidates = [
        candidate("a", "white", score=20, lines='["one"]'),
        candidate("shared", "white", score=30, lines='["one"]'),
        candidate("miss", "white", reason="miss", score=1),
        candidate("shared", "black", score=30),
        candidate("black", "black", score=10),
    ]
    plan = main._plan_prioritized_opening_admissions(
        candidates, {"white": 0, "black": 0}, "2026-09-27", 2,
    )
    assert [(repertoire_id, row["id"]) for repertoire_id, row in plan] == [
        ("white", "miss"), ("white", "shared"), ("black", "black"),
    ]


def test_postgres_priority_opening_slice_checkpoints_one_item_and_rejects_stale_replay(monkeypatch):
    from app.services import postgres_queue_refresh

    current_lease = "first"
    saved_phase = None
    saved_payload = None
    admitted = []

    @contextmanager
    def section(*, background):
        assert background
        yield object()

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_lease, saved_phase, saved_payload
        saved_phase, saved_payload = next_phase, next_payload
        current_lease = "second"
        return True

    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice",
                        lambda database, task: task["lease_token"] == current_lease)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance)
    monkeypatch.setattr(postgres_queue_refresh, "_admit_one_prioritized_opening",
                        lambda database, day, planned: admitted.append(planned["card_id"]))
    plan = [{"card_id": "one", "repertoire_id": "white", "reason": None},
            {"card_id": "two", "repertoire_id": "white", "reason": None}]
    task = {"id": "queue-refresh", "generation": 4, "lease_token": "first",
            "payload": {"queue_date": "2026-09-27", "_queue_phase": "prioritized_opening_item",
                        "opening_plan": plan, "opening_index": 0}}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert saved_phase == "prioritized_opening_item"
    assert saved_payload["opening_index"] == 1
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert admitted == ["one"]
    resumed = {**task, "lease_token": "second", "payload": saved_payload}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(resumed)
    assert admitted == ["one", "two"]
    assert saved_phase == "admit_study"


def test_postgres_priority_opening_publication_translates_opportunity_json(tmp_path, monkeypatch):
    from app.services import postgres_queue_refresh
    monkeypatch.setattr(postgres_queue_refresh, "lock_queue_date_for_position",
                        lambda _database, _queue_date: None)

    database_path = tmp_path / "priority-opening-publication.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,state TEXT,introduced_at TEXT,
                               archived INTEGER,pending_validation INTEGER);
            CREATE TABLE daily_queue(queue_date TEXT,card_id TEXT,position INTEGER,
                                     gameplay_priority_reason TEXT,admission_repertoire_id TEXT);
            CREATE TABLE repertoire_opportunities(repertoire_id TEXT,card_id TEXT,
                kind TEXT,evidence_json TEXT,status TEXT,resolved_at TEXT,updated_at TEXT);
            INSERT INTO cards VALUES('opening','new',NULL,0,0);
            INSERT INTO repertoire_opportunities(repertoire_id,card_id,kind,evidence_json,status)
                VALUES('rep','opening','weak_known_decision','{}','active');
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute_native(self, statement, parameters=()):
            assert "json_extract" not in statement
            return self.database.execute(
                statement.replace("%s", "?").replace("FOR UPDATE", ""), parameters,
            )

        def execute(self, statement, parameters=()):
            return self.database.execute(statement, parameters)

    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        postgres_queue_refresh._admit_one_prioritized_opening(
            NativeSqlite(database), "2026-09-27",
            {"card_id": "opening", "repertoire_id": "rep", "reason": "gameplay"},
        )
    with sqlite3.connect(database_path) as database:
        assert database.execute(
            "SELECT card_id,position,gameplay_priority_reason FROM daily_queue",
        ).fetchone() == ("opening", 0, "gameplay")
        assert database.execute(
            "SELECT state,introduced_at FROM cards WHERE id='opening'",
        ).fetchone() == ("learning", "2026-09-27")
        assert database.execute(
            "SELECT status FROM repertoire_opportunities",
        ).fetchone()[0] == "resolved"


def test_postgres_study_admission_slices_respect_quota_burial_and_replay(monkeypatch, tmp_path):
    from app.services import postgres_queue_refresh
    monkeypatch.setattr(postgres_queue_refresh, "lock_queue_date_for_position",
                        lambda _database, _queue_date: None)

    database_path = tmp_path / "study-admission.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE settings(id INTEGER PRIMARY KEY,study_new_per_day INTEGER);
            CREATE TABLE cards(id TEXT PRIMARY KEY,study_exercise_id TEXT,content_type TEXT,
                               state TEXT,archived INTEGER,pending_validation INTEGER,due_date TEXT);
            CREATE TABLE studies(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT,created_at TEXT);
            CREATE TABLE study_sibling_burials(exercise_id TEXT,study_day TEXT);
            CREATE TABLE daily_queue(queue_date TEXT,card_id TEXT,position INTEGER,
                                     status TEXT DEFAULT 'queued',card_bucket TEXT,admission_kind TEXT);
            INSERT INTO settings VALUES(1,2);
            INSERT INTO studies VALUES('study',0);
            INSERT INTO study_exercises VALUES('first','study','published','2026-01-01');
            INSERT INTO study_exercises VALUES('second','study','published','2026-01-02');
            INSERT INTO study_exercises VALUES('buried','study','published','2026-01-03');
            INSERT INTO cards VALUES('first-card','first','study_exercise','new',0,0,'2026-09-26');
            INSERT INTO cards VALUES('second-card','second','study_exercise','new',0,0,'2026-09-26');
            INSERT INTO cards VALUES('buried-card','buried','study_exercise','new',0,0,'2026-09-26');
            INSERT INTO study_sibling_burials VALUES('buried','2026-09-27');
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute_native(self, statement, parameters=()):
            return self.database.execute(
                statement.replace("%s", "?").replace("FOR UPDATE", ""), parameters,
            )

        execute = execute_native

    @contextmanager
    def read_section():
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            yield NativeSqlite(database)

    monkeypatch.setattr(postgres_queue_refresh, "background_read_connection", read_section)
    admitted = []
    while (card_id := postgres_queue_refresh._prepare_study_admission("2026-09-27")) is not None:
        with sqlite3.connect(database_path) as database:
            assert postgres_queue_refresh._admit_one_study_card(
                NativeSqlite(database), "2026-09-27", card_id,
            )
            assert not postgres_queue_refresh._admit_one_study_card(
                NativeSqlite(database), "2026-09-27", card_id,
            )
        admitted.append(card_id)
    assert admitted == ["first-card", "second-card"]
    with sqlite3.connect(database_path) as database:
        assert list(database.execute(
            "SELECT card_id,position,card_bucket,admission_kind FROM daily_queue ORDER BY position",
        )) == [("first-card", 0, "study_exercise", "new"),
               ("second-card", 1, "study_exercise", "new")]


def test_postgres_study_admission_rechecks_burial_and_quota_after_foreground_lock(monkeypatch, tmp_path):
    from app.services import postgres_queue_refresh

    database_path = tmp_path / "study-admission-race.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE settings(id INTEGER PRIMARY KEY,study_new_per_day INTEGER);
            CREATE TABLE cards(id TEXT PRIMARY KEY,study_exercise_id TEXT,content_type TEXT,
                state TEXT,archived INTEGER,pending_validation INTEGER,due_date TEXT);
            CREATE TABLE studies(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT);
            CREATE TABLE study_sibling_burials(exercise_id TEXT,study_day TEXT);
            CREATE TABLE daily_queue(queue_date TEXT,card_id TEXT,position INTEGER,
                status TEXT DEFAULT 'queued',card_bucket TEXT,admission_kind TEXT);
            INSERT INTO settings VALUES(1,1);
            INSERT INTO studies VALUES('study',0);
            INSERT INTO study_exercises VALUES('candidate','study','published');
            INSERT INTO study_exercises VALUES('other','study','published');
            INSERT INTO cards VALUES('candidate-card','candidate','study_exercise','new',0,0,'2026-09-26');
            INSERT INTO cards VALUES('other-card','other','study_exercise','new',0,0,'2026-09-26');
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute_native(self, statement, parameters=()):
            return self.database.execute(
                statement.replace("%s", "?").replace("FOR UPDATE", ""), parameters,
            )

    with sqlite3.connect(database_path) as database:
        adapter = NativeSqlite(database)
        monkeypatch.setattr(postgres_queue_refresh, "lock_queue_date_for_position",
                            lambda _database, day: database.execute(
                                "INSERT INTO study_sibling_burials VALUES('candidate',?)", (day,),
                            ))
        assert not postgres_queue_refresh._admit_one_study_card(
            adapter, "2026-09-27", "candidate-card",
        )
        assert database.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 0
        database.execute("DELETE FROM study_sibling_burials")
        monkeypatch.setattr(postgres_queue_refresh, "lock_queue_date_for_position",
                            lambda _database, day: database.execute(
                                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'other-card',0)",
                                (day,),
                            ))
        assert not postgres_queue_refresh._admit_one_study_card(
            adapter, "2026-09-27", "candidate-card",
        )
        assert database.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == 1
        database.execute("DELETE FROM daily_queue")
        monkeypatch.setattr(postgres_queue_refresh, "lock_queue_date_for_position",
                            lambda _database, _day: None)
        assert postgres_queue_refresh._admit_one_study_card(
            adapter, "2026-09-27", "candidate-card",
        )


def test_postgres_study_admission_waits_for_foreground_and_checkpoints_restart(monkeypatch):
    from app.services import postgres_queue_refresh

    admitted = []
    order = []
    current_lease = "first"
    saved_payload = None

    @contextmanager
    def section(*, background):
        assert background
        yield object()

    def wait_for_foreground():
        order.append("foreground-cleared")

    def prepare(queue_date):
        assert order[-1] == "foreground-cleared"
        return "study-card" if not admitted else None

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_lease, saved_payload
        saved_payload = next_payload
        current_lease = "second"
        return True

    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground",
                        wait_for_foreground)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice",
                        lambda database, task: task["lease_token"] == current_lease)
    monkeypatch.setattr(postgres_queue_refresh, "_prepare_study_admission", prepare)
    monkeypatch.setattr(postgres_queue_refresh, "_admit_one_study_card",
                        lambda database, queue_date, card_id: admitted.append(card_id) or True)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance)
    task = {"id": "study-refresh", "generation": 3, "lease_token": "first",
            "payload": {"queue_date": "2026-09-27", "_queue_phase": "admit_study"}}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert admitted == ["study-card"]
    assert saved_payload["_queue_phase"] == "admit_study"
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert admitted == ["study-card"]
    resumed = {**task, "lease_token": "second", "payload": saved_payload}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(resumed)
    assert saved_payload["_queue_phase"] == "randomize_queue"


def test_postgres_queue_randomization_replans_changed_membership_and_rejects_stale_lease(monkeypatch):
    from app.services import postgres_queue_refresh

    current_lease = "first"
    saved_payload = None
    observed = []
    plan = {"seed": 7, "membership_hash": "old", "entries": []}

    @contextmanager
    def section(*, background):
        assert background
        yield object()

    def wait_for_foreground():
        observed.append("foreground-cleared")

    def prepare(queue_date):
        assert observed[-1] == "foreground-cleared"
        return plan

    def publish(database, queue_date, prepared):
        assert prepared is plan
        observed.append("publish")
        return observed.count("publish") > 1

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_lease, saved_payload
        saved_payload = next_payload
        current_lease = "second"
        return True

    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground",
                        wait_for_foreground)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice",
                        lambda database, task: task["lease_token"] == current_lease)
    monkeypatch.setattr(postgres_queue_refresh, "_prepare_queue_randomization", prepare)
    monkeypatch.setattr(postgres_queue_refresh, "_publish_queue_randomization", publish)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance)
    task = {"id": "queue-refresh", "generation": 4, "lease_token": "first",
            "payload": {"queue_date": "2026-09-27", "_queue_phase": "randomize_queue"}}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert saved_payload["_queue_phase"] == "randomize_queue"
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert observed.count("publish") == 1
    resumed = {**task, "lease_token": "second", "payload": saved_payload}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(resumed)
    assert saved_payload["_queue_phase"] == "quarantine"
    assert observed.count("publish") == 2


def test_postgres_queue_randomization_hash_preserves_membership_order():
    from app import main

    rows = [{"id": 2, "card_id": "second"}, {"id": 4, "card_id": "fourth"}]
    assert main._queue_membership_hash(rows) != main._queue_membership_hash(rows[::-1])
    assert main._queue_membership_hash(rows) == main._queue_membership_hash(list(rows))


def test_postgres_queue_randomization_noop_rechecks_foreground_membership():
    from app import main
    from app.services import postgres_queue_refresh

    class ChangedQueue:
        def execute_native(self, statement, parameters):
            assert ("FROM daily_queue" in statement
                    or statement.startswith("SELECT pg_advisory_xact_lock"))
            return self

        def fetchall(self):
            return [{"id": 10, "card_id": "just-added"}]

    for entries in (None, [{"id": 1, "position": 0,
                            "bucket": "opening", "kind": "new"}]):
        stale_plan = {"membership_hash": main._queue_membership_hash([]),
                      "entries": entries}
        assert not postgres_queue_refresh._publish_queue_randomization(
            ChangedQueue(), "2026-09-27", stale_plan,
        )


def test_postgres_queue_randomization_splits_review_lookup_into_bounded_reads(monkeypatch):
    from app.services import postgres_queue_refresh

    observed_queries = []
    queue_rows = [
        {"id": 1, "card_id": "explicit", "content_type": "opening",
         "admission_kind": "explicit", "gameplay_priority_reason": None},
        {"id": 2, "card_id": "reviewed", "content_type": "study_exercise",
         "admission_kind": None, "gameplay_priority_reason": None},
        {"id": 3, "card_id": "new", "content_type": "opening",
         "admission_kind": None, "gameplay_priority_reason": None},
    ]

    def bounded_read(statement, parameters=(), *, native=False):
        observed_queries.append((statement, native))
        if "FROM daily_queue q JOIN cards" in statement:
            return queue_rows
        if "SELECT DISTINCT card_id FROM reviews" in statement:
            assert parameters == (["explicit", "reviewed", "new"],)
            return [("explicit",), ("reviewed",)]
        if "FROM daily_queue_days" in statement:
            return []
        raise AssertionError(statement)

    monkeypatch.setattr(postgres_queue_refresh, "_bounded_read", bounded_read)
    plan = postgres_queue_refresh._prepare_queue_randomization("2026-09-27")
    assert plan is not None
    assert {entry["id"]: entry["kind"] for entry in plan["entries"]} == {
        1: "explicit", 2: "review", 3: "new",
    }
    assert len(observed_queries) == 3
    assert all("EXISTS(SELECT 1 FROM reviews" not in query for query, _ in observed_queries)


def test_postgres_opening_quarantine_validates_outside_database_and_replays_once(monkeypatch, tmp_path):
    from app.services import postgres_queue_refresh

    database_path = tmp_path / "opening-quarantine.db"
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE repertoires(id TEXT PRIMARY KEY);
            CREATE TABLE repertoire_cards(card_id TEXT,repertoire_id TEXT);
            CREATE TABLE repertoire_integrity_card_blocks(card_id TEXT,repertoire_id TEXT);
            CREATE TABLE repertoire_lines(repertoire_id TEXT,trained_color TEXT,created_at TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,repertoire_id TEXT,start_fen TEXT,
                               moves_json TEXT,trained_color TEXT,archived INTEGER,
                               content_type TEXT,state TEXT);
            CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,queue_date TEXT,card_id TEXT,status TEXT);
            CREATE TABLE queue_projection_diagnostics(queue_date TEXT,card_id TEXT,
                                                       message TEXT,PRIMARY KEY(queue_date,card_id));
            INSERT INTO repertoires VALUES('rep');
        """)
        database.executemany(
            "INSERT INTO cards VALUES(?,?,?,?,?,0,'opening','learning')",
            [("valid", "rep", fen, '["e2e4"]', "white"),
             ("invalid", "rep", fen, '["e2e4","e7e5"]', "white"),
             ("later", "rep", fen, '["d2d4"]', "white")],
        )
        database.executemany(
            "INSERT INTO daily_queue VALUES(?,'2026-09-27',?,'queued')",
            [(1, "valid"), (2, "invalid"), (3, "later")],
        )

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute_native(self, statement, parameters=()):
            return self.database.execute(
                statement.replace("%s", "?").replace("FOR UPDATE OF q,c", ""),
                parameters,
            )

    @contextmanager
    def read_section():
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            yield NativeSqlite(database)

    monkeypatch.setattr(postgres_queue_refresh, "background_read_connection", read_section)
    first = postgres_queue_refresh._prepare_opening_quarantine("2026-09-27", 0)
    assert first["after_entry_id"] == 2
    assert first["has_more"]
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        assert postgres_queue_refresh._quarantine_one_opening(
            NativeSqlite(database), "2026-09-27", first["invalid"],
        )
        assert not postgres_queue_refresh._quarantine_one_opening(
            NativeSqlite(database), "2026-09-27", first["invalid"],
        )
    second = postgres_queue_refresh._prepare_opening_quarantine(
        "2026-09-27", first["after_entry_id"],
    )
    assert second == {"after_entry_id": 3, "has_more": False, "invalid": None}
    with sqlite3.connect(database_path) as database:
        assert list(database.execute("SELECT card_id,status FROM daily_queue ORDER BY id")) == [
            ("valid", "queued"), ("invalid", "skipped"), ("later", "queued"),
        ]
        assert database.execute(
            "SELECT COUNT(*) FROM queue_projection_diagnostics",
        ).fetchone()[0] == 1


def test_postgres_opening_quarantine_yields_to_foreground_and_resumes_cursor(monkeypatch):
    from app.services import postgres_queue_refresh

    current_lease = "first"
    saved_payload = None
    observed = []

    class Database:
        def execute_native(self, statement, parameters=()):
            assert "DELETE FROM queue_projection_diagnostics" in statement
            observed.append("diagnostics-reset")

    @contextmanager
    def section(*, background):
        assert background
        yield Database()

    def wait_for_foreground():
        observed.append("foreground-cleared")

    def prepare(queue_date, after_entry_id):
        assert observed[-1] == "foreground-cleared"
        if after_entry_id == 0:
            return {"after_entry_id": 12, "has_more": True,
                    "invalid": {"card_id": "bad"}}
        return {"after_entry_id": 19, "has_more": False, "invalid": None}

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_lease, saved_payload
        saved_payload = next_payload
        current_lease = "second"
        return True

    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground",
                        wait_for_foreground)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice",
                        lambda database, task: task["lease_token"] == current_lease)
    monkeypatch.setattr(postgres_queue_refresh, "_prepare_opening_quarantine", prepare)
    monkeypatch.setattr(postgres_queue_refresh, "_quarantine_one_opening",
                        lambda database, day, candidate: observed.append(candidate["card_id"]))
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance)
    task = {"id": "queue-refresh", "generation": 5, "lease_token": "first",
            "payload": {"queue_date": "2026-09-27", "_queue_phase": "quarantine"}}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert saved_payload["after_entry_id"] == 12
    assert saved_payload["_queue_phase"] == "quarantine"
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert observed.count("bad") == 1
    resumed = {**task, "lease_token": "second", "payload": saved_payload}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(resumed)
    assert saved_payload["after_entry_id"] == 19
    assert saved_payload["_queue_phase"] == "publish_projection"
    assert observed.count("diagnostics-reset") == 1


def test_postgres_queue_projection_and_task_completion_commit_together(monkeypatch, tmp_path):
    from app.services import postgres_queue_refresh

    database_path = tmp_path / "queue-projection.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE background_tasks(id TEXT PRIMARY KEY,generation INTEGER,
                lease_token TEXT,state TEXT,phase TEXT,lease_expires_at TEXT,
                last_error TEXT,completed_at TEXT,updated_at TEXT);
            CREATE TABLE background_task_events(id INTEGER PRIMARY KEY,task_id TEXT,
                generation INTEGER,event TEXT,phase TEXT,detail TEXT,created_at TEXT);
            CREATE TABLE queue_projections(queue_date TEXT PRIMARY KEY,state TEXT,
                generation INTEGER,refresh_pending INTEGER,last_error TEXT,
                blocked_count INTEGER,updated_at TEXT);
            INSERT INTO background_tasks(id,generation,lease_token,state,phase)
                VALUES('queue-job',3,'current','leased','publish_projection');
            INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
                VALUES('2026-09-27','refreshing',5,1);
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

        def execute_native(self, statement, parameters=()):
            return self.database.execute(statement.replace("%s", "?"), parameters)

    @contextmanager
    def section(*, background):
        assert background
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            yield NativeSqlite(database)

    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "_prepare_projection_blocked_count",
                        lambda queue_date: 3)
    task = {"id": "queue-job", "generation": 3, "lease_token": "current",
            "payload": {"queue_date": "2026-09-27", "_queue_phase": "publish_projection"}}
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    with sqlite3.connect(database_path) as database:
        assert database.execute(
            "SELECT state,generation,refresh_pending,blocked_count FROM queue_projections",
        ).fetchone() == ("ready", 6, 0, 3)
        assert database.execute(
            "SELECT state,phase,lease_token FROM background_tasks",
        ).fetchone() == ("complete", "published", None)
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT generation FROM queue_projections").fetchone()[0] == 6
        assert database.execute(
            "SELECT COUNT(*) FROM background_task_events WHERE event='published'",
        ).fetchone()[0] == 1


def test_postgres_queue_celery_dispatch_keeps_atomic_slice_receipt(monkeypatch):
    from app import tasks

    claimed = {"kind": "daily_queue", "id": "queue-job", "generation": 3,
               "lease_token": "current", "payload": {"queue_date": "2026-09-27"}}
    handled = []
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *_args: nullcontext())
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice",
                        lambda task: handled.append(task["id"]) or False)
    monkeypatch.setattr(tasks, "complete_task",
                        lambda *_args: pytest.fail("Queue handler completed its own task atomically"))
    assert tasks.execute_background_slice.run(claimed) is False
    assert handled == ["queue-job"]
    sent = []
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice",
                        lambda task: handled.append(task["id"]) or True)
    monkeypatch.setattr(tasks.celery_app, "send_task",
                        lambda task_name, **_kwargs: sent.append(task_name))
    assert tasks.execute_background_slice.run(claimed) is True
    assert sent == ["app.tasks.poll_background_tasks"]
    assert "daily_queue" in tasks._SUPPORTED_BACKGROUND_KINDS


def test_postgres_queue_ensure_command_coalesces_active_refresh(monkeypatch):
    from app import queue_commands

    enqueued = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def __init__(self):
            self.projection = None
            self.task = None
            self.writes = []

        def execute(self, statement, parameters=()):
            if "FROM queue_projections" in statement:
                return Cursor(self.projection)
            if "FROM background_tasks" in statement:
                return Cursor(self.task)
            self.writes.append((statement, parameters))
            return Cursor(None)

    monkeypatch.setattr(queue_commands, "enqueue_task_in_transaction",
                        lambda database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)) or {"id": "new-task"})
    database = Database()
    first = queue_commands.ensure_current_queue(
        database, {"queue_date": "2026-09-27"},
    )
    assert first == {"queue_date": "2026-09-27", "refresh_pending": True,
                     "task_id": "new-task"}
    assert enqueued == [("daily_queue", "current", {"queue_date": "2026-09-27"}, 10)]
    assert len(database.writes) == 1
    database.task = {"id": "new-task", "state": "leased",
                     "payload_json": '{"queue_date":"2026-09-27"}'}
    second = queue_commands.ensure_current_queue(
        database, {"queue_date": "2026-09-27"},
    )
    assert second == first
    assert len(enqueued) == 1
    database.projection = {"state": "ready", "refresh_pending": 0}
    assert queue_commands.ensure_current_queue(
        database, {"queue_date": "2026-09-27"},
    ) == {"queue_date": "2026-09-27", "refresh_pending": False}
    assert len(enqueued) == 1


def test_postgres_cutover_schema_keeps_json_array_length_available(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from generate_postgres_schema import generate_schema

    source = tmp_path / "source.db"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE example(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    schema = generate_schema(source)
    assert "CREATE FUNCTION json_array_length(document TEXT)" in schema
    assert "jsonb_array_length(document::jsonb)" in schema


def test_postgres_cutover_inventory_includes_native_postgres_queries():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from audit_storage_cutover import inspect_python_file

    source = Path(__file__).resolve().parents[1] / "app" / "services" / "postgres_queue_refresh.py"
    report = inspect_python_file(source)
    assert any(site["operation"] == "execute_native" for site in report["access_sites"])


def test_postgres_cutover_study_chapter_routes_dispatch_named_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"accepted": name}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(study_routes, "dispatch_command", record_command)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    client = TestClient(main.app)
    headers = {"Idempotency-Key": "study-command-1"}
    requests = (
        ("post", "/api/studies/study-1/chapters", {"title": "Chapter"}, "studies.chapters.create"),
        ("put", "/api/studies/study-1/chapters/order", ["chapter-1"], "studies.chapters.reorder"),
        ("patch", "/api/studies/study-1/chapters/chapter-1", {"title": "Renamed"}, "studies.chapters.rename"),
        ("post", "/api/studies/study-1/links", {
            "source_position_id": "position-1", "target_position_id": "position-2",
            "relation": "illustrates",
        }, "studies.links.create"),
    )
    for method, path, body, command_name in requests:
        response = getattr(client, method)(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": command_name}
    assert [name for name, _, _ in dispatched] == [item[3] for item in requests]
    assert all(key == "study-command-1" for _, _, key in dispatched)


def test_postgres_study_archive_routes_dispatch_idempotent_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(study_routes, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    client = TestClient(main.app)
    for action in ("archive", "unarchive"):
        response = client.post(
            f"/api/studies/study-1/{action}",
            headers={"Idempotency-Key": f"study-1-{action}"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": f"studies.{action}"}
    assert dispatched == [
        ("studies.archive", {"study_id": "study-1"}, "study-1-archive"),
        ("studies.unarchive", {"study_id": "study-1"}, "study-1-unarchive"),
    ]


def test_postgres_study_archive_queues_refresh_with_mutation(monkeypatch, tmp_path):
    from app import study_commands

    database_path = tmp_path / "archive-command.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE studies(id TEXT PRIMARY KEY,archived INTEGER,updated_at TEXT);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,study_exercise_id TEXT,archived INTEGER);
            INSERT INTO studies VALUES('study',0,NULL);
            INSERT INTO study_exercises VALUES('exercise','study');
            INSERT INTO cards VALUES('card','exercise',0);
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

    refreshed = []
    monkeypatch.setattr(study_commands, "request_queue_refresh_in_transaction",
                        lambda database, queue_date: refreshed.append(queue_date))
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        assert study_commands.archive_study(adapter, {"study_id": "study"}) == {
            "id": "study", "archived": True,
        }
        assert database.execute("SELECT archived FROM cards WHERE id='card'").fetchone()[0] == 1
        assert refreshed == [date.today().isoformat()]
        assert study_commands.unarchive_study(adapter, {"study_id": "study"}) == {
            "id": "study", "archived": False,
        }
        assert database.execute("SELECT archived FROM cards WHERE id='card'").fetchone()[0] == 1
    assert refreshed == [date.today().isoformat()]


def test_postgres_exercise_availability_routes_dispatch_idempotent_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(study_routes, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    client = TestClient(main.app)
    for action in ("suspend", "resume", "archive"):
        response = client.post(
            f"/api/studies/study-1/exercises/exercise-1/{action}",
            headers={"Idempotency-Key": f"exercise-1-{action}"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": f"studies.exercises.{action}"}
    assert dispatched == [
        (f"studies.exercises.{action}",
         {"study_id": "study-1", "exercise_id": "exercise-1"}, f"exercise-1-{action}")
        for action in ("suspend", "resume", "archive")
    ]


def test_postgres_exercise_availability_mutates_cards_and_queue_in_one_command(monkeypatch, tmp_path):
    from app import study_commands

    database_path = tmp_path / "exercise-availability.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,study_exercise_id TEXT,
                               archived INTEGER,pending_validation INTEGER);
            CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,card_id TEXT,status TEXT);
            INSERT INTO study_exercises VALUES('exercise','study','published');
            INSERT INTO cards VALUES('card','exercise',0,0);
            INSERT INTO daily_queue VALUES(1,'card','queued');
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

    refreshed = []
    monkeypatch.setattr(study_commands, "request_queue_refresh_in_transaction",
                        lambda database, queue_date: refreshed.append(queue_date))
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        payload = {"study_id": "study", "exercise_id": "exercise"}
        assert study_commands.suspend_exercise(adapter, payload) == {"suspended": True}
        assert database.execute("SELECT pending_validation FROM cards").fetchone()[0] == 1
        assert database.execute("SELECT status FROM daily_queue").fetchone()[0] == "blocked"
        assert study_commands.resume_exercise(adapter, payload) == {"suspended": False}
        assert database.execute("SELECT pending_validation FROM cards").fetchone()[0] == 0
        assert study_commands.archive_exercise(adapter, payload) == {"archived": True}
        assert database.execute("SELECT status FROM study_exercises").fetchone()[0] == "archived"
        assert database.execute("SELECT archived FROM cards").fetchone()[0] == 1
    assert refreshed == [date.today().isoformat()] * 3


def test_postgres_exercise_create_and_enroll_routes_dispatch_idempotent_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(study_routes, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    client = TestClient(main.app)
    exercise = {"position_id": "position-1", "specification": {
        "type": "explanation", "prompt": "Explain the idea", "rubric": "Mention the threat",
    }}
    created = client.post("/api/studies/study-1/exercises", json=exercise,
                          headers={"Idempotency-Key": "create-1"})
    enrolled = client.post("/api/studies/study-1/exercises/exercise-1/enroll",
                           headers={"Idempotency-Key": "enroll-1"})
    assert created.status_code == 200, created.text
    assert enrolled.status_code == 200, enrolled.text
    assert dispatched == [
        ("studies.exercises.create", {"study_id": "study-1", "exercise": {
            "position_id": "position-1", "specification": {
                "type": "explanation", "prompt": "Explain the idea", "rubric": "Mention the threat",
                "hint": "", "explanation": "", "further_analysis": "",
            }, "source": {}, "sibling_group": None, "point_value": None,
        }}, "create-1"),
        ("studies.exercises.enroll", {"study_id": "study-1", "exercise_id": "exercise-1"}, "enroll-1"),
    ]


def test_postgres_exercise_enrollment_replay_creates_one_card_and_queues_once(monkeypatch, tmp_path):
    import chess
    from app import study_commands

    database_path = tmp_path / "exercise-enrollment.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE studies(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE study_chapters(id TEXT PRIMARY KEY,study_id TEXT);
            CREATE TABLE study_sources(id TEXT PRIMARY KEY,chapter_id TEXT,valid INTEGER);
            CREATE TABLE study_positions(id TEXT PRIMARY KEY,source_id TEXT,fen TEXT,valid INTEGER);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,position_id TEXT,
                sibling_group TEXT,source_json TEXT,point_value REAL,created_at TEXT,updated_at TEXT,
                status TEXT DEFAULT 'draft',current_revision INTEGER DEFAULT 1);
            CREATE TABLE study_exercise_revisions(exercise_id TEXT,revision INTEGER,
                specification_json TEXT,digest TEXT,created_at TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,repertoire_id TEXT,kind TEXT,start_fen TEXT,
                moves_json TEXT,state TEXT,due_date TEXT,content_type TEXT,study_exercise_id TEXT,
                archived INTEGER DEFAULT 0);
            INSERT INTO studies VALUES('study',0);
            INSERT INTO study_chapters VALUES('chapter','study');
            INSERT INTO study_sources VALUES('source','chapter',1);
        """)
        database.execute("INSERT INTO study_positions VALUES('position','source',?,1)",
                         (chess.STARTING_FEN,))

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

    refreshed = []
    monkeypatch.setattr(study_commands, "request_queue_refresh_in_transaction",
                        lambda database, queue_date: refreshed.append(queue_date))
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        created = study_commands.create_exercise(adapter, {
            "study_id": "study", "exercise": {"position_id": "position", "specification": {
                "type": "explanation", "prompt": "Explain the position", "rubric": "Identify the idea",
            }},
        })
        assert created["revision"] == 1 and created["status"] == "draft"
        payload = {"study_id": "study", "exercise_id": created["id"]}
        first = study_commands.enroll_exercise(adapter, payload)
        replay = study_commands.enroll_exercise(adapter, payload)
        assert first["idempotent"] is False
        assert replay == {"card_id": first["card_id"], "idempotent": True}
        assert database.execute("SELECT COUNT(*) FROM cards WHERE study_exercise_id=?",
                                (created["id"],)).fetchone()[0] == 1
        assert database.execute("SELECT status FROM study_exercises WHERE id=?",
                                (created["id"],)).fetchone()[0] == "published"
    assert refreshed == [date.today().isoformat()]


def test_postgres_exercise_train_now_dispatches_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(study_routes, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    response = TestClient(main.app).post(
        "/api/studies/study-1/exercises/exercise-1/train-now",
        headers={"Idempotency-Key": "train-now-1"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [
        ("studies.exercises.train_now",
         {"study_id": "study-1", "exercise_id": "exercise-1"}, "train-now-1"),
    ]


def test_postgres_exercise_revision_dispatch_preserves_optional_field_presence(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(study_routes, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    response = TestClient(main.app).put(
        "/api/studies/study-1/exercises/exercise-1",
        json={"expected_revision": 1, "specification": {
            "type": "explanation", "prompt": "Explain the idea", "rubric": "Mention the threat",
        }, "source": {}},
        headers={"Idempotency-Key": "revision-1"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [
        ("studies.exercises.revise", {"study_id": "study-1", "exercise_id": "exercise-1",
                                      "revision": {"expected_revision": 1, "specification": {
                                          "type": "explanation", "prompt": "Explain the idea",
                                          "rubric": "Mention the threat",
                                      }, "source": {}}}, "revision-1"),
    ]


def test_postgres_exercise_revision_keeps_metadata_schedule_and_resets_material_card(monkeypatch, tmp_path):
    import chess
    from app import study_commands

    database_path = tmp_path / "exercise-revision.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE studies(id TEXT PRIMARY KEY);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,position_id TEXT,
                current_revision INTEGER,updated_at TEXT,source_json TEXT,sibling_group TEXT,
                point_value REAL);
            CREATE TABLE study_positions(id TEXT PRIMARY KEY,fen TEXT);
            CREATE TABLE study_exercise_revisions(exercise_id TEXT,revision INTEGER,
                specification_json TEXT,digest TEXT,created_at TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,repertoire_id TEXT,kind TEXT,start_fen TEXT,
                moves_json TEXT,state TEXT,due_date TEXT,content_type TEXT,study_exercise_id TEXT,
                revision INTEGER,archived INTEGER DEFAULT 0);
            CREATE TABLE daily_queue(card_id TEXT,status TEXT);
            INSERT INTO studies VALUES('study');
            INSERT INTO study_exercises VALUES('exercise','study','position',1,NULL,
                '{"tag":"kept"}',NULL,NULL);
        """)
        database.execute("INSERT INTO study_positions VALUES('position',?)", (chess.STARTING_FEN,))
        previous = {"type": "explanation", "prompt": "Original", "rubric": "Identify the idea",
                    "hint": "", "explanation": "Old notes", "further_analysis": ""}
        database.execute("INSERT INTO study_exercise_revisions VALUES('exercise',1,?,'digest',NULL)",
                         (json.dumps(previous),))
        database.execute("""INSERT INTO cards(id,kind,start_fen,moves_json,state,due_date,
                            content_type,study_exercise_id,revision)
                            VALUES('card','exercise',?,'[]','learning','2026-09-27',
                            'study_exercise','exercise',1)""", (chess.STARTING_FEN,))
        database.execute("INSERT INTO daily_queue VALUES('card','queued')")

    class NativeSqlite:
        def __init__(self, database):
            self.database = database
            self.statements = []

        def execute(self, statement, parameters=()):
            self.statements.append(statement)
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

    refreshed = []
    monkeypatch.setattr(study_commands, "request_queue_refresh_in_transaction",
                        lambda _database, queue_date: refreshed.append(queue_date))
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        metadata_result = study_commands.revise_exercise(adapter, {
            "study_id": "study", "exercise_id": "exercise", "revision": {
                "expected_revision": 1,
                "specification": {"type": "explanation", "prompt": "Original",
                                  "rubric": "Identify the idea", "explanation": "New notes"},
                "source": {},
            },
        })
        assert metadata_result == {"id": "exercise", "revision": 2,
                                   "material": False, "schedule_reset": False}
        assert adapter.statements[:2] == [
            "SELECT id FROM studies WHERE id=? FOR UPDATE",
            "SELECT * FROM study_exercises WHERE id=? FOR UPDATE",
        ]
        assert tuple(database.execute(
            "SELECT revision,archived FROM cards WHERE id='card'",
        ).fetchone()) == (2, 0)
        assert database.execute("SELECT source_json FROM study_exercises").fetchone()[0] == "{}"
        assert refreshed == []
        material_result = study_commands.revise_exercise(adapter, {
            "study_id": "study", "exercise_id": "exercise", "revision": {
                "expected_revision": 2,
                "specification": {"type": "explanation", "prompt": "Updated prompt",
                                  "rubric": "Identify the idea", "explanation": "New notes"},
            },
        })
        assert material_result == {"id": "exercise", "revision": 3,
                                   "material": True, "schedule_reset": True}
        assert database.execute("SELECT archived FROM cards WHERE id='card'").fetchone()[0] == 1
        assert database.execute("SELECT status FROM daily_queue").fetchone()[0] == "blocked"
        assert database.execute("SELECT COUNT(*) FROM cards WHERE archived=0 AND revision=3").fetchone()[0] == 1
        assert database.execute("SELECT source_json FROM study_exercises").fetchone()[0] == "{}"
    assert refreshed == [date.today().isoformat()]


def test_postgres_study_attempt_routes_dispatch_idempotent_foreground_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(study_routes, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    client = TestClient(main.app)
    submitted = client.post(
        "/api/studies/study-1/exercises/exercise-1/attempts",
        json={"attempt_id": "attempt-1", "revision": 1,
              "answer": {"type": "explanation", "text": "My idea"}, "context": "practice"},
    )
    assessed = client.post(
        "/api/studies/study-1/exercises/exercise-1/attempts/attempt-1/self-assess",
        json={"rating": "correct"},
    )
    assert submitted.status_code == 200, submitted.text
    assert assessed.status_code == 200, assessed.text
    assert dispatched[0][0] == "studies.attempts.submit"
    assert dispatched[0][1]["attempt"]["attempt_id"] == "attempt-1"
    assert dispatched[0][2] == "attempt-1"
    assert dispatched[1] == (
        "studies.attempts.self_assess",
        {"study_id": "study-1", "exercise_id": "exercise-1", "attempt_id": "attempt-1",
         "assessment": {"rating": "correct"}},
        "attempt-1:self-assess",
    )


def test_postgres_study_practice_attempt_replays_and_self_assesses_once(monkeypatch, tmp_path):
    import chess
    from app import study_attempt_commands

    database_path = tmp_path / "study-practice-attempt.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT,
                current_revision INTEGER,position_id TEXT);
            CREATE TABLE study_positions(id TEXT PRIMARY KEY,fen TEXT);
            CREATE TABLE study_exercise_revisions(exercise_id TEXT,revision INTEGER,
                specification_json TEXT);
            CREATE TABLE study_attempts(id TEXT PRIMARY KEY,exercise_id TEXT,revision INTEGER,
                card_id TEXT,queue_entry_id INTEGER,cycle INTEGER,context TEXT,
                answer_json TEXT,answer_hash TEXT,assessment_json TEXT,assessment_method TEXT,
                grader_version INTEGER,hint_seen INTEGER,solution_seen_before_answer INTEGER,
                started_at TEXT,committed_at TEXT,finalized_at TEXT,result_json TEXT);
            INSERT INTO study_exercises VALUES('exercise','study','published',1,'position');
        """)
        database.execute("INSERT INTO study_positions VALUES('position',?)", (chess.STARTING_FEN,))
        specification = {"type": "explanation", "prompt": "Explain the plan",
                         "rubric": "Mention development"}
        database.execute("INSERT INTO study_exercise_revisions VALUES('exercise',1,?)",
                         (json.dumps(specification),))

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

        def execute_native(self, statement, parameters=()):
            assert statement.startswith("SELECT pg_advisory_xact_lock")
            return self.database.execute("SELECT 1")

    refreshed = []
    monkeypatch.setattr(study_attempt_commands, "request_queue_refresh_in_transaction",
                        lambda _database, queue_date: refreshed.append(queue_date))
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        payload = {"study_id": "study", "exercise_id": "exercise", "attempt": {
            "attempt_id": "attempt", "revision": 1,
            "answer": {"type": "explanation", "text": "Develop pieces"}, "context": "practice",
        }}
        pending = study_attempt_commands.submit_study_attempt(adapter, payload)
        assert pending["pending_self_assessment"] is True
        assert study_attempt_commands.submit_study_attempt(adapter, payload) == pending
        assessment_payload = {"study_id": "study", "exercise_id": "exercise",
                              "attempt_id": "attempt", "assessment": {"rating": "correct"}}
        result = study_attempt_commands.self_assess_study_attempt(adapter, assessment_payload)
        assert result["rating"] == "correct" and result["persisted"] is True
        assert study_attempt_commands.self_assess_study_attempt(adapter, assessment_payload) == result
        assert study_attempt_commands.submit_study_attempt(adapter, payload) == result
        assert database.execute("SELECT COUNT(*) FROM study_attempts").fetchone()[0] == 1
        with pytest.raises(HTTPException) as reused_id:
            study_attempt_commands.submit_study_attempt(
                adapter, {**payload, "exercise_id": "another-exercise"},
            )
        assert reused_id.value.status_code == 409
        with pytest.raises(HTTPException) as wrong_study:
            study_attempt_commands.submit_study_attempt(
                adapter, {**payload, "study_id": "another-study"},
            )
        assert wrong_study.value.status_code == 409
        with pytest.raises(HTTPException) as conflict:
            study_attempt_commands.self_assess_study_attempt(adapter, {
                **assessment_payload, "assessment": {"rating": "again"},
            })
        assert conflict.value.status_code == 409
    assert refreshed == [date.today().isoformat()]


def test_postgres_study_review_attempt_rejects_second_answer_for_one_queue_entry(tmp_path):
    import chess
    from app import study_attempt_commands

    database_path = tmp_path / "study-review-attempt.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT,
                current_revision INTEGER,position_id TEXT);
            CREATE TABLE study_positions(id TEXT PRIMARY KEY,fen TEXT);
            CREATE TABLE study_exercise_revisions(exercise_id TEXT,revision INTEGER,
                specification_json TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,study_exercise_id TEXT,archived INTEGER,
                pending_validation INTEGER,revision INTEGER);
            CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,card_id TEXT,cycle INTEGER,
                status TEXT,queue_date TEXT);
            CREATE TABLE study_attempts(id TEXT PRIMARY KEY,exercise_id TEXT,revision INTEGER,
                card_id TEXT,queue_entry_id INTEGER UNIQUE,cycle INTEGER,context TEXT,
                answer_json TEXT,answer_hash TEXT,assessment_json TEXT,assessment_method TEXT,
                grader_version INTEGER,hint_seen INTEGER,solution_seen_before_answer INTEGER,
                started_at TEXT,committed_at TEXT,finalized_at TEXT,result_json TEXT);
            INSERT INTO study_exercises VALUES('exercise','study','published',1,'position');
            INSERT INTO cards VALUES('card','exercise',0,0,1);
            INSERT INTO daily_queue VALUES(7,'card',0,'queued','2026-09-27');
        """)
        database.execute("INSERT INTO study_positions VALUES('position',?)", (chess.STARTING_FEN,))
        database.execute("INSERT INTO study_exercise_revisions VALUES('exercise',1,?)",
                         (json.dumps({"type": "explanation", "prompt": "Explain",
                                      "rubric": "Development"}),))

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

        def execute_native(self, statement, parameters=()):
            assert statement.startswith("SELECT pg_advisory_xact_lock")
            return self.database.execute("SELECT 1")

    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        answer = {"revision": 1, "answer": {"type": "explanation", "text": "Develop"},
                  "context": "review", "card_id": "card", "queue_entry_id": 7, "queue_cycle": 0}
        first = study_attempt_commands.submit_study_attempt(adapter, {
            "study_id": "study", "exercise_id": "exercise",
            "attempt": {"attempt_id": "first", **answer},
        })
        assert first["pending_self_assessment"] is True
        with pytest.raises(HTTPException) as conflict:
            study_attempt_commands.submit_study_attempt(adapter, {
                "study_id": "study", "exercise_id": "exercise",
                "attempt": {"attempt_id": "second", **answer},
            })
        assert conflict.value.status_code == 409
        assert database.execute("SELECT COUNT(*) FROM study_attempts").fetchone()[0] == 1


def test_postgres_exercise_train_now_replay_keeps_one_explicit_queue_entry(monkeypatch, tmp_path):
    from app import study_commands

    database_path = tmp_path / "train-now.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE studies(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,study_exercise_id TEXT,archived INTEGER,
                               pending_validation INTEGER);
            CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,queue_date TEXT,card_id TEXT,
                cycle INTEGER,position INTEGER,admission_kind TEXT,card_bucket TEXT,
                status TEXT DEFAULT 'queued');
            INSERT INTO studies VALUES('study',0);
            INSERT INTO study_exercises VALUES('exercise','study','published');
            INSERT INTO cards VALUES('card','exercise',0,0);
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute(self, statement, parameters=()):
            return self.database.execute(statement.replace("FOR UPDATE", ""), parameters)

        def execute_native(self, statement, parameters=()):
            assert statement.startswith("SELECT pg_advisory_xact_lock")
            return self.database.execute("SELECT 1")

    refreshed = []
    monkeypatch.setattr(study_commands, "request_queue_refresh_in_transaction",
                        lambda database, queue_date: refreshed.append(queue_date))
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        adapter = NativeSqlite(database)
        payload = {"study_id": "study", "exercise_id": "exercise"}
        first = study_commands.train_exercise_now(adapter, payload)
        replay = study_commands.train_exercise_now(adapter, payload)
        assert first["idempotent"] is False
        assert replay == {"queue_entry_id": first["queue_entry_id"], "idempotent": True}
        queued = database.execute("SELECT card_id,cycle,position,admission_kind FROM daily_queue").fetchall()
        assert [tuple(entry) for entry in queued] == [("card", 0, 0, "explicit")]
    assert refreshed == [date.today().isoformat()]


def test_postgres_queue_contention_yields_without_spending_retry_or_replaying_stale_lease(monkeypatch, tmp_path):
    from psycopg.errors import LockNotAvailable, TransactionTimeout
    from app import tasks
    from app.services import durable_tasks

    database_path = tmp_path / "queue-contention.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE background_tasks(id TEXT PRIMARY KEY,generation INTEGER,state TEXT,
                phase TEXT,attempt_count INTEGER,next_attempt_at TEXT,lease_token TEXT,
                lease_expires_at TEXT,last_error TEXT,updated_at TEXT);
            CREATE TABLE background_task_events(id INTEGER PRIMARY KEY,task_id TEXT,
                generation INTEGER,event TEXT,phase TEXT,detail TEXT,created_at TEXT);
            INSERT INTO background_tasks VALUES('queue-job',2,'leased','claimed',1,NULL,
                'current',NULL,NULL,NULL);
        """)

    def write_background(operation, *, label):
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            return operation(database)

    monkeypatch.setattr(durable_tasks, "submit_background_write", write_background)
    claimed = {"kind": "daily_queue", "id": "queue-job", "generation": 2,
               "lease_token": "current", "payload": {"queue_date": "2026-09-27"}}
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *_args: nullcontext())
    monkeypatch.setattr(tasks, "fail_task",
                        lambda *_args: pytest.fail("Expected contention must not spend a retry"))
    monkeypatch.setattr(tasks, "defer_task_for_contention", durable_tasks.defer_task_for_contention)
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: None)
    for expected_contention in (LockNotAvailable, TransactionTimeout):
        with sqlite3.connect(database_path) as database:
            database.execute(
                "UPDATE background_tasks SET state='leased',phase='claimed',"
                "attempt_count=1,lease_token='current' WHERE id='queue-job'",
            )
        monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice",
                            lambda _task: (_ for _ in ()).throw(expected_contention("busy")))
        assert tasks.execute_background_slice.run(claimed) is True
    with sqlite3.connect(database_path) as database:
        assert database.execute(
            "SELECT state,phase,attempt_count,lease_token FROM background_tasks",
        ).fetchone() == ("retrying", "yielded", 0, None)
        assert database.execute(
            "SELECT COUNT(*) FROM background_task_events WHERE event='yielded'",
        ).fetchone()[0] == 2
    assert not durable_tasks.defer_task_for_contention("queue-job", 2, "current")


def test_postgres_review_reserves_card_then_queue_position_before_reordering(monkeypatch):
    from app import main, review_commands

    observed = []

    class RecordingDatabase:
        def execute(self, statement, parameters=()):
            observed.append(("card_lock", statement, parameters))
            return self

    monkeypatch.setattr(review_commands, "lock_queue_date_for_position",
                        lambda database, queue_date: observed.append(("queue_lock", queue_date)))
    monkeypatch.setattr(main, "_apply_review",
                        lambda card_id, request, *, database:
                        observed.append(("review", card_id, request.outcome)) or {"persisted": True})
    assert review_commands.submit_review(
        RecordingDatabase(), {"card_id": "card-1", "review": {"outcome": "again"}},
    ) == {"persisted": True}
    assert observed[0] == ("card_lock", "SELECT id FROM cards WHERE id=? FOR UPDATE", ("card-1",))
    assert observed[1] == ("queue_lock", date.today().isoformat())
    assert observed[2] == ("review", "card-1", "again")


def test_postgres_cutover_queue_fail_and_bury_dispatch_foreground_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"accepted": name}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", record_command)
    client = TestClient(main.app)
    for action, command_name in (("fail", "queue.attempt_failed"), ("bury", "queue.bury")):
        response = client.post(
            f"/api/queue/entries/42/{action}",
            headers={"Idempotency-Key": f"queue-42-{action}"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": command_name}
    assert dispatched == [
        ("queue.attempt_failed", {"entry_id": 42}, "queue-42-fail"),
        ("queue.bury", {"entry_id": 42}, "queue-42-bury"),
    ]


def test_postgres_cutover_review_route_dispatches_idempotent_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"review_id": 19, "persisted": True}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", record_command)
    response = TestClient(main.app).post(
        "/api/cards/card-1/review",
        json={"outcome": "correct", "queue_entry_id": 42},
        headers={"Idempotency-Key": "review-card-1-42"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"review_id": 19, "persisted": True}
    assert dispatched[0][0] == "cards.review"
    assert dispatched[0][1]["card_id"] == "card-1"
    assert dispatched[0][1]["review"]["queue_entry_id"] == 42
    assert dispatched[0][2] == "review-card-1-42"


def test_postgres_defense_answers_dispatch_atomic_foreground_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "get_settings",
                        lambda: SimpleNamespace(light_first_interval_days=2))
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"accepted": name})
    client = TestClient(main.app)
    recognition = client.post("/api/defense-exercises/candidate-1/recognition", json={
        "attempt_id": "answer-1", "exercise_revision": 2, "rubric_version": 3,
        "queue_entry_id": 42, "no_concrete_threat": True, "consequence": "none",
    })
    attempt = client.post("/api/defense-exercises/candidate-1/attempt", json={
        "attempt_id": "answer-1", "exercise_revision": 2, "queue_entry_id": 42,
        "move_uci": "e2e4", "recognition_attempt_id": "answer-1",
    })
    assert recognition.status_code == 200, recognition.text
    assert attempt.status_code == 200, attempt.text
    assert dispatched[0][0] == "defense.recognition.submit"
    assert dispatched[0][2] == "defense-recognition:answer-1"
    assert dispatched[1][0] == "defense.attempt.submit"
    assert dispatched[1][1]["light_first_interval_days"] == 2
    assert dispatched[1][2] == "defense-attempt:answer-1"


def test_postgres_defense_stale_answer_preserves_conflict_status(monkeypatch):
    from app import defense_commands

    monkeypatch.setattr(defense_commands, "submit_defense_attempt",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            ValueError("Exercise revision changed")))
    with pytest.raises(HTTPException) as error:
        defense_commands.submit_attempt(None, {
            "candidate_id": "candidate-1", "light_first_interval_days": 7,
            "request": {"attempt_id": "answer-1", "exercise_revision": 2,
                        "queue_entry_id": 42, "move_uci": "e2e4"},
        })
    assert error.value.status_code == 409


def test_postgres_tactic_attempt_dispatches_validated_foreground_command(monkeypatch):
    from fastapi.testclient import TestClient
    import chess
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "puzzle_membership", lambda: {})
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"mode": "light"})
    response = TestClient(main.app).post("/api/tactics/attempt", json={
        "attempt_id": "tactic-1", "puzzle_id": "puzzle-1", "deck_id": "deck-1",
        "correct": True, "clean": True, "source_fen": chess.STARTING_FEN,
        "moves": ["e2e4", "e7e5"],
    })
    assert response.status_code == 200, response.text
    assert dispatched[0][0] == "tactics.attempt.submit"
    assert dispatched[0][1]["pack_id"] == "deck-1"
    assert dispatched[0][1]["solution"] == ["e7e5"]
    assert dispatched[0][2] == "tactic-attempt:tactic-1"
    anonymous_request = {
        "puzzle_id": "puzzle-2", "deck_id": "deck-1", "correct": False,
        "clean": False, "source_fen": chess.STARTING_FEN,
        "moves": ["e2e4", "e7e5"],
    }
    assert TestClient(main.app).post("/api/tactics/attempt", json=anonymous_request).status_code == 422
    first = TestClient(main.app).post(
        "/api/tactics/attempt", json=anonymous_request,
        headers={"Idempotency-Key": "provider-tactic-2"},
    )
    second = TestClient(main.app).post(
        "/api/tactics/attempt", json=anonymous_request,
        headers={"Idempotency-Key": "provider-tactic-2"},
    )
    assert first.status_code == second.status_code == 200
    assert dispatched[-1][1]["request"]["attempt_id"] == dispatched[-2][1]["request"]["attempt_id"]
    assert dispatched[-1][2] == dispatched[-2][2] == "provider-tactic-2"


def test_postgres_tactic_activation_dispatches_and_reads_committed_catalog(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"updated": True})
    monkeypatch.setattr(main, "tactics_catalog", lambda: {"packs": [{"id": "pack-1", "active": True}]})
    response = TestClient(main.app).put(
        "/api/tactics/activation", json={"pack_ids": ["pack-1"], "active": True},
        headers={"Idempotency-Key": "activate-pack-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["packs"][0]["active"]
    assert dispatched == [("tactics.activation.set",
                           {"pack_ids": ["pack-1"], "active": True}, "activate-pack-1")]


def test_postgres_cutover_teaching_state_dispatches_and_replays_saved_timestamp(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.teaching_commands import record_teaching_state

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"cardId": payload["card_id"], "revision": 2, "ply": 3,
                "taughtAt": "2026-09-27T12:00:00+00:00"}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", record_command)
    response = TestClient(main.app).post(
        "/api/cards/card-1/teaching",
        json={"revision": 2, "ply": 3},
        headers={"Idempotency-Key": "teaching-card-1-2-3"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("cards.teaching.record", {
        "card_id": "card-1", "teaching_state": {"revision": 2, "ply": 3},
    }, "teaching-card-1-2-3")]

    class QueryResult:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class ExistingTeachingState:
        def __init__(self):
            self.queries = []

        def execute(self, statement, parameters):
            self.queries.append((statement, parameters))
            if statement.startswith("SELECT 1 FROM cards"):
                return QueryResult((1,))
            if statement.startswith("INSERT INTO teaching_states"):
                return QueryResult(None)
            return QueryResult(("2026-09-26T10:00:00+00:00",))

    existing_state = ExistingTeachingState()
    result = record_teaching_state(existing_state, dispatched[0][1])
    assert result == {"cardId": "card-1", "revision": 2, "ply": 3,
                      "taughtAt": "2026-09-26T10:00:00+00:00"}
    assert "ON CONFLICT(card_id,revision,ply) DO NOTHING" in existing_state.queries[1][0]


def test_postgres_cutover_main_repertoire_selection_uses_one_locked_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.repertoire_commands import select_main_repertoire

    dispatched = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"id": payload["repertoire_id"], "is_main": True}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", record_command)
    response = TestClient(main.app).put(
        "/api/repertoires/opening-1/main",
        headers={"Idempotency-Key": "main-opening-1"},
    )
    assert response.status_code == 200, response.text
    assert dispatched == [("repertoires.main.select", {"repertoire_id": "opening-1"}, "main-opening-1")]

    class QueryResult:
        def __init__(self, rows):
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

    class LockedRepertoires:
        def __init__(self):
            self.queries = []

        def execute_native(self, statement, parameters):
            self.queries.append((statement, parameters))
            return QueryResult([])

        def execute(self, statement, parameters):
            self.queries.append((statement, parameters))
            return QueryResult([("opening-1",), ("opening-2",)])

    database = LockedRepertoires()
    assert select_main_repertoire(database, {"repertoire_id": "opening-1"}) == {
        "id": "opening-1", "is_main": True,
    }
    assert database.queries[0][0].startswith("SELECT pg_advisory_xact_lock")
    assert database.queries[1][0].endswith("ORDER BY id FOR UPDATE")
    assert database.queries[2][0].startswith("UPDATE repertoires SET is_main=")


def test_postgres_cutover_background_slice_restarts_only_with_current_lease(monkeypatch):
    from app.services import durable_tasks

    active_task = {"id": "task-1", "generation": 3, "lease_token": "lease-current"}
    events: list[tuple] = []

    class Cursor:
        def __init__(self, *, row=None, rowcount=0):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class Database:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement, parameters):
            self.statements.append(statement)
            if statement.startswith("SELECT generation"):
                return Cursor(row={"generation": 3, "lease_token": "lease-current", "state": "leased"})
            return Cursor(rowcount=int(parameters[-2:] == (3, "lease-current")))

    monkeypatch.setattr(durable_tasks, "_record_event", lambda *arguments: events.append(arguments))
    database = Database()
    assert durable_tasks.lock_current_slice(database, active_task)
    assert not durable_tasks.lock_current_slice(database, {**active_task, "generation": 2})
    assert durable_tasks.advance_task_slice_in_transaction(
        database, active_task, next_phase="seed", next_payload={"_queue_phase": "seed"},
    )
    assert not durable_tasks.advance_task_slice_in_transaction(
        database, {**active_task, "lease_token": "stale"},
        next_phase="seed", next_payload={"_queue_phase": "seed"},
    )
    assert "attempt_count=0" in database.statements[-1]
    assert len(events) == 1


def test_postgres_cutover_translates_placeholders_and_rejects_runtime_pragma():
    assert postgres_sql("SELECT id FROM cards WHERE id=?") == "SELECT id FROM cards WHERE id = %s"
    assert postgres_sql("INSERT OR IGNORE INTO settings(id) VALUES(?)").endswith(
        "ON CONFLICT DO NOTHING"
    )
    with pytest.raises(ValueError, match="SQLite PRAGMA"):
        postgres_sql("PRAGMA foreign_keys = ON")


def test_postgres_cutover_conflict_target_does_not_include_null_ordering():
    statement = postgres_sql(
        "INSERT INTO queue_projections(queue_date,state) VALUES(?,'refreshing') "
        "ON CONFLICT(queue_date) DO UPDATE SET state=excluded.state"
    )
    assert "ON CONFLICT(queue_date) DO UPDATE" in statement
    assert "NULLS FIRST" not in statement


def test_postgres_cutover_card_copy_uses_backend_column_catalog(monkeypatch):
    from app import database as database_module

    with sqlite3.connect(":memory:") as sqlite_database:
        sqlite_database.execute("CREATE TABLE cards(id TEXT PRIMARY KEY, due_date TEXT)")
        assert database_module.card_columns(sqlite_database) == ["id", "due_date"]

    class PgDatabase:
        def execute(self, statement, parameters):
            assert "information_schema.columns" in statement
            assert parameters == ("cards",)
            return [("id",), ("due_date",)]

    monkeypatch.setattr(database_module.postgres_store, "configured", lambda: True)
    assert database_module.card_columns(PgDatabase()) == ["id", "due_date"]


def test_postgres_cutover_row_supports_mapping_and_sqlite_value_iteration():
    row = TempoRow(("total", "unread_count"), (3, 2))
    assert dict(row) == {"total": 3, "unread_count": 2}
    assert tuple(row) == (3, 2)
    assert row[0] == row["total"] == 3


def test_postgres_cutover_idempotency_digest_is_payload_order_independent():
    first = request_digest("review.submit", {"card_id": "a", "outcome": "again"})
    assert first == request_digest("review.submit", {"outcome": "again", "card_id": "a"})
    assert first != request_digest("review.submit", {"card_id": "a", "outcome": "correct"})


def test_postgres_cutover_ambiguous_timeout_stays_pending(monkeypatch):
    class PendingTask:
        state = "PENDING"

        def get(self, **_):
            raise CeleryTimeout()

    monkeypatch.setattr(command_dispatch.celery_app, "send_task", lambda *_, **__: PendingTask())
    monkeypatch.setattr(
        command_dispatch, "read_operation",
        lambda operation_id, **_: {"operation_id": operation_id, "state": "pending"},
    )
    response = command_dispatch.dispatch_command(
        "review.submit", {"card_id": "a"}, idempotency_key="review-a"
    )
    assert response.status_code == 202
    assert response.headers["location"] == "/api/operations/review-a"
    assert b'"state":"pending"' in response.body


def test_postgres_cutover_result_store_outage_checks_receipt_before_reporting_pending(monkeypatch):
    class ResultStoreUnavailableTask:
        state = "PENDING"

        def get(self, **_):
            from redis import ConnectionError as RedisConnectionError
            raise RedisConnectionError("result store stopped")

    monkeypatch.setattr(command_dispatch.celery_app, "send_task",
                        lambda *_, **__: ResultStoreUnavailableTask())
    monkeypatch.setattr(command_dispatch, "read_operation",
                        lambda operation_id, **_: {"operation_id": operation_id, "state": "pending"})
    response = command_dispatch.dispatch_command(
        "review.submit", {"card_id": "a"}, idempotency_key="review-result-store-outage",
    )
    assert response.status_code == 202
    assert b'review-result-store-outage' in response.body


def test_postgres_cutover_broker_failure_reports_actionable_error(monkeypatch):
    def unavailable(*_, **__):
        raise BrokerUnavailable("broker stopped")

    monkeypatch.setattr(command_dispatch.celery_app, "send_task", unavailable)
    with pytest.raises(HTTPException) as error:
        command_dispatch.dispatch_command(
            "review.submit", {"card_id": "a"}, idempotency_key="review-a"
        )
    assert error.value.status_code == 503
    assert "same Idempotency-Key" in error.value.detail


def test_postgres_cutover_reused_key_cannot_return_another_commands_receipt(monkeypatch):
    class CompletedTask:
        state = "SUCCESS"

        def get(self, **_):
            return None

    monkeypatch.setattr(command_dispatch.celery_app, "send_task", lambda *_, **__: CompletedTask())

    def conflicting_receipt(*_, **__):
        raise CommandConflict("Operation ID was already used for another request")

    monkeypatch.setattr(command_dispatch, "read_operation", conflicting_receipt)
    with pytest.raises(HTTPException) as error:
        command_dispatch.dispatch_command(
            "studies.create", {"title": "different"}, idempotency_key="reused-key"
        )
    assert error.value.status_code == 409


def test_postgres_cutover_foreground_admission_blocks_background_slice(monkeypatch):
    class AtomicRedis:
        def __init__(self):
            self.lock = threading.Lock()
            self.foreground: set[str] = set()
            self.background: set[str] = set()

        def eval(self, script, _key_count, *_arguments):
            token = _arguments[-2]
            with self.lock:
                if script == redis_admission_gate._REGISTER_FOREGROUND:
                    self.foreground.add(token)
                    return 1
                if self.foreground:
                    return 0
                self.background.add(token)
                return 1

        def zrem(self, key, token):
            with self.lock:
                (self.foreground if key.endswith("foreground") else self.background).discard(token)

    shared_redis = AtomicRedis()
    monkeypatch.setattr(redis_admission_gate, "client", lambda: shared_redis)
    background_started = threading.Event()

    def enter_background():
        with redis_admission_gate.background_lease():
            background_started.set()

    with redis_admission_gate.foreground_lease():
        worker = threading.Thread(target=enter_background)
        worker.start()
        assert not background_started.wait(0.05)
    assert background_started.wait(1)
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_postgres_cutover_background_reads_respect_foreground_admission(monkeypatch):
    from app import database

    connection_opened = threading.Event()
    read_finished = threading.Event()

    @contextmanager
    def test_postgres_connection(*, read_only, background):
        assert read_only and background
        connection_opened.set()
        yield object()

    monkeypatch.setattr(database.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(database.postgres_store, "connection", test_postgres_connection)
    monkeypatch.setattr(database.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)

    def read_in_background():
        with database.background_read_connection():
            read_finished.set()

    with database.activity_gate.foreground():
        worker = threading.Thread(target=read_in_background)
        worker.start()
        assert not connection_opened.wait(0.05)
    assert read_finished.wait(1)
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_postgres_cutover_background_claim_orders_supported_kinds_by_priority(monkeypatch):
    from app.services import durable_tasks

    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE background_tasks(
                id TEXT PRIMARY KEY,kind TEXT,deduplication_key TEXT,
                generation INTEGER,priority INTEGER,state TEXT,phase TEXT,
                payload_version INTEGER,payload_json TEXT,attempt_count INTEGER,
                max_attempts INTEGER,next_attempt_at TEXT,lease_token TEXT,
                lease_expires_at TEXT,last_error TEXT,created_at TEXT,
                started_at TEXT,completed_at TEXT,updated_at TEXT,
                UNIQUE(kind,deduplication_key)
            );
            CREATE TABLE background_task_events(
                id INTEGER PRIMARY KEY,task_id TEXT,generation INTEGER,event TEXT,
                phase TEXT,detail TEXT,created_at TEXT
            );
            CREATE TABLE background_activity(
                source TEXT,work_id TEXT,paused INTEGER DEFAULT 0,
                promoted INTEGER DEFAULT 0
            );
        """)
        priorities = (
            ("unported_analysis", 1), ("priority_retention", 200),
            ("repertoire_game_refresh", 90), ("defensive_threat_report_audit", 135),
        )
        for task_kind, priority in priorities:
            durable_tasks.enqueue_task_in_transaction(
                database, task_kind, task_kind, {}, priority=priority,
            )
        monkeypatch.setattr(
            durable_tasks, "submit_background_write",
            lambda operation, *, label: operation(database),
        )
        allowed_kinds = (
            "priority_retention", "repertoire_game_refresh",
            "defensive_threat_report_audit",
        )
        claimed = [durable_tasks.claim_task(allowed_kinds=allowed_kinds) for _ in range(4)]
        assert [task["kind"] if task else None for task in claimed] == [
            "repertoire_game_refresh", "defensive_threat_report_audit",
            "priority_retention", None,
        ]


def test_postgres_cutover_rubric_audit_uses_boolean_case_parameter():
    from app.services.threat_training import _persist_audited_rubric_validation

    @dataclass
    class Validation:
        state: str = "engine_supported"
        diagnostic: str = "verified"

    saved_parameters: list[tuple] = []

    class Database:
        def execute(self, statement, parameters):
            assert "approved_at=CASE WHEN ?" in statement
            saved_parameters.append(parameters)

    database = Database()
    _persist_audited_rubric_validation(database, "candidate-1", Validation(), True)
    _persist_audited_rubric_validation(database, "candidate-2", Validation(), False)
    assert saved_parameters[0][3] is True
    assert saved_parameters[1][3] is False


def test_postgres_cutover_rubric_audit_yields_and_discards_stale_replay(monkeypatch):
    from types import SimpleNamespace
    from app import tasks
    from app.services import threat_training

    foreground_finished = threading.Event()
    read_opened = threading.Event()
    published_candidates: list[str] = []
    advanced_cursors: list[str] = []
    current_lease = {"token": "active"}
    claimed_task = {
        "kind": "defensive_rubric_audit", "id": "rubric-task",
        "generation": 3, "lease_token": "active", "payload": {"cursor": ""},
    }
    candidate = {
        "id": "candidate-1", "evidence_json": '{"anchor":{},"seed":{}}',
        "policy_json": "{}", "source_fingerprint": "source-1",
        "exercise_revision": 2, "card_id": None,
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(())

    class ReadDatabase:
        def execute(self, statement, _parameters):
            return Cursor(candidate if "SELECT candidate.*" in statement else None)

    class WriteDatabase:
        def execute(self, statement, parameters=()):
            if "SELECT source_fingerprint" in statement:
                return Cursor(candidate)
            if "UPDATE threat_training_candidates SET validation_state" in statement:
                assert parameters[3] is True
                published_candidates.append(parameters[-1])
            if statement.startswith("UPDATE background_tasks"):
                advanced_cursors.append(parameters[0])
                current_lease["token"] = "next-generation"
            return Cursor()

    @contextmanager
    def test_read_connection():
        read_opened.set()
        yield ReadDatabase()

    @contextmanager
    def test_write_connection(*, background):
        assert background
        yield WriteDatabase()

    anchor = SimpleNamespace(position=SimpleNamespace(
        start_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        prefix_uci=(), learner_color="white",
    ))
    seed = SimpleNamespace(geometry=SimpleNamespace(
        major=SimpleNamespace(square="a1", piece="rook"),
    ))

    @dataclass
    class Validation:
        state: str = "engine_supported"
        diagnostic: str = "verified"

    monkeypatch.setattr(threat_training.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(threat_training.activity_gate, "wait_for_foreground", foreground_finished.wait)
    monkeypatch.setattr(threat_training, "background_read_connection", test_read_connection)
    monkeypatch.setattr(threat_training, "connection", test_write_connection)
    monkeypatch.setattr(threat_training, "_anchor_from_json", lambda _raw: anchor)
    monkeypatch.setattr(threat_training, "_seed_from_json", lambda _raw: seed)
    monkeypatch.setattr(threat_training, "ThreatPolicy", lambda **_arguments: object())
    monkeypatch.setattr(threat_training, "make_validation_plan", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(threat_training, "validate_threat_anchor", lambda *_args: Validation())
    monkeypatch.setattr(threat_training, "enqueue_defense_admission", lambda *, background: None)
    monkeypatch.setattr(
        threat_training, "lock_current_slice",
        lambda _database, task: task["lease_token"] == current_lease["token"],
    )

    results: list[bool] = []
    worker = threading.Thread(
        target=lambda: results.append(threat_training.execute_defense_rubric_audit_slice(claimed_task)),
    )
    worker.start()
    assert not read_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert results == [True]
    assert published_candidates == ["candidate-1"]
    assert advanced_cursors == ['{"cursor": "candidate-1"}']
    assert threat_training.execute_defense_rubric_audit_slice(claimed_task) is True
    assert published_candidates == ["candidate-1"]

    claimed_filters: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tasks, "claim_task",
        lambda *, allowed_kinds: claimed_filters.append(allowed_kinds) or claimed_task,
    )
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: None)
    assert tasks.poll_background_tasks.run() is True
    assert "defensive_rubric_audit" in claimed_filters[0]


def test_postgres_cutover_priority_retention_locks_bounded_primary_keys(monkeypatch):
    from app.services import priority_retention

    statements: list[str] = []
    deleted: list[tuple[str, int, str]] = []

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
            statements.append(statement)
            if "FROM background_tasks" in statement:
                return Cursor(row={"generation": 1, "lease_token": "lease", "state": "leased"})
            if "FROM repertoire_priority_publications" in statement:
                return Cursor(row={"generation": 3})
            if "FROM repertoire_priority_jobs" in statement:
                return Cursor(row={"generation": 4})
            if "SELECT generation,card_id" in statement:
                return Cursor(rows=[{"generation": 2, "card_id": "stale-card"}])
            return Cursor()

        def executemany(self, statement, parameters):
            statements.append(statement)
            deleted.extend(parameters)

    @contextmanager
    def test_connection(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(priority_retention.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(priority_retention, "connection", test_connection)
    monkeypatch.setattr(priority_retention.activity_gate, "wait_for_foreground", lambda: None)
    assert priority_retention.execute_priority_retention_slice({
        "id": "task", "generation": 1, "lease_token": "lease",
        "payload": {"repertoire_id": "repertoire"},
    }) is False
    assert "FOR UPDATE" in statements[0]
    assert any("FOR UPDATE SKIP LOCKED" in statement for statement in statements)
    assert all("rowid" not in statement for statement in statements)
    assert deleted == [("repertoire", 2, "stale-card")]
    assert priority_retention.execute_priority_retention_slice({
        "id": "task", "generation": 1, "lease_token": "expired-lease",
        "payload": {"repertoire_id": "repertoire"},
    }) is False
    assert deleted == [("repertoire", 2, "stale-card")]


def test_postgres_cutover_queue_repertoire_choices_scan_only_active_cards(monkeypatch):
    from app import main

    statements: list[str] = []

    class EmptyCursor:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class EmptyDatabase:
        def execute(self, statement, _parameters=()):
            statements.append(statement)
            return EmptyCursor()

    @contextmanager
    def test_read_connection():
        yield EmptyDatabase()

    monkeypatch.setattr(main, "read_connection", test_read_connection)
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    assert main._queue_payload()["cards"] == []
    queue_statement = next(statement for statement in statements if "ranked_repertoires" in statement)
    assert queue_statement.count("FROM active_queue queue_card JOIN cards c") == 2


def test_postgres_cutover_queue_unlock_slice_replays_and_advances_without_skips():
    from app.main import _unlock_eligible_opening_cards

    def populated_database():
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,archived INTEGER);
            CREATE TABLE opening_graph_steps(card_id TEXT,parent_card_id TEXT,
                                             repertoire_id TEXT,generation INTEGER);
            CREATE TABLE opening_graph_publications(repertoire_id TEXT,generation INTEGER);
            INSERT INTO opening_graph_publications VALUES('repertoire',1);
        """)
        for card_number in range(129):
            card_id = f"card-{card_number:03}"
            database.execute(
                "INSERT INTO cards VALUES(?,'opening','locked',0)", (card_id,),
            )
            if card_number % 7 == 0:
                database.execute(
                    "INSERT INTO opening_graph_steps VALUES(?,NULL,'repertoire',1)",
                    (card_id,),
                )
        return database

    with populated_database() as complete_database, populated_database() as sliced_database:
        _unlock_eligible_opening_cards(complete_database, "2026-09-27")
        first_cursor = _unlock_eligible_opening_cards(
            sliced_database, "2026-09-27", after_card_id="", batch_size=16,
        )
        assert first_cursor is not None
        # Replaying a committed slice may select a few additional locked cards.
        # The cursor must still advance without skipping any eligible card.
        cursor = _unlock_eligible_opening_cards(
            sliced_database, "2026-09-27", after_card_id="", batch_size=16,
        )
        while cursor is not None:
            cursor = _unlock_eligible_opening_cards(
                sliced_database, "2026-09-27", after_card_id=cursor, batch_size=16,
            )
        complete_states = list(complete_database.execute("SELECT id,state FROM cards ORDER BY id"))
        sliced_states = list(sliced_database.execute("SELECT id,state FROM cards ORDER BY id"))
        assert [tuple(row) for row in sliced_states] == [tuple(row) for row in complete_states]


def test_postgres_queue_refresh_eligibility_slices_yield_and_restart_without_replay(monkeypatch, tmp_path):
    from app import main
    from app.services import postgres_queue_refresh

    database_path = tmp_path / "queue-slices.sqlite"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,archived INTEGER);
            CREATE TABLE opening_graph_steps(card_id TEXT,parent_card_id TEXT,
                                             repertoire_id TEXT,generation INTEGER);
            CREATE TABLE opening_graph_publications(repertoire_id TEXT,generation INTEGER);
            CREATE TABLE background_tasks(id TEXT PRIMARY KEY,generation INTEGER,
                                          lease_token TEXT,state TEXT,phase TEXT,payload_json TEXT);
            INSERT INTO opening_graph_publications VALUES('repertoire',1);
            INSERT INTO background_tasks VALUES('queue-job',1,'lease-first','leased','claimed',
                                                 '{"queue_date":"2026-09-27"}');
        """)
        for card_number in range(65):
            card_id = f"card-{card_number:03}"
            database.execute("INSERT INTO cards VALUES(?,'opening','locked',0)", (card_id,))
            if card_number % 8 == 0:
                database.execute(
                    "INSERT INTO opening_graph_steps VALUES(?,NULL,'repertoire',1)", (card_id,),
                )

    events = []

    def wait_for_foreground():
        events.append("wait")

    @contextmanager
    def section(*, background):
        assert background and events[-1] == "wait"
        events.append("database")
        database = sqlite3.connect(database_path)
        database.row_factory = sqlite3.Row
        try:
            yield database
            database.commit()
        finally:
            database.close()

    def lock_current_slice(database, task):
        row = database.execute(
            "SELECT generation,lease_token,state FROM background_tasks WHERE id=?", (task["id"],),
        ).fetchone()
        return (row["generation"] == task["generation"] and
                row["lease_token"] == task["lease_token"] and row["state"] == "leased")

    def advance_slice(database, task, *, next_phase, next_payload):
        return bool(database.execute(
            "UPDATE background_tasks SET state='queued',phase=?,payload_json=?,lease_token=NULL "
            "WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
            (next_phase, json.dumps(next_payload), task["id"], task["generation"], task["lease_token"]),
        ).rowcount)

    completed_phases = []
    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground", wait_for_foreground)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice", lock_current_slice)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance_slice)
    for phase_name, handler in main._QUEUE_ELIGIBILITY_PHASES[1:]:
        monkeypatch.setattr(main, handler.__name__,
                            lambda database, queue_date, name=phase_name: completed_phases.append(name))

    lease_number = 1
    first_task = {"id": "queue-job", "generation": 1, "lease_token": "lease-first",
                  "payload": {"queue_date": "2026-09-27"}}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(first_task)
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(first_task)
    while True:
        with sqlite3.connect(database_path) as database:
            phase, payload_json = database.execute(
                "SELECT phase,payload_json FROM background_tasks WHERE id='queue-job'",
            ).fetchone()
            if phase == "tactical_introductions":
                break
            lease_number += 1
            lease_token = f"lease-{lease_number}"
            database.execute(
                "UPDATE background_tasks SET state='leased',lease_token=? WHERE id='queue-job'",
                (lease_token,),
            )
        next_task = {"id": "queue-job", "generation": 1, "lease_token": lease_token,
                     "payload": json.loads(payload_json)}
        assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(next_task)
    with sqlite3.connect(database_path) as database:
        unlocked = [row[0] for row in database.execute(
            "SELECT id FROM cards WHERE state='new' ORDER BY id",
        )]
    assert unlocked == [f"card-{number:03}" for number in range(0, 65, 8)]
    assert completed_phases == ["block_opening", "block_defense", "restore_due", "restore_study"]
    assert events[::2] == ["wait"] * (len(events) // 2)
    assert events[1::2] == ["database"] * (len(events) // 2)


def test_postgres_queue_refresh_foreground_request_blocks_new_database_slice(monkeypatch):
    from app import main
    from app.services import postgres_queue_refresh

    opened_database = threading.Event()
    finished = threading.Event()

    @contextmanager
    def section(*, background):
        assert background
        opened_database.set()
        yield object()

    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice", lambda database, task: True)
    monkeypatch.setattr(main, "_unlock_eligible_opening_cards", lambda *args, **kwargs: None)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction",
                        lambda *args, **kwargs: True)
    task = {"id": "queue-job", "generation": 1, "lease_token": "lease",
            "payload": {"queue_date": "2026-09-27"}}

    def run_slice():
        try:
            assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
        finally:
            finished.set()

    with postgres_queue_refresh.activity_gate.foreground():
        worker = threading.Thread(target=run_slice)
        worker.start()
        assert not opened_database.wait(0.05)
    assert finished.wait(2)
    worker.join(timeout=2)
    assert opened_database.is_set()


def test_postgres_queue_opening_reset_phases_are_idempotent_and_preserve_active_cards():
    from app.main import _reset_unintroduced_opening_cards, _reset_stale_opening_introductions

    with sqlite3.connect(":memory:") as database:
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,introduced_at TEXT);
            CREATE TABLE reviews(card_id TEXT);
            CREATE TABLE daily_queue(card_id TEXT,queue_date TEXT);
            INSERT INTO cards VALUES('never','opening','learning',NULL);
            INSERT INTO cards VALUES('stale','opening','learning','2026-09-26');
            INSERT INTO cards VALUES('reviewed','opening','learning','2026-09-26');
            INSERT INTO cards VALUES('queued','opening','learning','2026-09-26');
            INSERT INTO reviews VALUES('reviewed');
            INSERT INTO daily_queue VALUES('queued','2026-09-27');
        """)
        for _ in range(2):
            _reset_unintroduced_opening_cards(database, "2026-09-27")
            _reset_stale_opening_introductions(database, "2026-09-27")
        assert list(database.execute(
            "SELECT id,state,introduced_at FROM cards ORDER BY id",
        )) == [
            ("never", "new", None),
            ("queued", "learning", "2026-09-26"),
            ("reviewed", "learning", "2026-09-26"),
            ("stale", "new", None),
        ]


def test_postgres_queue_unseen_reconciliation_matches_sqlite_and_survives_reordering(monkeypatch, tmp_path):
    from app.main import reconcile_unseen_queue
    from app.services import postgres_queue_refresh

    schema = """
        CREATE TABLE cards(id TEXT PRIMARY KEY,repertoire_id TEXT,content_type TEXT,
                           state TEXT,introduced_at TEXT);
        CREATE TABLE reviews(card_id TEXT);
        CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,queue_date TEXT,card_id TEXT,
                                 status TEXT,admission_kind TEXT,admission_repertoire_id TEXT,
                                 position INTEGER);
        CREATE TABLE settings(id INTEGER PRIMARY KEY,new_cards_per_day INTEGER);
        CREATE TABLE repertoire_integrity_card_blocks(repertoire_id TEXT,card_id TEXT);
        INSERT INTO settings VALUES(1,2);
        INSERT INTO cards VALUES('reviewed','r1','opening','learning','2026-09-27');
        INSERT INTO reviews VALUES('reviewed');
    """
    for queue_id, repertoire_id, admission_kind in (
        (1, "r1", "new"), (2, "r1", "new"), (3, "r1", "new"),
        (4, "r2", "new"), (5, "r2", "new"), (6, "r1", "explicit"),
    ):
        schema += (
            f"INSERT INTO cards VALUES('card-{queue_id}','{repertoire_id}','opening','new',NULL);"
            f"INSERT INTO daily_queue VALUES({queue_id},'2026-09-27','card-{queue_id}',"
            f"'queued','{admission_kind}',NULL,{queue_id});"
        )
    database_path = tmp_path / "sliced-reconciliation.db"
    with sqlite3.connect(database_path) as database:
        database.executescript(schema)
    with sqlite3.connect(":memory:") as reference:
        reference.row_factory = sqlite3.Row
        reference.executescript(schema)
        reconcile_unseen_queue(reference, "2026-09-27", 2)
        expected_queue = list(reference.execute("SELECT id,card_id FROM daily_queue ORDER BY id"))
        expected_cards = list(reference.execute(
            "SELECT id,state,introduced_at FROM cards ORDER BY id",
        ))

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute_native(self, statement, parameters=()):
            parameters = tuple(parameters)
            exclusion = "q.id <> ALL(%s::bigint[])"
            if exclusion in statement:
                processed_ids = parameters[-1]
                statement = statement.replace(
                    exclusion,
                    "q.id NOT IN (" + ",".join("?" for _ in processed_ids) + ")"
                    if processed_ids else "1=1",
                )
                parameters = (*parameters[:-1], *processed_ids)
            statement = statement.replace("%s", "?").replace("FOR UPDATE OF q,c", "")
            return self.database.execute(statement, parameters)

    @contextmanager
    def read_section():
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            yield NativeSqlite(database)

    monkeypatch.setattr(postgres_queue_refresh, "background_read_connection", read_section)
    processed_ids: list[int] = []
    introduced_counts: dict[str, int] = {}
    while True:
        candidate, count = postgres_queue_refresh._prepare_unseen_reconciliation(
            "2026-09-27", processed_ids, introduced_counts,
        )
        if candidate is None:
            break
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            kept = postgres_queue_refresh._reconcile_one_unseen_entry(
                NativeSqlite(database), "2026-09-27", candidate, count,
            )
        processed_ids.append(candidate["id"])
        introduced_counts[candidate["repertoire_id"]] = count + int(kept)
        if len(processed_ids) == 1:
            # A foreground bury/reorder can move an unprocessed row before the
            # prior position; the durable ID set must still visit it.
            with sqlite3.connect(database_path) as database:
                database.execute("UPDATE daily_queue SET position=0 WHERE id=5")
    with sqlite3.connect(database_path) as database:
        assert list(database.execute("SELECT id,card_id FROM daily_queue ORDER BY id")) == [
            tuple(row) for row in expected_queue
        ]
        assert list(database.execute(
            "SELECT id,state,introduced_at FROM cards ORDER BY id",
        )) == [tuple(row) for row in expected_cards]
    assert sorted(processed_ids) == [1, 2, 3, 4, 5]
    assert processed_ids[1] == 5


def test_postgres_queue_reconcile_checkpoint_discards_stale_replay(monkeypatch):
    from app.services import postgres_queue_refresh

    current_token = "first"
    saved_payload = None
    saved_phase = None
    reconciled_ids = []

    @contextmanager
    def section(*, background):
        assert background
        yield object()

    def prepare(queue_date, processed_ids, introduced_counts):
        if processed_ids:
            return None, 0
        return {"id": 42, "card_id": "opening", "repertoire_id": "repertoire"}, 1

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_token, saved_payload, saved_phase
        saved_phase, saved_payload = next_phase, next_payload
        current_token = None
        return True

    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice",
                        lambda database, task: task["lease_token"] == current_token)
    monkeypatch.setattr(postgres_queue_refresh, "_prepare_unseen_reconciliation", prepare)
    monkeypatch.setattr(postgres_queue_refresh, "_reconcile_one_unseen_entry",
                        lambda database, queue_date, candidate, count:
                        reconciled_ids.append(candidate["id"]) or True)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance)
    first = {"id": "queue-job", "generation": 3, "lease_token": "first",
             "payload": {"queue_date": "2026-09-27", "_queue_phase": "reconcile_unseen"}}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(first)
    assert saved_phase == "reconcile_unseen"
    assert saved_payload["processed_ids"] == [42]
    assert saved_payload["introduced_counts"] == {"repertoire": 2}
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(first)
    current_token = "second"
    second = {**first, "lease_token": "second", "payload": saved_payload}
    assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(second)
    assert saved_phase == "admit_due"
    assert reconciled_ids == [42]


def test_postgres_due_queue_slices_admit_only_eligible_cards_in_order(monkeypatch, tmp_path):
    from app.services import postgres_queue_refresh
    monkeypatch.setattr(postgres_queue_refresh, "lock_queue_date_for_position",
                        lambda _database, _queue_date: None)

    database_path = tmp_path / "due-slices.db"
    with sqlite3.connect(database_path) as database:
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,due_date TEXT,state TEXT,archived INTEGER,
                               pending_validation INTEGER,content_type TEXT,repertoire_id TEXT,
                               study_exercise_id TEXT);
            CREATE TABLE repertoires(id TEXT PRIMARY KEY);
            CREATE TABLE repertoire_cards(card_id TEXT,repertoire_id TEXT);
            CREATE TABLE repertoire_integrity_card_blocks(card_id TEXT,repertoire_id TEXT);
            CREATE TABLE study_exercises(id TEXT PRIMARY KEY,study_id TEXT,status TEXT);
            CREATE TABLE studies(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE study_sibling_burials(exercise_id TEXT,study_day TEXT);
            CREATE TABLE daily_queue(queue_date TEXT,card_id TEXT,position INTEGER);
            INSERT INTO repertoires VALUES('r1');
            INSERT INTO studies VALUES('study',0);
            INSERT INTO study_exercises VALUES('published','study','published');
            INSERT INTO study_exercises VALUES('draft','study','draft');
            INSERT INTO cards VALUES('opening','2026-09-26','learning',0,0,'opening','r1',NULL);
            INSERT INTO cards VALUES('study','2026-09-26','mature',0,0,'study_exercise','r1','published');
            INSERT INTO cards VALUES('blocked','2026-09-26','learning',0,0,'opening','r1',NULL);
            INSERT INTO cards VALUES('draft','2026-09-26','learning',0,0,'study_exercise','r1','draft');
            INSERT INTO cards VALUES('future','2026-09-28','learning',0,0,'opening','r1',NULL);
            INSERT INTO cards VALUES('archived','2026-09-26','learning',1,0,'opening','r1',NULL);
            INSERT INTO cards VALUES('already','2026-09-26','learning',0,0,'opening','r1',NULL);
            INSERT INTO repertoire_integrity_card_blocks VALUES('blocked','r1');
            INSERT INTO daily_queue VALUES('2026-09-27','already',0);
        """)

    class NativeSqlite:
        def __init__(self, database):
            self.database = database

        def execute_native(self, statement, parameters=()):
            return self.database.execute(
                statement.replace("%s", "?").replace("FOR UPDATE", ""), parameters,
            )

    @contextmanager
    def read_section():
        with sqlite3.connect(database_path) as database:
            database.row_factory = sqlite3.Row
            yield NativeSqlite(database)

    monkeypatch.setattr(postgres_queue_refresh, "background_read_connection", read_section)
    admitted_ids = []
    while True:
        card_id = postgres_queue_refresh._prepare_due_card("2026-09-27")
        if card_id is None:
            break
        with sqlite3.connect(database_path) as database:
            assert postgres_queue_refresh._admit_one_due_card(
                NativeSqlite(database), "2026-09-27", card_id,
            )
        admitted_ids.append(card_id)
    assert admitted_ids == ["opening", "study"]
    with sqlite3.connect(database_path) as database:
        assert list(database.execute(
            "SELECT card_id,position FROM daily_queue ORDER BY position",
        )) == [("already", 0), ("opening", 1), ("study", 2)]


def test_postgres_tactical_queue_prepares_outside_database_and_retries_timed_out_read(monkeypatch):
    from psycopg.errors import TransactionTimeout
    from app.services import postgres_queue_refresh

    database_open = False
    settings_attempts = 0

    class QueryResult:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class ReadDatabase:
        def execute(self, statement, parameters):
            nonlocal settings_attempts
            if "tactics_new_per_day" in statement:
                settings_attempts += 1
                if settings_attempts == 1:
                    raise TransactionTimeout("read exceeded its budget")
                return QueryResult([(1,)])
            if "tactic_introductions" in statement:
                return QueryResult([(0,)])
            if "tactic_pack_activation" in statement:
                return QueryResult([("pack-a",)])
            if "tactic_rotation" in statement:
                return QueryResult([("",)])
            if "tactic_progress" in statement:
                return QueryResult([])
            raise AssertionError(statement)

    @contextmanager
    def read_section():
        nonlocal database_open
        assert not database_open
        database_open = True
        try:
            yield ReadDatabase()
        finally:
            database_open = False

    def records(pack_id):
        assert not database_open and pack_id == "pack-a"
        return [{"PuzzleId": "puzzle-a", "FEN": "source-fen"}]

    monkeypatch.setattr(postgres_queue_refresh, "background_read_connection", read_section)
    monkeypatch.setattr(postgres_queue_refresh, "pack_records", records)
    monkeypatch.setattr(postgres_queue_refresh, "validate_puzzle_record",
                        lambda record: ("training-fen", ["e2e4"]))
    monkeypatch.setattr(postgres_queue_refresh, "card_id", lambda fen, moves: "card-a")
    prepared = postgres_queue_refresh._prepare_tactical_introduction("2026-09-28")
    assert prepared == {
        "pack_id": "pack-a", "puzzle_id": "puzzle-a", "card_id": "card-a",
        "training_fen": "training-fen", "solution_json": '["e2e4"]',
        "source_fen": "source-fen", "rotation_cursor": "",
    }
    assert settings_attempts == 2
    assert not database_open


def test_postgres_tactical_queue_foreground_contention_and_stale_replay(monkeypatch):
    from app.services import postgres_queue_refresh

    preparation_started = threading.Event()
    finished = threading.Event()
    current_lease = True
    publications = []
    failures = []

    @contextmanager
    def section(*, background):
        assert background
        yield object()

    def prepare(queue_date):
        preparation_started.set()
        return {"puzzle_id": "one"}

    def publish(database, queue_date, prepared):
        publications.append(prepared["puzzle_id"])
        return True

    def advance(database, task, *, next_phase, next_payload):
        nonlocal current_lease
        current_lease = False
        assert next_phase == "tactical_introductions"
        return True

    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.setattr(postgres_queue_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_queue_refresh, "connection", section)
    monkeypatch.setattr(postgres_queue_refresh, "_prepare_tactical_introduction", prepare)
    monkeypatch.setattr(postgres_queue_refresh, "_publish_tactical_introduction", publish)
    monkeypatch.setattr(postgres_queue_refresh, "lock_current_slice",
                        lambda database, task: current_lease)
    monkeypatch.setattr(postgres_queue_refresh, "advance_task_slice_in_transaction", advance)
    task = {"id": "queue-job", "generation": 1, "lease_token": "lease",
            "payload": {"queue_date": "2026-09-28", "_queue_phase": "tactical_introductions"}}

    def run_slice():
        try:
            assert postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    with postgres_queue_refresh.activity_gate.foreground():
        worker = threading.Thread(target=run_slice)
        worker.start()
        assert not preparation_started.wait(0.05)
    assert finished.wait(2)
    worker.join(timeout=2)
    assert not failures
    assert publications == ["one"]
    assert not postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
    assert publications == ["one"]


def test_postgres_cutover_game_refresh_waits_for_foreground_and_discards_stale_replay(monkeypatch):
    from app import tasks
    from app.services import repertoire_game_refresh

    foreground_finished = threading.Event()
    connection_opened = threading.Event()
    queued_games: list[str] = []
    persisted_cursors: list[str] = []
    current_lease = {"token": "active"}
    claimed_task = {
        "kind": "repertoire_game_refresh", "id": "refresh-task", "generation": 2,
        "lease_token": "active", "payload": {"after_game_id": ""},
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters):
            if "FROM imported_games" in statement:
                return Cursor({"id": "game-1"})
            if "INSERT INTO game_derivation_jobs" in statement:
                queued_games.append(parameters[0])
            return Cursor()

    @contextmanager
    def test_connection(*, background):
        assert background
        connection_opened.set()
        yield Database()

    def enqueue_next_slice(_database, _kind, _key, payload, **_options):
        persisted_cursors.append(payload["after_game_id"])
        current_lease["token"] = "next-generation"

    monkeypatch.setattr(repertoire_game_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_game_refresh.activity_gate, "wait_for_foreground", foreground_finished.wait)
    monkeypatch.setattr(repertoire_game_refresh, "connection", test_connection)
    monkeypatch.setattr(
        repertoire_game_refresh, "lock_current_slice",
        lambda _database, task: task["lease_token"] == current_lease["token"],
    )
    monkeypatch.setattr(repertoire_game_refresh, "enqueue_task_in_transaction", enqueue_next_slice)

    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(repertoire_game_refresh.execute_repertoire_game_refresh_slice(claimed_task)),
    )
    worker.start()
    assert not connection_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [True]
    assert queued_games == ["game-1"]
    assert persisted_cursors == ["game-1"]
    assert repertoire_game_refresh.execute_repertoire_game_refresh_slice(claimed_task) is False
    assert queued_games == ["game-1"]

    claimed_filters: list[tuple[str, ...]] = []
    sent_tasks: list[str] = []
    monkeypatch.setattr(
        tasks, "claim_task",
        lambda *, allowed_kinds: claimed_filters.append(allowed_kinds) or claimed_task,
    )
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda task_name, **_arguments: sent_tasks.append(task_name))
    assert tasks.poll_background_tasks.run() is True
    assert claimed_filters == [tasks._SUPPORTED_BACKGROUND_KINDS]
    assert sent_tasks == ["app.tasks.execute_background_slice"]
    completed_tasks: list[str] = []
    monkeypatch.setattr(tasks, "execute_repertoire_game_refresh_slice", lambda _task: False)
    monkeypatch.setattr(
        tasks, "complete_task",
        lambda task_id, _generation, _lease: completed_tasks.append(task_id),
    )
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *_arguments: nullcontext())
    assert tasks.execute_background_slice.run(claimed_task) is False
    assert completed_tasks == ["refresh-task"]


def test_postgres_opportunity_refresh_dispatches_idempotent_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, opportunity_commands

    dispatched = []
    queued = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key:
            dispatched.append((name, payload, idempotency_key)) or {"queued": True},
    )
    response = TestClient(main.app).post(
        "/api/repertoires/rep/opportunities/refresh",
        headers={"Idempotency-Key": "refresh-one"},
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"queued": True}
    assert dispatched == [("opportunities.refresh", {"repertoire_id": "rep"}, "refresh-one")]

    class ExistingRepertoire:
        def execute_native(self, statement, _parameters):
            assert "FROM repertoires" in statement
            return SimpleNamespace(fetchone=lambda: (1,))

    monkeypatch.setattr(
        opportunity_commands, "enqueue_task_in_transaction",
        lambda _database, kind, key, payload, *, priority:
            queued.append((kind, key, payload, priority)),
    )
    assert opportunity_commands.refresh_opportunities(
        ExistingRepertoire(), {"repertoire_id": "rep"},
    ) == {"queued": True}
    assert queued == [("repertoire_opportunity", "rep",
                       {"repertoire_id": "rep", "phase": "summaries", "cursor": ""}, 130)]


def test_postgres_opportunity_refresh_yields_to_foreground_and_discards_restart_replay(monkeypatch):
    from app import tasks
    from app.services import repertoire_opportunities

    foreground_finished = threading.Event()
    read_opened = threading.Event()
    published_phases: list[str] = []
    current_lease = {"token": "first-lease"}
    claimed_task = {
        "kind": "repertoire_opportunity", "id": "opportunity-refresh",
        "generation": 3, "lease_token": "first-lease",
        "payload": {"repertoire_id": "rep", "phase": "summaries", "cursor": ""},
    }

    class EmptyCursor:
        def fetchone(self):
            return None

    class EmptyDatabase:
        def execute(self, statement, _parameters):
            assert "repertoire_decision_events" in statement
            return EmptyCursor()

    @contextmanager
    def bounded_read():
        read_opened.set()
        yield EmptyDatabase()

    @contextmanager
    def bounded_write(*, background):
        assert background
        yield object()

    monkeypatch.setattr(repertoire_opportunities.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_opportunities.activity_gate, "wait_for_foreground", foreground_finished.wait)
    monkeypatch.setattr(repertoire_opportunities, "background_read_connection", bounded_read)
    monkeypatch.setattr(repertoire_opportunities, "connection", bounded_write)
    monkeypatch.setattr(
        repertoire_opportunities, "lock_current_slice",
        lambda _database, task: task["lease_token"] == current_lease["token"],
    )
    monkeypatch.setattr(
        repertoire_opportunities, "_advance_slice",
        lambda _database, _task, phase, _cursor: published_phases.append(phase),
    )

    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(
        repertoire_opportunities.execute_opportunity_slice(claimed_task)
    ))
    worker.start()
    assert not read_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [True]
    assert published_phases == ["cards"]
    current_lease["token"] = "replacement-lease"
    assert repertoire_opportunities.execute_opportunity_slice(claimed_task) is True
    assert published_phases == ["cards"]
    assert "repertoire_opportunity" in tasks._SUPPORTED_BACKGROUND_KINDS


def test_postgres_opportunity_recurring_query_uses_native_modulo(monkeypatch):
    from app.services import repertoire_opportunities

    statements: list[str] = []

    class EmptyCursor:
        def __iter__(self):
            return iter(())

    class RecordingDatabase:
        def execute_native(self, statement, _parameters):
            statements.append(statement)
            return EmptyCursor()

    monkeypatch.setattr(repertoire_opportunities.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_opportunities, "_window_days", lambda _database: 90)
    assert repertoire_opportunities._load_recurring_decisions(RecordingDatabase(), "rep", "card") == []
    assert "MOD(event.ply-earlier.ply,2)=0" in statements[0]
    assert "event.repertoire_id=%s" in statements[0]


def test_postgres_cutover_threat_report_audit_yields_and_replays_once(monkeypatch):
    from app import tasks
    from app.services import threat_pipeline

    foreground_finished = threading.Event()
    read_opened = threading.Event()
    advanced_cursors: list[str] = []
    current_lease = {"token": "active"}
    claimed_task = {
        "kind": "defensive_threat_report_audit", "id": "audit-task",
        "generation": 4, "lease_token": "active", "payload": {"cursor": ""},
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class ReadDatabase:
        def execute(self, statement, _parameters):
            if "FROM threat_analysis_requests" in statement:
                return Cursor({"id": "report-1", "request_json": "{}", "report_json": "{}"})
            return Cursor()

    class WriteDatabase:
        def execute(self, statement, parameters):
            if statement.startswith("UPDATE background_tasks"):
                advanced_cursors.append(parameters[0])
            return Cursor()

    @contextmanager
    def test_read_connection():
        read_opened.set()
        yield ReadDatabase()

    @contextmanager
    def test_write_connection(*, background):
        assert background
        yield WriteDatabase()

    monkeypatch.setattr(threat_pipeline.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(threat_pipeline.activity_gate, "wait_for_foreground", foreground_finished.wait)
    monkeypatch.setattr(threat_pipeline, "background_read_connection", test_read_connection)
    monkeypatch.setattr(threat_pipeline, "connection", test_write_connection)
    monkeypatch.setattr(threat_pipeline, "_request_from_json", lambda _raw: object())
    monkeypatch.setattr(threat_pipeline, "report_from_json", lambda _raw: object())
    monkeypatch.setattr(threat_pipeline, "validate_analysis_report", lambda *_arguments: None)
    monkeypatch.setattr(
        threat_pipeline, "lock_current_slice",
        lambda _database, task: task["lease_token"] == current_lease["token"],
    )

    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(threat_pipeline.execute_threat_report_audit(claimed_task)),
    )
    worker.start()
    assert not read_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [True]
    assert advanced_cursors == ['{"cursor": "report-1"}']
    current_lease["token"] = "next-generation"
    assert threat_pipeline.execute_threat_report_audit(claimed_task) is True
    assert advanced_cursors == ['{"cursor": "report-1"}']

    polled_filters: list[tuple[str, ...]] = []
    sent_tasks: list[str] = []
    monkeypatch.setattr(
        tasks, "claim_task",
        lambda *, allowed_kinds: polled_filters.append(allowed_kinds) or claimed_task,
    )
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda task_name, **_arguments: sent_tasks.append(task_name))
    assert tasks.poll_background_tasks.run() is True
    assert polled_filters == [tasks._SUPPORTED_BACKGROUND_KINDS]
    assert sent_tasks == ["app.tasks.execute_background_slice"]


def test_postgres_discovery_recommendation_yields_to_foreground_and_discards_restart_replay(monkeypatch):
    from app import tasks
    from app.services import discovery_admission

    foreground_finished = threading.Event()
    preparation_finished = threading.Event()
    release_preparation = threading.Event()
    first_read_opened = threading.Event()
    writer_opened = threading.Event()
    published_requests: list[str] = []
    current_lease = {"token": "first-lease"}
    task = {
        "kind": "discovery_recommendation", "id": "recommendation-task",
        "generation": 2, "lease_token": "first-lease",
        "payload": {"opportunity_id": "opportunity-1"},
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class ReadDatabase:
        def execute(self, statement, _parameters):
            assert "FROM repertoire_opportunities" in statement
            return Cursor({"id": "opportunity-1"})

    class WriteDatabase:
        def execute(self, statement, _parameters):
            if "FROM repertoire_opportunities" in statement:
                return Cursor({"status": "active", "card_id": None})
            if statement.startswith("INSERT"):
                published_requests.append(statement)
            return Cursor()

    @contextmanager
    def bounded_read():
        first_read_opened.set()
        yield ReadDatabase()

    @contextmanager
    def bounded_write(*, background):
        assert background
        writer_opened.set()
        yield WriteDatabase()

    @dataclass(frozen=True)
    class PreparedRequest:
        request_id: str = "request-1"

    def prepare_source(_opportunity):
        preparation_finished.set()
        assert release_preparation.wait(2)
        return {"id": "game-1", "ply": 2}

    monkeypatch.setattr(discovery_admission.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(discovery_admission.activity_gate, "wait_for_foreground", foreground_finished.wait)
    monkeypatch.setattr(discovery_admission, "background_read_connection", bounded_read)
    monkeypatch.setattr(discovery_admission, "connection", bounded_write)
    monkeypatch.setattr(discovery_admission, "_background_source_game", prepare_source)
    monkeypatch.setattr(discovery_admission, "_full_history_request", lambda _game: (None, PreparedRequest()))
    monkeypatch.setattr(
        discovery_admission, "lock_current_slice",
        lambda _database, claimed: claimed["lease_token"] == current_lease["token"],
    )
    worker = threading.Thread(target=discovery_admission.execute_recommendation_request_slice, args=(task,))
    worker.start()
    assert not first_read_opened.wait(0.05)
    foreground_finished.set()
    assert preparation_finished.wait(2)
    assert not writer_opened.is_set()
    release_preparation.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert len(published_requests) == 2
    current_lease["token"] = "replacement-lease"
    discovery_admission.execute_recommendation_request_slice(task)
    assert len(published_requests) == 2
    assert "discovery_recommendation" in tasks._SUPPORTED_BACKGROUND_KINDS


def test_postgres_activity_control_dispatches_idempotent_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import activity_commands, main

    dispatched = []
    controlled = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key:
            dispatched.append((name, payload, idempotency_key)) or {"ok": True},
    )
    response = TestClient(main.app).post(
        "/api/system/activity/control", headers={"Idempotency-Key": "control-one"},
        json={"source": "durable", "id": "task-one", "action": "prioritize"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert dispatched == [("activity.control", {
        "source": "durable", "id": "task-one", "action": "prioritize",
    }, "control-one")]
    monkeypatch.setattr(
        activity_commands, "set_control_in_transaction",
        lambda database, source, work_id, action:
            controlled.append((database, source, work_id, action)) or True,
    )
    database = object()
    assert activity_commands.control_activity(database, {
        "source": "durable", "id": "task-one", "action": "prioritize",
    }) == {"ok": True}
    assert controlled == [(database, "durable", "task-one", "prioritize")]


def test_postgres_coverage_refresh_dispatches_durable_seed(monkeypatch):
    from fastapi.testclient import TestClient
    from app import coverage_commands, main

    dispatched = []
    requested = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(
        command_dispatch, "dispatch_command",
        lambda name, payload, *, idempotency_key:
            dispatched.append((name, payload, idempotency_key)) or
            {"run_id": "run-one", "status": "queued"},
    )
    response = TestClient(main.app).post(
        "/api/repertoires/rep/coverage/refresh",
        headers={"Idempotency-Key": "coverage-one"},
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"run_id": "run-one", "status": "queued"}
    assert dispatched == [("coverage.refresh.request", {"repertoire_id": "rep"}, "coverage-one")]
    monkeypatch.setattr(
        coverage_commands, "request_coverage_seed_in_transaction",
        lambda database, repertoire_id, *, automatic:
            requested.append((database, repertoire_id, automatic)) or
            {"run_id": "run-one", "status": "queued"},
    )
    database = object()
    assert coverage_commands.request_coverage_refresh(database, {
        "repertoire_id": "rep",
    }) == {"run_id": "run-one", "status": "queued"}
    assert requested == [(database, "rep", False)]


def test_postgres_coverage_seed_supersedes_active_generation_after_branch_edit(monkeypatch):
    from app.services import postgres_coverage_seed

    statements = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            if "SELECT 1 FROM repertoires" in statement:
                return SimpleNamespace(fetchone=lambda: (1,))
            if "SELECT id FROM repertoire_coverage_runs" in statement:
                return SimpleNamespace(fetchone=lambda: ("old-run",))
            if "SELECT * FROM settings" in statement:
                return SimpleNamespace(fetchone=lambda: {
                    "coverage_maia_elo": 1500, "coverage_reply_denominator": 2,
                    "coverage_cumulative_target": 80, "coverage_horizon_fullmoves": 4,
                    "coverage_path_floor": 0.01,
                })
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(postgres_coverage_seed, "recent_player_cohort", lambda *_args: {
        "maia_elo": 1500, "explorer_rating": 1600, "recent_median_rating": 1500,
        "speed_weights": {"rapid": 1}, "games": 1,
    })
    monkeypatch.setattr(postgres_coverage_seed, "_source_fingerprint",
                        lambda *_args: "new-fingerprint")
    enqueued = []
    monkeypatch.setattr(postgres_coverage_seed, "enqueue_task_in_transaction",
                        lambda database, kind, key, payload, **kwargs:
                            enqueued.append((kind, key, payload)))
    result = postgres_coverage_seed.request_coverage_seed_in_transaction(
        Database(), "rep", automatic=True, supersede_active=True,
    )
    assert result["run_id"] != "old-run"
    assert any("UPDATE repertoire_coverage_runs SET status='failed'" in sql
               and parameters[-1] == "old-run" for sql, parameters in statements)
    assert enqueued[0][2]["run_id"] == result["run_id"]
    assert enqueued[0][2]["source_fingerprint"] == "new-fingerprint"


def test_postgres_coverage_seed_yields_to_foreground_and_discards_restart_replay(monkeypatch):
    from app import tasks
    from app.services import postgres_coverage_seed

    foreground_finished = threading.Event()
    read_opened = threading.Event()
    staged_positions: list[str] = []
    current_lease = {"token": "first-lease"}
    task = {
        "kind": "coverage_seed", "id": "seed-task", "generation": 5,
        "lease_token": "first-lease",
        "payload": {"run_id": "run-one", "repertoire_id": "rep",
                    "horizon_fullmoves": 4, "source_fingerprint": "fingerprint",
                    "after_line_id": "", "position_index": 0},
    }

    class Cursor:
        def fetchone(self):
            return {"id": "line-one", "start_fen": chess.STARTING_FEN,
                    "moves_json": '["e2e4","e7e5"]', "trained_color": "white"}

    class ReadDatabase:
        def execute_native(self, statement, _parameters):
            assert "FROM repertoire_lines" in statement
            return Cursor()

    @contextmanager
    def bounded_read():
        assert foreground_finished.wait(2)
        read_opened.set()
        yield ReadDatabase()

    @contextmanager
    def bounded_write(*, background):
        assert background
        class BuildingRun:
            def execute_native(self, statement, _parameters):
                assert "FROM repertoire_coverage_runs" in statement
                return SimpleNamespace(fetchone=lambda: ("building",))
        yield BuildingRun()

    monkeypatch.setattr(postgres_coverage_seed, "background_read_connection", bounded_read)
    monkeypatch.setattr(postgres_coverage_seed, "connection", bounded_write)
    monkeypatch.setattr(postgres_coverage_seed, "discover_opponent_positions",
                        lambda _lines, _horizon: [{"fen_key": "position-one"}])
    monkeypatch.setattr(postgres_coverage_seed, "lock_current_slice",
                        lambda _database, claimed: claimed["lease_token"] == current_lease["token"])
    monkeypatch.setattr(postgres_coverage_seed, "_stage_position",
                        lambda _database, _task, position: staged_positions.append(position["fen_key"]))
    monkeypatch.setattr(postgres_coverage_seed, "advance_task_slice_in_transaction",
                        lambda *_args, **_kwargs: True)
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(postgres_coverage_seed.execute_coverage_seed_slice(task)))
    worker.start()
    assert not read_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == [True]
    assert staged_positions == ["position-one"]
    current_lease["token"] = "replacement-lease"
    assert postgres_coverage_seed.execute_coverage_seed_slice(task) is False
    assert staged_positions == ["position-one"]
    assert "coverage_seed" in tasks._SUPPORTED_BACKGROUND_KINDS


def test_postgres_coverage_seed_rejects_source_change_before_activation(monkeypatch):
    from app.services import postgres_coverage_seed

    updates = []
    task = {
        "id": "seed-task", "generation": 4, "lease_token": "lease",
        "payload": {"run_id": "run", "repertoire_id": "rep",
                    "horizon_fullmoves": 4, "source_fingerprint": "original",
                    "after_line_id": "last-line", "position_index": 0},
    }

    @contextmanager
    def bounded_read():
        yield object()

    class WriteDatabase:
        def execute_native(self, statement, parameters):
            updates.append((statement, parameters))

    @contextmanager
    def bounded_write(*, background):
        assert background
        yield WriteDatabase()

    monkeypatch.setattr(postgres_coverage_seed, "prepare_next_coverage_line", lambda *_args: None)
    monkeypatch.setattr(postgres_coverage_seed, "background_read_connection", bounded_read)
    monkeypatch.setattr(postgres_coverage_seed, "_source_fingerprint", lambda _database, _rep: "changed")
    monkeypatch.setattr(postgres_coverage_seed, "connection", bounded_write)
    monkeypatch.setattr(postgres_coverage_seed, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(postgres_coverage_seed, "complete_task_slice_in_transaction", lambda *_args: True)
    assert postgres_coverage_seed.execute_coverage_seed_slice(task) is True
    assert len(updates) == 1
    assert "status='failed'" in updates[0][0]
    assert updates[0][1][2] == "run"


def test_postgres_coverage_seed_terminal_failure_marks_run_failed(monkeypatch):
    from app.services import durable_tasks

    statements = []

    class RecordingDatabase:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            if statement.startswith("SELECT attempt_count,max_attempts"):
                return SimpleNamespace(fetchone=lambda: {
                    "attempt_count": 5, "max_attempts": 5,
                    "kind": "coverage_seed", "payload_json": '{"run_id":"run-one"}',
                })
            return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(durable_tasks, "submit_background_write",
                        lambda operation, *, label: operation(RecordingDatabase()))
    monkeypatch.setattr(durable_tasks, "_record_event", lambda *_args: None)
    assert durable_tasks.fail_task("task", 1, "lease", RuntimeError("bad position"))["state"] == "failed"
    assert any("UPDATE repertoire_coverage_runs SET status='failed'" in statement
               and parameters[2] == "run-one" for statement, parameters in statements)


def test_postgres_coverage_building_summary_remains_queued(monkeypatch):
    from app.services import repertoire_coverage

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class ReadDatabase:
        def execute(self, statement, _parameters):
            if "FROM repertoire_coverage_runs" in statement:
                return Cursor({"id": "run-one", "status": "building",
                               "settings_json": "{}", "last_error": None})
            if "FROM repertoire_coverage_candidates" in statement:
                return Cursor({"required_probability": 0, "covered_branches": 0,
                               "required_branches": 0, "covered_probability": 0})
            return Cursor((0,))

    @contextmanager
    def read_connection():
        yield ReadDatabase()

    monkeypatch.setattr(repertoire_coverage, "connection", read_connection)
    summary = repertoire_coverage.coverage_summary("rep")
    assert summary["status"] == "queued"
    assert summary["is_complete"] is False
