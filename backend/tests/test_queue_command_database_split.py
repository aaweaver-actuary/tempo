"""Foreground queue storage capture, lock order and warming boundaries."""

import ast

import pytest
from pathlib import Path

from app import queue_commands
from app import queue_attempt_origins
from app.services import durable_tasks
from app.services.queue_ordering import PRESENTATION_VERSION
from tempo_storage import postgres
from tempo_storage.commands import queue as storage


@pytest.mark.parametrize("after,selected,expected_position,shift", [
    (None, None, 9, False), (0, 0, 0, True), (4, None, 9, True), (-2, 3, 3, True),
])
def test_requeue_preserves_offset_zero_fallback_and_write_order(monkeypatch, after, selected, expected_position, shift):
    from app.services import queue_application as owner
    events, database = [], object()
    monkeypatch.setattr(owner, "capture_daily_queue_order", lambda *args: events.append(("capture", args)) or "order")
    monkeypatch.setattr(owner.requeue_storage, "next_cycle", lambda *args: events.append(("cycle", args)) or 7)
    monkeypatch.setattr(owner.requeue_storage, "next_position", lambda *args: events.append(("tail", args)) or 9)
    monkeypatch.setattr(owner.requeue_storage, "position_at_offset", lambda *args: events.append(("offset", args)) or selected)
    monkeypatch.setattr(owner.requeue_storage, "shift_positions", lambda *args: events.append(("shift", args)))
    monkeypatch.setattr(owner.requeue_storage, "append_attempt", lambda *args: events.append(("insert", args)) or 51)
    monkeypatch.setattr(owner.requeue_storage, "set_review_admission", lambda *args: events.append(("admission", args)))
    monkeypatch.setattr(owner, "preserve_daily_queue_order", lambda *args, **kwargs: events.append(("preserve", args, kwargs)))
    assert owner.requeue(database, "day", "card", after, "guided") == 51
    assert [e[0] for e in events] == (["capture", "cycle", "tail", "insert", "admission", "preserve"] if after is None else
        ["capture", "cycle", "offset"] + (["tail"] if selected is None else []) + ["shift", "insert", "admission", "preserve"])
    assert ("insert", (database, "day", "card", 7, expected_position, "guided")) in events
    assert (("shift", (database, "day", expected_position)) in events) is shift
    assert events[-1] == ("preserve", (database, "day"), {"previous": "order"})
    if after is not None:
        assert ("offset", (database, "day", owner.MISS_REASON, max(0, after))) in events


@pytest.mark.parametrize("row,expected", [(None, None), ((0,), 0), ((8,), 8)])
def test_requeue_offset_storage_distinguishes_missing_row_from_zero(monkeypatch, row, expected):
    from tempo_storage.queue_refresh.application import requeue
    calls = []
    monkeypatch.setattr(requeue, "available_attempt_sql", lambda _: "SELECT q.id FROM daily_queue q")
    class Database:
        def execute(self, sql, parameters):
            calls.append((sql, parameters))
            return type("Cursor", (), {"fetchone": lambda self: row})()
    assert requeue.position_at_offset(Database(), "day", "reason", 4) == expected
    assert calls == [("SELECT q.position FROM daily_queue q ORDER BY q.position,q.id LIMIT 1 OFFSET %s", ("day", 4))]


@pytest.mark.parametrize("method,args", [("next_cycle", ("day", "card")), ("next_position", ("day",)),
                                         ("append_attempt", ("day", "card", 1, 2, "guided"))])
@pytest.mark.parametrize("row", [None, (None,), (True,), ("9",)])
def test_requeue_storage_rejects_missing_or_invalid_required_integer(method, args, row):
    from tempo_storage.queue_refresh.application import requeue
    class Database:
        def execute(self, *_):
            return type("Cursor", (), {"fetchone": lambda self: row})()
    with pytest.raises(TypeError):
        getattr(requeue, method)(Database(), *args)


def test_requeue_storage_cycle_includes_origins_and_returns_inserted_identity():
    from tempo_storage.queue_refresh.application import requeue
    calls, values = [], iter([(8,), (52,)])
    class Database:
        def execute(self, sql, parameters):
            calls.append((sql, parameters))
            return type("Cursor", (), {"fetchone": lambda self: next(values)})()
    database = Database()
    assert requeue.next_cycle(database, "day", "card") == 8
    assert requeue.append_attempt(database, "day", "card", 8, 3, "guided") == 52
    assert "UNION ALL SELECT cycle FROM queue_attempt_origins" in calls[0][0]
    assert calls[0][1] == ("day", "card", "day", "card")
    assert calls[1][0].endswith("RETURNING id")
    assert calls[1][1] == ("day", "card", 8, 3, "guided")


def test_queue_command_policy_has_no_sql_or_driver_operations():
    for node in ast.walk(ast.parse(Path(queue_commands.__file__).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "execute",
                "execute_native",
                "executemany",
                "postgres_sql",
            }
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not any(
                sql in node.value.upper()
                for sql in (
                    "SELECT ",
                    "INSERT INTO ",
                    "UPDATE DAILY_QUEUE ",
                    "DELETE FROM ",
                )
            )


def test_availability_projection_is_captured_before_each_date_read(monkeypatch):
    events, reads = [], []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters):
            if statement.startswith("SELECT queue_date FROM queue_attempt_origins"):
                origin_reads.append((statement, parameters))
                events.append("origin")
                return Cursor(None)
            if statement.startswith("SELECT 1 FROM queue_attempt_origins"):
                origin_reads.append((statement, parameters))
                events.append("origin")
                return Cursor(None)
            reads.append((statement, parameters))
            events.append("read")
            return Cursor({"id": 7})

    def builder(reason):
        assert reason == queue_commands.queue_application.MISS_REASON
        events.append("capture")
        return f"projection-{len(reads)}"

    def current_day(_database):
        events.append("date")
        return "day"

    origin_reads = []
    monkeypatch.setattr(storage, "available_attempt_sql", builder)
    monkeypatch.setattr(queue_commands, "current_study_day", current_day)
    monkeypatch.setattr(
        queue_attempt_origins,
        "validate_failure_marker",
        lambda *args: events.append("marker"),
    )
    queue_commands.validate_available_attempt(Database(), 7, "card", 3)
    assert events == [
        "capture",
        "origin",
        "date",
        "read",
        "marker",
        "capture",
        "read",
        "origin",
    ]
    assert reads[0][0].startswith("projection-0") and reads[0][1] == ("day", 7)
    assert reads[1][0].startswith("projection-1") and reads[1][1] == ("day",)
    assert [parameters for _, parameters in origin_reads] == [
        (7, "card", 3),
        (7, "card", 3),
    ]
    assert origin_reads[0][0].startswith("SELECT queue_date FROM queue_attempt_origins")
    assert origin_reads[1][0].startswith("SELECT 1 FROM queue_attempt_origins")


def test_queue_warming_captures_compiler_before_completion_callback(monkeypatch):
    events = []
    monkeypatch.setattr(
        postgres,
        "postgres_sql",
        lambda statement: events.append(("compile", statement)),
    )

    def warm_completion():
        events.append(("completion", None))
        monkeypatch.setattr(
            postgres,
            "postgres_sql",
            lambda *_: events.append(("late compiler", None)),
        )

    monkeypatch.setattr(durable_tasks, "warm_completion_sql", warm_completion)
    queue_commands.warm_queue_refresh_sql()
    assert events == [
        ("completion", None),
        ("compile", queue_commands._QUEUE_REFRESH_PROJECTION_SQL),
    ]


def test_absent_queue_task_is_advisory_locked_then_rechecked_before_projection(
    monkeypatch,
):
    events = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters=()):
            events.append(("read", statement, parameters))
            count = sum(event[0] == "read" for event in events)
            if count == 1:
                return Cursor(None)
            if count == 2:
                return Cursor({"id": "task", "payload_json": '{"queue_date":"2026-10-21"}'})
            return Cursor(
                {
                    "state": "ready",
                    "refresh_pending": 0,
                    "has_queued_entries": True,
                    "presentation_membership_hash": PRESENTATION_VERSION + ":receipt",
                }
            )

        def execute_native(self, statement, parameters):
            events.append(("advisory", statement, parameters))

    monkeypatch.setattr(storage, "PostgresConnection", Database)
    assert queue_commands.ensure_current_queue(
        Database(), {"queue_date": "2026-10-21"}
    ) == {
        "queue_date": "2026-10-21",
        "refresh_pending": False,
    }
    assert [event[0] for event in events] == ["read", "advisory", "read", "read"]
    assert events[0][1] == events[2][1] and events[0][2] == events[2][2] == ()
    assert events[1][2] == ("tempo:daily-queue:current:ensure",)
    assert events[-1][1].endswith(" FOR UPDATE")
    assert events[-1][2] == ("2026-10-21",)


@pytest.mark.parametrize(
    "receipt,queued,ready",
    [
        (PRESENTATION_VERSION + ":receipt", True, True),
        ("queue-presentation-v2:receipt", True, False),
        (None, True, False),
        ("unmixed:" + PRESENTATION_VERSION + ":receipt", True, False),
        (None, False, True),
        ("queue-presentation-v2:receipt", False, True),
    ],
)
def test_ready_day_version_requests_refresh_only_for_nonempty_stale_presentation(
    monkeypatch,
    receipt,
    queued,
    ready,
):
    calls = []
    monkeypatch.setattr(storage, "lock_current_queue_task", lambda *_: None)
    monkeypatch.setattr(
        storage,
        "current_projection",
        lambda *_: dict(
            state="ready",
            refresh_pending=0,
            has_queued_entries=queued,
            presentation_membership_hash=receipt,
        ),
    )
    monkeypatch.setattr(
        queue_commands,
        "request_queue_refresh_in_transaction",
        lambda _db, day: calls.append(day) or dict(id="refresh"),
    )
    result = queue_commands.ensure_current_queue(None, {"queue_date": "2026-10-21"})
    assert result["refresh_pending"] is not ready
    assert calls == ([] if ready else ["2026-10-21"])


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("receipt", [None, "saved-receipt"])
def test_internal_readiness_queries_return_day_receipt_and_queued_presence(
    queued, receipt
):
    import sqlite3
    from tempo_storage.lifecycle import refresh_requests

    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE queue_projections(queue_date TEXT, state TEXT, refresh_pending INT);
            CREATE TABLE daily_queue_days(queue_date TEXT PRIMARY KEY, membership_hash TEXT);
            CREATE TABLE daily_queue(queue_date TEXT, status TEXT);
            INSERT INTO queue_projections VALUES('2026-10-21','ready',0);
            INSERT INTO daily_queue_days VALUES('other','other-receipt');
            INSERT INTO daily_queue VALUES('other','queued');
            INSERT INTO daily_queue VALUES('2026-10-21','complete');
        """)
        if receipt:
            database.execute(
                "INSERT INTO daily_queue_days VALUES(?,?)", ("2026-10-21", receipt)
            )
        if queued:
            database.execute(
                "INSERT INTO daily_queue VALUES(?,'queued')", ("2026-10-21",)
            )
        projections = [
            storage.current_projection(database, "2026-10-21", False),
            refresh_requests.daily_queue_projection(database, "2026-10-21"),
        ]
        for projection in projections:
            assert dict(projection) == dict(
                state="ready",
                refresh_pending=0,
                presentation_membership_hash=receipt,
                has_queued_entries=int(queued),
            )


@pytest.mark.parametrize(
    "dispositions,expected",
    [
        (["legacy", "legacy"], ["owners", "owner:a", "owner:b", "card", "day"]),
        (["legacy", "position"], ["owners", "owner:a", "owner:b"]),
        (["hold", "legacy"], ["owners", "owner:a"]),
    ],
)
def test_legacy_issuance_owner_facts_are_fenced_before_card_locks(
    monkeypatch, dispositions, expected
):
    from types import SimpleNamespace
    from tempo_storage.study import position_owner_epoch

    events, reads = [], []
    rows = [{"repertoire_id": "a"}, {"repertoire_id": "b"}]

    class Database:
        def execute_native(self, statement, parameters):
            events.append("owners")
            reads.append((statement, parameters))
            return SimpleNamespace(fetchall=lambda: rows)

    database = Database()
    payload = dict(
        queue_date="2026-11-02", entry_id=7, card_id="card", expected_revision=3
    )
    monkeypatch.setattr(
        queue_commands, "current_study_day", lambda db: payload["queue_date"]
    )
    monkeypatch.setattr(storage, "is_postgres_connection", lambda db: db is database)

    def owner_mode(db, repertoire_id, epoch):
        assert db is database and epoch is None
        events.append("owner:" + repertoire_id)
        return SimpleNamespace(
            disposition=dispositions[rows.index({"repertoire_id": repertoire_id})],
            reason="owner-unavailable",
        )

    monkeypatch.setattr(position_owner_epoch, "owner_mode", owner_mode)
    monkeypatch.setattr(
        queue_commands, "_lock_attempt_card", lambda db, entry: events.append("card")
    )
    monkeypatch.setattr(
        queue_commands,
        "lock_queue_date_for_position",
        lambda db, day: events.append("day"),
    )
    monkeypatch.setattr(queue_commands.queue_storage, "retired_issuance_target", lambda *args: False)
    monkeypatch.setattr(storage, "issued_origin", lambda *args: True)
    monkeypatch.setattr(storage, "prepare_queue_head", lambda *args: lambda day: None)
    result = queue_commands.issue_queue_head(database, payload)
    assert events == expected
    assert len(reads) == 1
    assert reads[0][1] == (7, "card", 3) * 3
    assert reads[0][0].endswith("ORDER BY r.repertoire_id")
    if dispositions == ["legacy", "legacy"]:
        assert result == {"issued": False}
    else:
        assert result == dict(
            issued=False,
            position_study_mode="position",
            position_presentation=None,
            position_study_hold="owner-unavailable",
        )


def test_backward_day_locks_existing_day_task_before_its_projection():
    events = []
    class Cursor:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value
    class Database:
        def execute(self, statement, parameters=()):
            events.append((statement, parameters))
            if "deduplication_key='current'" in statement:
                return Cursor({"id":"old", "payload_json":'{"queue_date":"2026-11-12"}'})
            return Cursor({"id":"new", "payload_json":'{"queue_date":"2026-11-11"}'})
    task = storage.lock_current_queue_task(Database(), "2026-11-11")
    assert task["id"] == "new"
    assert len(events) == 2 and all("FOR UPDATE" in event[0] for event in events)
    assert events[1][1] == ("current-day:2026-11-11",)
