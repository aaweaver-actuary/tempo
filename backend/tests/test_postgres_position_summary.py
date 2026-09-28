"""Optional repertoire filtering remains typed under the PostgreSQL reader."""

from contextlib import contextmanager

from app import main, postgres_store


class Cursor:
    def fetchall(self):
        return []


def test_postgres_position_summary_types_optional_repertoire_filter(monkeypatch):
    statements = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            return Cursor()

    @contextmanager
    def connection():
        yield Database()

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "connection", connection)
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    assert main.game_position_summary(fen)["encounters"] == 0
    assert "%s::text IS NULL" in statements[0][0]
    assert statements[0][1][1:] == (None, None)
    assert main.game_position_summary(fen, "opening-one")["encounters"] == 0
    assert statements[1][1][1:] == ("opening-one", "opening-one")
