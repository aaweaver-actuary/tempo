"""Bounded-reader relocation and preserved queue/presentation query boundaries."""

import ast
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from tempo_storage.queue_refresh import reads

from app.services import queue_application


def test_queue_refresh_and_capacity_keep_all_sql_and_driver_mechanics_in_root_database():
    base = Path(__file__).resolve().parents[1] / "app/services"
    for filename in ("postgres_queue_refresh.py", "opening_capacity_admission.py"):
        tree = ast.parse((base / filename).read_text())
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {"execute", "execute_native", "executemany", "cursor", "configured"}
        ], filename
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.match(r"^(SELECT|INSERT|UPDATE|DELETE|WITH|FROM)\b", node.value)
        ], filename
    tree = ast.parse((base / "queue_application.py").read_text())
    presentation = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "presentation_rows"
    )
    assert not [
        node
        for node in ast.walk(presentation)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {"execute", "execute_native", "executemany", "cursor", "configured"}
    ]


@pytest.mark.parametrize("frozen_active", [False, True])
def test_study_query_preparation_captures_route_identity_before_read_callbacks(
    monkeypatch, frozen_active
):
    statements = []
    read_last, read_page = reads.study_opening_queries(frozen_active)
    monkeypatch.setattr(reads, "_STUDY_OPENING_ROUTES", "poisoned-later-route")
    monkeypatch.setattr(reads, "_STUDY_OPENING_ORDER", "poisoned-later-order")

    class Reader:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            return self

        def fetchall(self):
            return []

    @contextmanager
    def snapshot(*, repertoire_snapshot=False):
        assert repertoire_snapshot
        yield Reader()

    monkeypatch.setattr(reads, "read_connection", snapshot)
    assert read_last() == []
    size = 7 if frozen_active else 6
    after, upper = tuple(range(size)), tuple(range(10, 10 + size))
    assert read_page(after, upper, 16) == []
    assert len(statements) == 2
    assert all("poisoned-later" not in statement for statement, _ in statements)
    assert statements[1][1] == (*after, *upper, 16)
    assert statements[1][0].count("%s") == 2 * size + 1


@pytest.mark.parametrize("frozen_active", [False, True])
def test_ordered_opening_occurrences_use_closed_snapshot_readers_with_original_cursors(
    monkeypatch, frozen_active
):
    calls = []

    class Reader:
        def execute_native(self, statement, parameters):
            calls.append(("query", statement, parameters))
            return self

        def fetchall(self):
            return [("result",)]

    @contextmanager
    def read_connection(*, repertoire_snapshot=False):
        assert repertoire_snapshot is True
        calls.append(("open",))
        yield Reader()
        calls.append(("close",))

    monkeypatch.setattr(reads, "read_connection", read_connection, raising=False)
    monkeypatch.setattr(
        reads,
        "_bounded_read",
        lambda *args, **kwargs: pytest.fail("occurrences must use snapshot reader"),
    )
    last, page = reads.study_opening_queries(frozen_active)
    size = 7 if frozen_active else 6
    after, upper = tuple(range(size)), tuple(range(10, 10 + size))
    assert last() == [("result",)]
    assert page(after, upper, 16) == [("result",)]
    assert [call[0] for call in calls] == [
        "open",
        "query",
        "close",
        "open",
        "query",
        "close",
    ]
    assert calls[1][1].endswith(" DESC LIMIT 1") and calls[1][2] == ()
    assert calls[4][1].endswith(" LIMIT %s")
    assert calls[4][2] == (*after, *upper, 16)
    assert calls[4][1].count("%s") == 2 * size + 1


def test_empty_presentation_keeps_single_read_without_review_or_eligibility_queries(
    monkeypatch,
):
    statements = []
    monkeypatch.setattr(reads.postgres, "configured", lambda: False)

    def read(statement, parameters):
        statements.append((statement, parameters))
        return []

    assert (
        queue_application.presentation_rows(
            None, "2026-10-21", read=read, after_entry_id=7, limit=16
        )
        == []
    )
    assert len(statements) == 1
    assert statements[0][1] == ("2026-10-21", 7, 16)


def _priority_presentation_row(number, reason, **values):
    return dict(
        id=number,
        card_id=f"card-{number}",
        position=number,
        content_type="opening",
        admission_kind=values.get("admission_kind", "new"),
        gameplay_priority_reason=reason,
        cycle=values.get("cycle", 0),
        attempt_state="clean",
        attempt_failed=False,
        review_completed=False,
        revision=1,
        issued=False,
        presented=False,
        taught=False,
    )


def _presentation_reader(rows, *, reviewed=(), eligible=()):
    calls = []

    def read(statement, parameters=()):
        assert not re.match(r"\s*(INSERT|UPDATE|DELETE)\b", statement, re.IGNORECASE)
        calls.append((statement, parameters))
        if statement.startswith("SELECT q.id,q.card_id"):
            return rows
        if statement.startswith("SELECT DISTINCT card_id FROM reviews"):
            return [(identity,) for identity in reviewed]
        if statement.startswith(
            "SELECT presentation_id FROM study_recombination_presentations"
        ):
            return []
        return [(identity,) for identity in eligible]

    return read, calls


@pytest.mark.parametrize("reason", [None, "historical-priority"])
def test_nonpriority_presentation_keeps_row_and_inerts_historical_priority(
    monkeypatch, reason
):
    monkeypatch.setattr(reads.postgres, "configured", lambda: False)
    rows = [_priority_presentation_row(1, reason)]
    reader, calls = _presentation_reader(rows, eligible=(1,))
    result = queue_application.presentation_rows(
        None, "2026-10-28", read=reader, limit=16
    )
    assert result[0]["gameplay_priority_reason"] is None and result[0]["eligible"]
    assert not any("FROM study_game_feedback" in sql for sql, _ in calls)


def test_bounded_presentation_preserves_order_protection_and_ordinary_admissions(
    monkeypatch,
):
    monkeypatch.setattr(reads.postgres, "configured", lambda: False)
    rows = [
        _priority_presentation_row(1, None),
        _priority_presentation_row(2, "historical-priority", cycle=1),
        _priority_presentation_row(3, None, admission_kind="explicit"),
    ]
    rows[0]["issued"] = True
    rows[1]["presented"] = True
    reader, calls = _presentation_reader(rows, reviewed=("card-1",), eligible=(1, 2))
    result = queue_application.presentation_rows(
        None, "2026-10-28", read=reader, after_entry_id=0, limit=16
    )
    assert [(row["id"], row["card_id"], row["position"]) for row in result] == [
        (1, "card-1", 1),
        (2, "card-2", 2),
        (3, "card-3", 3),
    ]
    assert [row["admission_kind"] for row in result] == ["review", "review", "explicit"]
    assert [row["eligible"] for row in result] == [True, True, False]
    assert result[0]["issued"] and result[1]["presented"]
    assert all(row["gameplay_priority_reason"] is None for row in result)
    assert len(calls) == 4 and all("study_game_feedback" not in sql for sql, _ in calls)
    assert calls[0][1] == ("2026-10-28", 0, 16)


def test_presentation_protection_sets_match_correlated_facts_with_historical_and_null_evidence(
    monkeypatch,
):
    import sqlite3

    from tempo_storage.queue_refresh import presentation

    monkeypatch.setattr(presentation.postgres, "configured", lambda: True)
    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY, content_type TEXT, revision INT);
            CREATE TABLE daily_queue(id INT PRIMARY KEY, card_id TEXT, position INT, queue_date TEXT,
                status TEXT, admission_kind TEXT, gameplay_priority_reason TEXT, cycle INT,
                attempt_state TEXT, attempt_failed INT, review_result_json TEXT);
            CREATE TABLE queue_attempt_origins(queue_entry_id INT,card_id TEXT,revision INT,issued_as_head INT);
            CREATE TABLE opening_evidence_attempts(queue_entry_id INT);
            CREATE TABLE teaching_states(card_id TEXT);
        """)
        database.executemany(
            "INSERT INTO cards VALUES(?,'opening',2)",
            [(f"card-{n}",) for n in range(575)],
        )
        database.executemany(
            "INSERT INTO daily_queue VALUES(?,?,?,'today','queued','new',NULL,0,'clean',0,NULL)",
            [(n, f"card-{n}", n) for n in range(575)],
        )
        database.executemany(
            "INSERT INTO opening_evidence_attempts VALUES(?)",
            [(None,)] + [(-n,) for n in range(1, 5312)] + [(3,)],
        )
        database.executemany(
            "INSERT INTO queue_attempt_origins VALUES(?,?,?,?)",
            [
                (1, "card-1", 2, 1),
                (2, "card-2", 1, 1),
                (3, "wrong-card", 2, 1),
                (4, "card-4", 2, 0),
                (None, "card-5", 2, 1),
            ],
        )
        database.execute("INSERT INTO teaching_states VALUES('card-7')")
        expected = [
            tuple(row)
            for row in database.execute("""
            SELECT q.id,
                EXISTS(SELECT 1 FROM queue_attempt_origins o WHERE o.queue_entry_id=q.id
                       AND o.card_id=c.id AND o.revision=c.revision AND o.issued_as_head=1),
                EXISTS(SELECT 1 FROM opening_evidence_attempts a WHERE a.queue_entry_id=q.id),
                EXISTS(SELECT 1 FROM teaching_states t WHERE t.card_id=c.id)
            FROM daily_queue q JOIN cards c ON c.id=q.card_id ORDER BY q.id
        """)
        ]

        def read(statement, parameters):
            if statement.startswith("SELECT q.id,q.card_id"):
                return database.execute(
                    statement.replace("%s", "?"), parameters
                ).fetchall()
            if statement.startswith(
                "SELECT DISTINCT card_id FROM reviews"
            ) or statement.startswith(
                "SELECT presentation_id FROM study_recombination_presentations"
            ):
                return []
            return [(n,) for n in range(575)]

        rows, reviewed, eligible = presentation.presentation_facts(
            None, "today", "miss", read=read
        )
        assert [
            (r["id"], r["issued"], r["presented"], r["taught"]) for r in rows
        ] == expected
        assert {r["id"] for r in rows if r["issued"]} == {1}
        assert {r["id"] for r in rows if r["presented"]} == {3}
        assert {r["id"] for r in rows if r["taught"]} == {7}
        assert all(
            r[field] in (0, 1)
            for r in rows
            for field in ("issued", "presented", "taught")
        )
        assert len(rows) == len(eligible) == 575 and reviewed == set()
