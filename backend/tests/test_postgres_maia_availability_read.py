"""A foreground availability GET must not wait behind its own lease."""

from contextlib import contextmanager

from app import main, postgres_store
from app.services.activity_gate import activity_gate


class Cursor:
    def fetchone(self):
        return (1,)


class Database:
    def execute_native(self, _statement, _parameters=()):
        return Cursor()


def test_postgres_maia_availability_get_uses_foreground_reader(monkeypatch):
    calls = []

    @contextmanager
    def foreground_reader():
        calls.append("foreground")
        yield Database()

    @contextmanager
    def background_reader():
        calls.append("background")
        yield Database()

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "read_connection", foreground_reader)
    monkeypatch.setattr(main, "background_read_connection", background_reader)
    with activity_gate.foreground():
        assert main.coverage_maia_available() == {"available": True}
    assert calls == ["foreground"]
    with activity_gate.background_request():
        assert main.coverage_maia_available() == {"available": True}
    assert calls == ["foreground", "background"]
