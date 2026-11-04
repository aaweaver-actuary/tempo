"""LP07: native original-response immutability on a disposable N2 database."""
import hashlib
import os
from pathlib import Path
import secrets
import sys
import threading

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
for directory in [ROOT, ROOT / 'backend', ROOT / 'database', *ROOT.glob('packages/*/src'),
                  *ROOT.glob('services/*/src'), *ROOT.glob('services/learning/*/src'),
                  *ROOT.glob('contracts/*/src'), *ROOT.glob('services/learning/runtime/.venv/lib/python*/site-packages')]:
    sys.path.insert(0, str(directory))
from database.tests.position_study import acceptance
from tempo_legacy_database.adapters.postgres_store import tempo_row_factory


def test_lp07_original_response_native_migration_and_immutability(monkeypatch):
    admin = os.environ.get('TEMPO_LEARNING_TEST_ADMIN')
    assert admin and os.environ.get('TEMPO_LEARNING_TEST_FIXTURE', '').startswith('tempo-learning-')
    params = conninfo_to_dict(admin)
    assert params.get('host') in ('127.0.0.1', 'localhost') and params.get('dbname') == 'postgres'
    name = 'tempo_position_test_n2_' + secrets.token_hex(12)
    with psycopg.connect(admin, autocommit=True) as conn:
        assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
        for role in ('tempo_reader', 'tempo_writer'):
            assert not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(params['password'])))
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(admin, dbname=name)
    monkeypatch.setenv('TEMPO_POSITION_N2_DSN', dsn)
    from tooling import apply_postgres_migrations as migration
    def migrate_to_59(raw):
        assert raw.execute("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')").fetchone()[0] == 0
        paths = sorted(migration.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
        migration.validate_migration_history(paths, [], expected_version=60)
        for path in paths[:-1]:
            registration = migration.MODULE_MIGRATIONS.get(path.name)
            if registration:
                source = (ROOT / 'database' / registration[2]).read_bytes()
                assert hashlib.sha256(source).hexdigest() == registration[3]
                raw.execute(source.decode(), prepare=False)
            raw.execute(path.read_text(), prepare=False)
        assert raw.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == 59
    monkeypatch.setattr(acceptance, '_migrate', migrate_to_59)
    journey = acceptance._real_review_journey
    def run_checks(raw, port, dsn, monkeypatch, plan, entry, card_id, fsrs):
        from app.services import postgres_opening_evidence as source
        journey(raw, port, dsn, monkeypatch, plan, entry, card_id, fsrs)
        before = [dict(row) for row in raw.execute('SELECT * FROM study_position_first_responses ORDER BY decision_index').fetchall()]
        assert len(before) >= 2
        raw.commit()
        path = migration.MIGRATIONS / '060_position_first_response_immutable.sql'
        with pytest.raises(psycopg.errors.DivisionByZero):
            with raw.transaction():
                raw.execute(path.read_text() + '\nSELECT 1/0;', prepare=False)
        assert raw.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == 59
        assert raw.execute("SELECT to_regprocedure('public.reject_position_first_response_mutation()')").fetchone()[0] is None
        raw.commit()
        migration.apply_migrations(dsn)
        assert raw.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == 60
        assert [dict(row) for row in raw.execute('SELECT * FROM study_position_first_responses ORDER BY decision_index').fetchall()] == before
        raw.commit()
        first = before[0]
        calls = []
        raw.execute('BEGIN')
        retry = source.persist_position_first_response(port, plan, first['payload_json'], first['accepted_at'],
            apply_grade=lambda *args: calls.append(args))
        assert retry['event_id'] == first['event_id'] and retry['accepted_at'] == first['accepted_at'] and not calls
        raw.rollback()
        changed = dict(first['payload_json'], assistance_before=not first['payload_json']['assistance_before'])
        with pytest.raises(source.EvidenceConflict):
            source.persist_position_first_response(port, plan, changed, first['accepted_at'], apply_grade=lambda *args: calls.append(args))
        raw.rollback()
        columns = ('event_id','attempt_id','decision_index','repertoire_id','variant','position','owner_epoch',
                   'payload_digest','payload_json','observed_at','accepted_at')
        for column in columns:
            with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
                raw.execute(sql.SQL('UPDATE study_position_first_responses SET {c}={c} WHERE event_id=%s').format(c=sql.Identifier(column)), (first['event_id'],))
            raw.rollback()
        for statement in ('DELETE FROM study_position_first_responses', 'TRUNCATE study_position_first_responses'):
            with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
                raw.execute(statement)
            raw.rollback()
        assert [dict(row) for row in raw.execute('SELECT * FROM study_position_first_responses ORDER BY decision_index').fetchall()] == before
        raw.commit()
        with psycopg.connect(dsn) as holder:
            holder.execute('SELECT 1 FROM study_position_first_responses WHERE event_id=%s FOR UPDATE', (first['event_id'],))
            with psycopg.connect(dsn) as waiter:
                waiter.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    waiter.execute('UPDATE study_position_first_responses SET event_id=event_id WHERE event_id=%s', (first['event_id'],))
        with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
            raw.execute('UPDATE study_position_first_responses SET event_id=event_id WHERE event_id=%s', (first['event_id'],))
        raw.rollback()
        assert [dict(row) for row in raw.execute('SELECT * FROM study_position_first_responses ORDER BY decision_index').fetchall()] == before
        raw.commit()
    monkeypatch.setattr(acceptance, '_real_review_journey', run_checks)
    try:
        acceptance._run_schema_journey(monkeypatch, branches=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
            for role in ('tempo_reader', 'tempo_writer'):
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
