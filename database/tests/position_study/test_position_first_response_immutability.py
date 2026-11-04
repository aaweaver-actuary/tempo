"""LP07: native original-response immutability on a disposable N2 database."""
import hashlib
import os
from pathlib import Path
import secrets
import sys
import json

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
for directory in [ROOT, ROOT / 'backend', ROOT / 'database', *ROOT.glob('packages/*/src'),
                  *ROOT.glob('services/*/src'), *ROOT.glob('services/learning/*/src'),
                  *ROOT.glob('contracts/*/src')]:
    sys.path.insert(0, str(directory))
sys.path.extend(str(path) for path in ROOT.glob('services/learning/runtime/.venv/lib/python*/site-packages'))
from database.tests.position_study import acceptance
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory


def test_lp07_original_response_native_migration_and_immutability(monkeypatch):
    from app.services import queue_next_card
    with queue_next_card._source_cache_lock:
        prior_cache = {key: value for key, value in queue_next_card._source_cache.items() if key[0] == 'n2'}
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
    empty_name = 'tempo_position_test_n2_' + secrets.token_hex(12)
    with psycopg.connect(admin, autocommit=True) as conn:
        assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (empty_name,)).fetchone()
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(empty_name)))
    try:
        empty_dsn = make_conninfo(admin, dbname=empty_name)
        migration.apply_migrations(empty_dsn)
        with psycopg.connect(empty_dsn) as empty:
            assert empty.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == 60
            assert empty.execute('SELECT count(*) FROM study_position_first_responses').fetchone()[0] == 0
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(empty_name)))
            assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (empty_name,)).fetchone()
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
        # Simulate pre-existing corrupt legacy evidence before the new migration.
        second = raw.execute('SELECT event_id FROM study_position_first_responses ORDER BY decision_index OFFSET 1 LIMIT 1').fetchone()
        assert second is not None
        raw.execute("UPDATE study_position_first_responses SET payload_json='{}'::jsonb WHERE event_id=%s", (second['event_id'],))
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
        with pytest.raises(ValueError):
            source.decode_position_first_response(before[1]['payload_json'], before[1]['payload_digest'], plan)
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
        with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
            raw.execute("UPDATE study_position_first_responses SET payload_json='{}'::jsonb,payload_digest='replacement' WHERE event_id=%s", (first['event_id'],))
        raw.rollback()
        columns = ('event_id','attempt_id','decision_index','repertoire_id','variant','position','owner_epoch',
                   'payload_digest','payload_json','observed_at','accepted_at')
        for column in columns:
            with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
                raw.execute(sql.SQL('UPDATE study_position_first_responses SET {c}={c} WHERE event_id=%s').format(c=sql.Identifier(column)), (first['event_id'],))
            raw.rollback()
        with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
            raw.execute('DELETE FROM study_position_first_responses')
        raw.rollback()
        # PostgreSQL can reject the single-table form at its existing FK check
        # before the trigger. The legal CASCADE form must reach our guard.
        with pytest.raises((psycopg.errors.FeatureNotSupported, psycopg.errors.CheckViolation)) as error:
            raw.execute('TRUNCATE study_position_first_responses')
        assert error.value.sqlstate in ('0A000', '23514')
        raw.rollback()
        with pytest.raises(psycopg.errors.CheckViolation, match='study_position_first_response_immutable'):
            raw.execute('TRUNCATE study_position_first_responses CASCADE')
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
        # Actual second issuance and checkpoint: a post-effect failure rolls the
        # original receipt and real grade back together, then retry succeeds.
        from app.services.position_queue_admission import prepare_due, admit_due
        from app.services.queue_next_card import prepare_queue_head
        from app.queue_commands import issue_queue_head
        from app.services.opening_decision_evidence import decision_manifest
        from tempo_legacy_database.adapters.study import position_grade_effects
        from datetime import datetime, timezone
        import chess
        day = plan.local_day.isoformat()
        assert admit_due(port, day, prepare_due(port, day, {})) is None
        raw.commit()
        next_row = raw.execute("SELECT id,card_id FROM daily_queue WHERE status='queued' ORDER BY position,id").fetchone()
        assert next_row is not None
        next_entry, next_card = next_row['id'], next_row['card_id']
        raw.commit()
        head = {'queue_date': day, 'entry_id': next_entry, 'card_id': next_card, 'expected_revision': 1}
        assert issue_queue_head(port, prepare_queue_head(head))['issued']
        raw.commit()
        second_plan = prepare_queue_head(head).proposal
        scope = raw.execute("""SELECT snapshot.*,context.effective_trained_color FROM opening_evidence_presentations snapshot
            JOIN opening_evidence_queue_contexts context ON context.presentation_snapshot_id=snapshot.id
            WHERE context.queue_entry_id=%s AND context.repertoire_id='n2'""", (next_entry,)).fetchone()
        manifest = source.issued_position_manifest(second_plan, decision_manifest(source._manifest_snapshot(dict(scope)), 'n2'))
        decision = manifest['decisions'][0]
        alternative = next(move.uci() for move in chess.Board(decision['fen']).legal_moves if move.uci() != decision['expected_uci'])
        now = datetime.now(timezone.utc).isoformat()
        checkpoint = dict(attempt_id=second_plan.attempt_id, manifest=manifest, origin_queue_entry_id=next_entry,
            queue_entry_id=next_entry, started_at=now, study_timezone='UTC', source='live', events=[
                dict(sequence=1, decision_index=0, decision_id=decision['decision_id'], expected_uci=decision['expected_uci'],
                     kind='first_response', observed_at=now, response_uci=alternative, disposition='wrong')])
        raw.commit()
        real_apply = position_grade_effects.apply_in_borrowed_transaction
        def fail_after_grade(*args, **kwargs):
            real_apply(*args, **kwargs)
            raise RuntimeError('fixture after grade')
        prepared = source.prepare_standalone_checkpoint({'checkpoint': checkpoint})
        with monkeypatch.context() as scoped:
            scoped.setattr(position_grade_effects, 'apply_in_borrowed_transaction', fail_after_grade)
            raw.execute('BEGIN')
            with pytest.raises(RuntimeError, match='fixture after grade'):
                source.commit_standalone_checkpoint(port, prepared)
            raw.rollback()
        assert raw.execute('SELECT count(*) FROM study_position_first_responses WHERE attempt_id=%s', (second_plan.attempt_id,)).fetchone()[0] == 0
        assert raw.execute('SELECT count(*) FROM study_position_grades WHERE attempt_id=%s', (second_plan.attempt_id,)).fetchone()[0] == 0
        raw.commit()
        prepared = source.prepare_standalone_checkpoint({'checkpoint': checkpoint})
        raw.execute('BEGIN')
        assert source.commit_standalone_checkpoint(port, prepared)['persisted']
        # The duplicate is attempted while the real original insert and grade
        # are uncommitted; owner locks bound the wait before any second insert.
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as contender:
            contender.execute("SET LOCAL lock_timeout='100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                source.commit_standalone_checkpoint(PostgresConnection(contender), prepared)
        raw.commit()
        assert raw.execute('SELECT count(*) FROM study_position_first_responses WHERE attempt_id=%s', (second_plan.attempt_id,)).fetchone()[0] == 1
        assert raw.execute('SELECT count(*) FROM study_position_grades WHERE attempt_id=%s', (second_plan.attempt_id,)).fetchone()[0] == 1
        raw.commit()
        prepared = source.prepare_standalone_checkpoint({'checkpoint': checkpoint})
        raw.execute('BEGIN')
        assert source.commit_standalone_checkpoint(port, prepared)['persisted']
        raw.commit()
        assert raw.execute('SELECT count(*) FROM study_position_first_responses WHERE attempt_id=%s', (second_plan.attempt_id,)).fetchone()[0] == 1
        assert raw.execute('SELECT count(*) FROM study_position_grades WHERE attempt_id=%s', (second_plan.attempt_id,)).fetchone()[0] == 1
        raw.commit()
    monkeypatch.setattr(acceptance, '_real_review_journey', run_checks)
    monkeypatch.setattr(acceptance, '_real_daily_credit_followup', lambda *args, **kwargs: None)
    try:
        acceptance._run_schema_journey(monkeypatch, branches=True)
    finally:
        # This process-local hint is keyed without database identity. Restore only
        # the N2 entries owned by this disposable test before another N2 DB runs.
        with queue_next_card._source_cache_lock:
            for key in list(queue_next_card._source_cache):
                if key[0] == 'n2': del queue_next_card._source_cache[key]
            queue_next_card._source_cache.update(prior_cache)
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
            for role in ('tempo_reader', 'tempo_writer'):
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
