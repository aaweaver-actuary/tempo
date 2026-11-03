"""Explicit empty, task-owned PostgreSQL schema-56 compatibility proof."""
import hashlib
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_schema56_readers_fail_closed_without_abandonment_table():
    from psycopg.conninfo import conninfo_to_dict
    dsn = os.environ.get('TEMPO_POSITION_ABANDON_56_DSN')
    if not dsn:
        pytest.skip('requires explicit empty task-owned TEMPO_POSITION_ABANDON_56_DSN')
    params = conninfo_to_dict(dsn)
    if params.get('host') not in ('127.0.0.1', 'localhost') or not params.get('dbname', '').startswith('tempo_position_test_abandon_56'):
        raise RuntimeError('refusing nonlocal or non-abandonment test database')
    import psycopg
    from tooling import apply_postgres_migrations as migrations
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    from tempo_legacy_database.adapters.lifecycle import queue_attempt_abandonment as abandonment
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        assert raw.execute("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')").fetchone()[0] == 0
        paths = sorted(migrations.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
        for path in paths:
            if int(path.name[:3]) > 56:
                break
            registration = migrations.MODULE_MIGRATIONS.get(path.name)
            if registration:
                source = (ROOT / 'database' / registration[2]).read_bytes()
                assert hashlib.sha256(source).hexdigest() == registration[3]
                raw.execute(source.decode(), prepare=False)
            raw.execute(path.read_text(), prepare=False)
        database = PostgresConnection(raw)
        assert not abandonment.installed(database)
        assert not abandonment.is_abandoned(database, 13624, 'unknown', 1)
        with pytest.raises(ValueError, match='schema_unavailable'):
            abandonment.plan_in_writer(database, {'queue_entry_id': 13624, 'card_id': 'unknown',
                'revision': 1, 'operation_id': 'test', 'actor': 'test', 'reason': 'test'})
