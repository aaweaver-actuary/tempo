"""Fake borrowed-driver checks for current PGN storage; no native claims.

E01 dispositions of the six retired-import facade cases:
- reverse-policy imports: rebind to current PGN storage/preparation and receipts;
- inert public facade: retired approval facade; retain inert storage-owner import;
- 12-field raw receipt: rebind unchanged projection to receipts.operation_status;
- seven-field approval card preview: retired; retain current existing/deleted IDs;
- facade INTRANS predicate: retired; retain borrowed-only storage lifecycle check;
- original receipt lock: rebind to current opaque receipts capability directly.
The removed approval API is not restored. Current imports.pgn.explicit admission
and discard behavior remains covered below, including receipt identity and batches.
"""

import ast
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import chess
import pytest

from app import pgn_import_commands as application
from tempo_storage.authored_repertoires.pgn_import import storage


def _retained_storage_application_imports(source):
    """Inspect this adapter's imports, including imports inside lazy methods."""
    modules = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return {name for name in modules if name == "app" or name.startswith("app.")}


def test_retained_import_storage_does_not_import_application_policy():
    from tempo_storage.authored_repertoires.pgn_import import preparation
    from tempo_storage.commands.receipts import storage as receipts

    for owner in (storage, preparation, receipts):
        assert _retained_storage_application_imports(Path(owner.__file__).read_text()) == set()


@pytest.mark.parametrize(
    "source",
    [
        "from app.repertoire.changes.imports import ImportPreview, verify_import_approval",
        "def preview():\n    from app.pgn_import_commands import ImportPayload, prepare_import_payload",
        "def validate():\n    from app.pgn_import_validation import validate_sources",
        "def apply():\n    import app.pgn_import_commands as commands",
    ],
)
def test_retained_import_storage_guard_rejects_original_reverse_application_imports(
    source,
):
    # These cover the original adapter's value/policy and lazy command imports.
    assert _retained_storage_application_imports(source)


def test_current_import_storage_imports_without_command_registration():
    script = """
import importlib.abc, socket, sqlite3, subprocess, sys
def forbidden(*args, **kwargs):
    raise AssertionError('connection or process startup during storage import')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
sqlite3.connect = forbidden
subprocess.Popen = forbidden
denied = {'app.pgn_import_commands', 'app.command_gateway', 'app.main', 'app.tasks'}
class RejectImporterOrDatabase(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in denied):
            raise AssertionError('unexpected startup import: ' + fullname)
sys.meta_path.insert(0, RejectImporterOrDatabase())
import psycopg
psycopg.connect = forbidden
from tempo_storage.authored_repertoires.pgn_import import storage, preparation
from tempo_storage.commands.receipts import storage as receipts
assert all(callable(value) for value in (
    storage.insert_import_rows, storage.discard_receipt, preparation.delivery,
    receipts.operation_status, receipts.receipt_writer, receipts.lock_operation,
))
assert not denied.intersection(sys.modules)
"""
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(root / "backend"), str(root / "database"), environment.get("PYTHONPATH", "")))
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


class Rows:
    def __init__(self, *, value=None, rows=(), events=None):
        self.value, self.rows, self.events = value, rows, events

    def fetchone(self):
        if self.events is not None:
            self.events.append("fetchone")
        return self.value

    def __iter__(self):
        if self.events is not None:
            self.events.append("iterate")
        return iter(self.rows)

    def fetchall(self):
        return self.rows


class Batch:
    def __init__(self, events, result_sets):
        self.events, self.result_sets = events, iter(result_sets)
        self.calls, self.open = [], False

    def __enter__(self):
        self.open = True
        self.events.append("cursor_enter")
        return self

    def __exit__(self, *error):
        self.open = False
        self.events.append("cursor_exit")

    def executemany(self, statement, parameters, **keywords):
        self.calls.append((statement, parameters, keywords))
        self.events.append(("batch", len(self.calls)))
        if len(self.calls) == 1:
            self.current = next(self.result_sets, None)

    def fetchall(self):
        self.events.append("fetchall")
        return self.current

    def nextset(self):
        self.events.append("nextset")
        self.current = next(self.result_sets, None)
        return self.current is not None


class Borrowed:
    def __init__(self, events, replies=(), batch=None):
        self.events, self.replies, self.batch = events, iter(replies), batch
        self.raw = self
        self.native_calls, self.raw_calls = [], []

    def execute_native(self, statement, parameters=()):
        self.native_calls.append((statement, parameters))
        self.events.append(("native", len(self.native_calls)))
        return next(self.replies)

    def execute(self, statement, parameters=()):
        self.raw_calls.append((statement, parameters))
        self.events.append(("raw", len(self.raw_calls)))
        return next(self.replies)

    def cursor(self):
        self.events.append("cursor")
        return self.batch

    def __getattr__(self, name):
        raise AssertionError("Unexpected transaction/lifecycle access: " + name)


def test_retained_import_storage_preserves_full_receipt_tuple_and_raw_json():
    from tempo_storage.commands.receipts import storage as receipts
    from tempo_storage.postgres import TempoRow

    columns = (
        "command_name",
        "request_hash",
        "state",
        "response_json",
        "error_json",
        "attempt_count",
        "next_retry_at",
        "last_error_json",
        "lease_expires_at",
        "payload_json",
        "retry_cycle",
        "cycle_attempt_count",
    )
    payload_json = ' { "source_name": "book.pgn", "lines": [] }\n'
    error_json = '{ "status_code":409, "detail": {"code":"repertoire_conflict"} }'
    values = (
        "imports.pgn.explicit",
        "original-request-hash",
        "failed",
        None,
        error_json,
        7,
        "next-retry",
        '{ "old":true }',
        "lease-expiry",
        payload_json,
        2,
        3,
    )
    events = []
    database = Borrowed(events, replies=[Rows(value=TempoRow(columns, values)), Rows()])
    result = receipts.operation_status(database, "original-operation")
    assert tuple(result) == values and len(result) == 12
    assert result[4] == error_json and result[9] == payload_json
    assert receipts.operation_status(database, "missing-operation") is None
    assert [parameters for _, parameters in database.raw_calls] == [
        ("original-operation",),
        ("missing-operation",),
    ]
    assert events == [("raw", 1), ("raw", 2)]
    assert database.native_calls == []


def test_current_import_card_identity_projections_preserve_empty_and_existing_rows():
    # The retired approval preview's seven fields/order have no current caller.
    # Import admission consumes existing/deleted identities, not approval previews.
    database = Borrowed([], replies=[Rows(rows=[("a",), ("z",)]), Rows(rows=[])])
    identifiers = ["z", "a"]
    assert tuple(storage.existing_card_ids(database, identifiers)) == (("a",), ("z",))
    assert tuple(storage.deleted_card_ids(database, identifiers)) == ()
    assert [parameters for _, parameters in database.native_calls] == [(identifiers,), (identifiers,)]
    assert database.raw_calls == []


def test_current_import_storage_borrows_transaction_without_lifecycle_access():
    # RetainedImportStorage.in_transaction was only an approval facade predicate.
    # Current adapters borrow the caller's transaction; the strict double rejects
    # opening, committing, rolling back, closing, or inspecting driver lifecycle.
    database = Borrowed([], replies=[Rows(), Rows(), Rows()])
    storage.lock_main_repertoire(database)
    storage.save_repertoire(database, "r", "name", "source", "original-time")
    storage.lock_discard_operation(database, "original-operation")
    assert len(database.native_calls) == 2 and len(database.raw_calls) == 1
    assert database.raw_calls[0][1] == ("original-operation",)


def test_current_import_receipt_lock_preserves_original_identity_and_opaque_writer():
    from tempo_storage.commands.receipts import storage as receipts

    database = Borrowed([], replies=[Rows()])
    writer = receipts.receipt_writer(database)
    assert not hasattr(writer, "commit") and not hasattr(writer, "connection")
    assert receipts.lock_operation(writer, "original-operation") is None
    assert [parameters for _, parameters in database.raw_calls] == [("original-operation",)]
    assert database.native_calls == []


@pytest.mark.parametrize(
    "line_rows,result_sets,expected",
    [
        ([("one",), ("two",)], [[("one",)], [], [("two",), ("one",)]], {"one", "two"}),
        ([], [], set()),
    ],
)
def test_import_batches_keep_returning_sets_then_depth_annotations_and_cursor_close(
    line_rows, result_sets, expected
):
    events = []
    batch = Batch(events, result_sets)
    database = Borrowed(events, batch=batch)
    depths, annotations = [("depth",)], [("annotation",)]
    assert (
        storage.insert_import_rows(database, line_rows, depths, annotations) == expected
    )
    assert [
        call[1] is rows
        for call, rows in zip(batch.calls, (line_rows, depths, annotations))
    ] == [True] * 3
    assert [call[2] for call in batch.calls] == [{"returning": True}, {}, {}]
    returning_events = ["fetchall", "nextset"] * len(result_sets) if line_rows else []
    assert events == [
        "cursor",
        "cursor_enter",
        ("batch", 1),
        *returning_events,
        ("batch", 2),
        ("batch", 3),
        "cursor_exit",
    ]
    assert not batch.open


@pytest.mark.parametrize("existing_repertoire", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_import_keeps_clock_identity_lazy_reads_certification_and_callback_order(
    monkeypatch, existing_repertoire, explicit
):
    events = []
    batch = Batch(events, [])
    database = Borrowed(
        events,
        replies=[
            Rows(),
            Rows(value=("repertoire",) if existing_repertoire else None, events=events),
            Rows(),
            Rows(),
            Rows(
                rows=[
                    dict(
                        id="old-line",
                        start_fen=chess.STARTING_FEN,
                        moves_json='["e2e4"]',
                    )
                ],
                events=events,
            ),
            Rows(rows=[("existing",)], events=events),
            Rows(rows=[], events=events),
        ],
        batch=batch,
    )

    def now(zone):
        events.append("clock")
        return SimpleNamespace(isoformat=lambda: "timestamp")

    def new_id():
        events.append("uuid")
        return "repertoire"

    def scope(connection, lines):
        assert connection is database and not batch.open
        assert lines == [
            ("repertoire", chess.STARTING_FEN, ["e2e4"]),
            ("repertoire", chess.STARTING_FEN, ["d2d4"]),
        ]
        events.append("scope")
        return ["old-route", "new-route"]

    original_executemany = batch.executemany

    def executemany(statement, parameters, **keywords):
        if not batch.calls:
            # Use the exact ID prepared by the application, rather than replacing its hash rule.
            batch.result_sets = iter([[(parameters[1][0],)]])
        original_executemany(statement, parameters, **keywords)

    def certify(connection, repertoire, route):
        assert connection is database and repertoire == "repertoire" and not batch.open
        assert route == "new-route"
        events.append("certify")

    monkeypatch.setattr(batch, "executemany", executemany)
    monkeypatch.setattr(application, "datetime", SimpleNamespace(now=now))
    monkeypatch.setattr(application.uuid, "uuid4", new_id)
    monkeypatch.setattr(
        application,
        "date",
        SimpleNamespace(today=lambda: events.append("day") or date(2026, 10, 20)),
    )
    monkeypatch.setattr(application, "ensure_batch_lines_in_scope", scope)
    monkeypatch.setattr(application, "certify_admitted_route", certify)
    monkeypatch.setattr(
        application,
        "invalidate_integrity_in_transaction",
        lambda *args: events.append("invalidate"),
    )

    def graph(*args, **kwargs):
        assert kwargs == (
            {"origin_import_operation_id": "explicit-operation"} if explicit else {}
        )
        events.append(("graph", args))
        return {"id": "graph", "generation": 2}

    monkeypatch.setattr(application, "request_graph_rebuild_in_transaction", graph)
    monkeypatch.setattr(
        application,
        "integrity_summary",
        lambda *args: events.append("summary") or {"state": "pending"},
    )
    payload = dict(
        source_name="book.pgn",
        trained_color="black",
        depth=3,
        games_found=2,
        unique_lines=2,
        imported_segments_count=2,
        decision_segments_count=1,
        prefix_segments_count=1,
        segment_ids=["existing", "new"],
        prefix_segment_ids=["existing"],
        lines=[
            dict(starting_fen=chess.STARTING_FEN, moves=["e2e4"], annotations=[]),
            dict(
                starting_fen=chess.STARTING_FEN,
                moves=["d2d4"],
                annotations=[
                    dict(
                        fen_key="fen",
                        comment="text",
                        arrows=[dict(color="G")],
                        squares=[],
                    )
                ],
            ),
        ],
    )
    # The pre-existing outer Games refresh decorator is unchanged and outside this split.
    if explicit:
        payload["operation_id"] = "explicit-operation"
    handler = (
        application.explicit_pgn_import if explicit else application.admit_pgn_import
    )
    result = handler(database, payload)
    assert events[:3] == ["clock", ("native", 1), ("native", 2)]
    assert events.count("uuid") == (0 if existing_repertoire else 1)
    assert database.native_calls[3][1] == (
        "repertoire",
        "book",
        "book.pgn",
        "timestamp",
    )
    assert batch.calls[0][1][0][0] == "old-line"
    assert batch.calls[1][1] == [("old-line", 3), (batch.calls[0][1][1][0], 3)]
    assert batch.calls[2][1] == [
        ("repertoire", "fen", "text", '[{"color": "G"}]', "[]", "timestamp")
    ]
    assert (
        events.index("cursor_exit")
        < events.index("certify")
        < events.index(("native", 6))
    )
    assert events[-4:] == [
        "invalidate",
        "day",
        ("graph", (database, "repertoire", "2026-10-20")),
        "summary",
    ]
    assert events.count("iterate") == 3
    assert result["cards_created"] == 1 and result["decision_cards_created"] == 1
    assert result["prefix_cards_created"] == 0 and result["shared_prefixes_reused"] == 1
    assert (
        result["integrity"] == {"state": "pending"}
        and result["graph_state"] == "refreshing"
    )
    assert result.get("preparation") == (
        {"graph_task_id": "graph", "graph_generation": 2} if explicit else None
    )
    assert all(
        "UPDATE repertoires SET is_main" not in sql for sql, _ in database.native_calls
    )
    assert "is_main=excluded" not in database.native_calls[3][0]


@pytest.mark.parametrize(
    "receipt,outcome,status",
    [
        (("imports.pgn.admit", "queued", None), "discarded", None),
        (
            ("imports.pgn.admit", "complete", '{"repertoire_id":"saved"}'),
            "already_complete",
            None,
        ),
        (("other.command", "failed", None), None, 409),
    ],
)
def test_discard_keeps_same_raw_lock_insert_read_before_original_decision(
    receipt, outcome, status
):
    events = []
    database = Borrowed(
        events, replies=[Rows(), Rows(), Rows(value=receipt, events=events), Rows()]
    )
    if status:
        with pytest.raises(application.HTTPException) as error:
            application.discard_pgn_import(database, {"operation_id": "operation"})
        assert error.value.status_code == status
    else:
        result = application.discard_pgn_import(database, {"operation_id": "operation"})
        assert result["outcome"] == outcome
        if outcome == "already_complete":
            assert result["import_result"] == {"repertoire_id": "saved"}
    expected_writes = 4 if outcome == "discarded" else 3
    assert events == [("raw", 1), ("raw", 2), ("raw", 3), "fetchone"] + (
        [("raw", 4)] if expected_writes == 4 else []
    )
    assert len(database.raw_calls) == expected_writes
    assert database.raw_calls[0][1] == ("operation",)
    assert database.raw_calls[1][1][:2] == ("operation", "discarded:operation")
    assert json.loads(database.raw_calls[1][1][2]) == dict(
        code="import_discarded", message="PGN import discarded.", status_code=410
    )
    if outcome == "discarded":
        assert database.raw_calls[-1][1] == (database.raw_calls[1][1][2], "operation")


def test_discard_rejects_invalid_identity_before_any_storage():
    database = Borrowed([])
    with pytest.raises(application.HTTPException) as error:
        application.discard_pgn_import(database, {"operation_id": ""})
    assert error.value.status_code == 422
    assert database.raw_calls == []
