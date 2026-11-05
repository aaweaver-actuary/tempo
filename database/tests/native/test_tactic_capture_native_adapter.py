"""Run the existing capture proof on the fixture-owned disposable database."""
import psycopg
import sys
from psycopg.conninfo import make_conninfo

from test_product_introductions_postgres import owned_postgres  # noqa: F401


def test_native_tactic_capture_contention_replay_and_rollback(owned_postgres, monkeypatch):
    dsn = make_conninfo(**owned_postgres)
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', dsn)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', dsn)
    from database.tooling.apply_postgres_migrations import apply_migrations
    from tempo_legacy_database.adapters import postgres_store
    apply_migrations(dsn, defense_zero=True)
    with psycopg.connect(dsn) as connection:
        connection.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        connection.execute('INSERT INTO tactic_rotation(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
    original_path = sys.path[:]
    try:
        from database.tests.check_postgres_tactic_capture import main
        main(dsn)
    finally:
        sys.path[:] = original_path
        postgres_store.close_pools()
