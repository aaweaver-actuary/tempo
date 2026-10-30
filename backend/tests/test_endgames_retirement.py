"""Retired-family compatibility upgrades never erase unrelated study data."""
from pathlib import Path
import runpy
import sqlite3
import pytest
from tempo_legacy_database import connections
from tempo_legacy_database.ddl.retired_practice_schema import retire_endgames

PROOF = runpy.run_path(str(Path(__file__).resolve().parents[2] / "database/tests/endgames_retirement.py"))


def test_endgames_sqlite_upgrade_deletes_only_retired_family_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(connections, "DB_PATH", tmp_path / "retirement.sqlite")
    connections.initialize()
    with connections.connection() as database:
        before = PROOF["seed_sqlite_retirement"](database)
    connections.initialize()
    connections.initialize()
    with connections.connection() as database:
        PROOF["assert_sqlite_kept_state"](database, before)
        assert database.execute("SELECT COUNT(*) FROM cards WHERE content_type='endgame'").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id='retired'").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM deleted_cards WHERE card_id='retired'").fetchone()[0] == 1
        for table in ('endgame_templates', 'endgame_attempts', 'tablebase_cache'):
            assert database.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
        assert 'draw_hold_user_moves' not in {row[1] for row in database.execute('PRAGMA table_info(settings)')}
        with pytest.raises(sqlite3.IntegrityError, match='Practice family retired'):
            database.execute("UPDATE cards SET content_type='endgame' WHERE id='kept'")


def test_endgames_sqlite_retirement_rejects_mixed_ownership_without_partial_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(connections, "DB_PATH", tmp_path / "retirement.sqlite")
    connections.initialize()
    with connections.connection() as database:
        before = PROOF["seed_sqlite_retirement"](database, conflict=True)
    with pytest.raises(ValueError, match='another practice family'):
        with connections.connection() as database:
            retire_endgames(database)
    with connections.read_connection() as database:
        PROOF["assert_sqlite_kept_state"](database, before)
        assert database.execute("SELECT COUNT(*) FROM cards WHERE id='retired'").fetchone()[0] == 1
