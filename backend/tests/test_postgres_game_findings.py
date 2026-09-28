"""Regressions for separating finding preparation from database publication."""

from __future__ import annotations

from contextlib import contextmanager

from app.services import game_findings


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
