"""Six-table preliminary reader against an invocation-owned empty PostgreSQL DB."""
import hashlib
import os
from pathlib import Path
import secrets
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'database'))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from tempo_storage.postgres import PostgresConnection, tempo_row_factory
from tempo_storage.tactics.row_snapshot import read_tactics_row_snapshot


def _migrate(raw):
    from tooling import apply_postgres_migrations as migration
    assert raw.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')").fetchone()[0] == 0
    paths = sorted(migration.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
    migration.validate_migration_history(paths, [], expected_version=migration.POSTGRES_SCHEMA_VERSION)
    assert len(paths) == migration.POSTGRES_SCHEMA_VERSION
    for path in paths:
        raw.execute(migration.migration_source(path), prepare=False)
        assert raw.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == int(path.name[:3])


def test_tactics_preliminary_rows_readonly_repeatable():
    admin = os.environ.get('TEMPO_LEARNING_TEST_ADMIN')
    assert admin and os.environ.get('TEMPO_LEARNING_TEST_FIXTURE', '').startswith('tempo-learning-')
    params = conninfo_to_dict(admin)
    assert params.get('host') in ('127.0.0.1', 'localhost') and params.get('dbname') == 'postgres'
    name = 'tempo_position_test_n2_' + secrets.token_hex(12)
    created = False
    with psycopg.connect(admin, autocommit=True) as conn:
        assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
        for role in ('tempo_reader', 'tempo_writer'):
            assert not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(params['password'])))
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
    dsn = make_conninfo(admin, dbname=name)
    try:
        with psycopg.connect(dsn) as raw:
            _migrate(raw)
            raw.execute("INSERT INTO tactic_progress VALUES ('orphan','old-deck',NULL,' clock ',NULL,'light')")
            raw.execute("INSERT INTO tactic_pack_activation VALUES ('old-pack',1)")
            raw.execute("INSERT INTO tactic_rotation VALUES (1,'old-pack') ON CONFLICT (id) DO UPDATE SET last_pack_id=excluded.last_pack_id")
            raw.execute("INSERT INTO tactic_introductions VALUES ('orphan','old-pack','day')")
            raw.execute("INSERT INTO tactic_discovery_attempts VALUES ('attempt','old-deck','orphan',1,%s)", (' { "result": null } ',))
            # A real capture foreign key requires a synthetic card; it remains outside the reader.
            raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('fixture','fixture','fixture','now')")
            raw.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES ('card','fixture','checkpoint','fen','[]','day')")
            raw.execute("INSERT INTO tactic_captures VALUES ('capture','card','manual',NULL,'','note',' clock ',%s,%s)", (' { "request": null } ', ' { "result": 1 } '))
        def read(scope, **limits):
            return read_tactics_row_snapshot(scope, max_rows=limits.get('rows', 100), max_text_bytes=limits.get('text', 10000))
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
            raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            scope = PostgresConnection(raw, read_only=True)
            first = read(scope)
            assert first.status == 'PRELIMINARY_UNBOUND'
            assert first.asset_authority == 'UNPROVEN' and first.browser_authority == 'UNRECONCILED'
            assert first.writer_fence is first.final_watermark is None
            assert first.tactic_progress[0].card_id is None and first.tactic_progress[0].clean_pass_at == ' clock '
            assert first.tactic_discovery_attempts[0].id == 'attempt' and first.tactic_discovery_attempts[0].result_json == ' { "result": null } '
            assert first.tactic_captures[0].id == 'capture' and first.tactic_captures[0].source_ref is None
            assert first.tactic_captures[0].source_url == '' and first.tactic_captures[0].request_json == ' { "request": null } '
            assert read(scope, rows=1).code == 'ROW_LIMIT_EXCEEDED'
            assert read(scope, text=1).code == 'TEXT_LIMIT_EXCEEDED'
            with psycopg.connect(dsn) as writer:
                writer.execute("UPDATE tactic_progress SET deck_id='new' WHERE puzzle_id='orphan'")
                writer.execute("UPDATE tactic_pack_activation SET active=0 WHERE pack_id='old-pack'")
            assert read(scope) == first and scope.in_transaction
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
            raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            newer = read(PostgresConnection(raw, read_only=True))
            assert newer.tactic_progress[0].deck_id == 'new' and newer.tactic_pack_activation[0].active == 0
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
            raw.execute('SELECT 1')
            assert read(PostgresConnection(raw)).code == 'ACTIVE_READ_ONLY_TRANSACTION_REQUIRED'
            assert read(PostgresConnection(raw, read_only=True)).code == 'READ_ONLY_REPEATABLE_READ_PUBLIC_REQUIRED'
        with psycopg.connect(dsn) as raw:
            for table in ('tactic_captures', 'tactic_discovery_attempts', 'tactic_introductions', 'tactic_rotation', 'tactic_pack_activation', 'tactic_progress'):
                raw.execute(f'DELETE FROM {table}')
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
            raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            empty = read(PostgresConnection(raw, read_only=True))
            assert all(getattr(empty, table) == () for table in empty.scope_tables)
    finally:
        if created:
            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
                assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
