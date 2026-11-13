"""Current-schema bounded 1258-row native publication and rollback proof.

Adapted from check_postgres_queue_refresh_concurrency's publication case: the
retired fixture and historical schema pin are replaced by the current owner.
"""
from pathlib import Path
import json
import sys
import time
from unittest.mock import patch

import psycopg
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database/tests"))
from native_study_fixture import owned_postgres
from queue_refresh_scale_fixture import seed_queue_refresh_case, protected_hash
from tempo_storage import postgres
from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION
from app.services import durable_tasks, queue_application, postgres_queue_refresh as refresh


def test_queue_refresh_native_publication_budget(owned_postgres, monkeypatch):
    """Run separately from the race case, on a fresh owner database."""
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)
    try:
        with raw() as db:
            columns = db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='queue_projections' AND column_name IN ('requested_refresh_epoch','published_refresh_epoch')").fetchone()[0]
            assert columns == 2
            assert db.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == POSTGRES_SCHEMA_VERSION
            facts = seed_queue_refresh_case(db, queued_count=1258, issued_count=2, changed_kind_count=16, frozen_active=False)
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
            queue_application.request_queue_refresh_in_transaction(postgres.PostgresConnection(db), facts.queue_date)
        original_prepare = refresh._prepare_queue_randomization
        original_publish = refresh._publish_queue_randomization
        failures = []
        for scenario in ('unchanged', 'reordered', 'kinds', 'forced_timeout'):
            with raw() as db:
                task = db.execute("SELECT id,generation FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
                epoch = db.execute('SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()[0]
                checkpoint = {'queue_date': facts.queue_date, '_queue_phase': 'randomize_queue', '_refresh_cycle_epoch': epoch}
                db.execute("UPDATE background_tasks SET state='queued',phase='randomize_queue',payload_json=%s,lease_token=NULL,lease_expires_at=NULL WHERE id=%s", (json.dumps(checkpoint), task['id']))
                # Ensure an actual planned write is evaluated for every scenario.
                db.execute('UPDATE daily_queue_days SET membership_hash=%s WHERE queue_date=%s', ('fixture-not-current', facts.queue_date))
                before = protected_hash(db, (*facts.issued_ids, facts.blocked_id, facts.completed_id))
            claimed = durable_tasks.claim_task('daily_queue')
            assert claimed
            with raw() as db:
                original = {r[0]: (r[1], r[2], r[3]) for r in db.execute("SELECT id,position,card_bucket,admission_kind FROM daily_queue WHERE status='queued'")}
            def planned(*args, **kwargs):
                plan = original_prepare(*args, **kwargs)
                assert len(plan['entries']) == 1258
                for entry in plan['entries']:
                    entry['position'], entry['bucket'], entry['kind'] = original[entry['id']]
                if scenario in ('reordered', 'forced_timeout'):
                    movable = [entry for entry in plan['entries'] if entry['id'] not in facts.issued_ids]
                    positions = [entry['position'] for entry in movable][::-1]
                    for entry, position in zip(movable, positions):
                        entry['position'] = position
                if scenario == 'kinds':
                    for entry in plan['entries']:
                        if entry['id'] in facts.changed_kind_ids:
                            entry['kind'] = 'new'
                return plan
            settings = []
            stage_times = []
            def measured(name, fn):
                def invoke(*args, **kwargs):
                    started = time.perf_counter()
                    try:
                        return fn(*args, **kwargs)
                    finally:
                        stage_times.append((name, round((time.perf_counter()-started)*1000, 1)))
                return invoke
            writer_started = []
            original_lock = refresh.lock_current_slice
            def measured_lock(*args, **kwargs):
                writer_started.append(time.perf_counter())
                return original_lock(*args, **kwargs)
            def checked_publish(db, day, plan):
                settings.append(tuple(db.execute_native("SELECT current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone()))
                return original_publish(db, day, plan)
            def state_snapshot(db):
                return {table: db.execute('SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) FROM '+table+' t').fetchone()[0]
                        for table in ('daily_queue', 'queue_attempt_origins', 'reviews', 'daily_queue_days', 'background_tasks', 'queue_projections')}
            with raw() as db:
                full_before = state_snapshot(db)
            original_save = refresh.presentation_storage.save_plan
            save_reached = []
            def save_then_timeout(db, *args):
                value = original_save(db, *args)
                if scenario == 'forced_timeout':
                    save_reached.append(True)
                    db.execute_native('SELECT pg_sleep(0.65)')
                return value
            start = time.perf_counter()
            try:
                with patch.object(refresh.presentation_storage, 'save_plan', save_then_timeout), patch.object(refresh, '_prepare_queue_randomization', planned), patch.object(refresh, '_publish_queue_randomization', checked_publish), patch.object(refresh, 'lock_current_slice', measured_lock), patch.object(refresh.queue_application, 'presentation_rows', measured('presentation_rows', refresh.queue_application.presentation_rows)), patch.object(refresh.presentation_storage, 'apply_plan', measured('apply_plan', refresh.presentation_storage.apply_plan)):
                    result = refresh.execute_postgres_queue_refresh_slice(claimed)
                assert scenario != 'forced_timeout' and result
                outcome = 'advanced'
            except (psycopg.errors.TransactionTimeout, psycopg.OperationalError) as error:
                if scenario != 'forced_timeout':
                    failures.append(scenario)
                outcome = type(error).__name__ + ':' + str(getattr(error, 'sqlstate', None))
            finished = time.perf_counter()
            elapsed = round((finished - start) * 1000, 1)
            assert settings == [('500ms', '25ms')]
            with raw() as db:
                after = protected_hash(db, (*facts.issued_ids, facts.blocked_id, facts.completed_id))
                state = db.execute('SELECT state,phase,lease_token FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()
                full_after = state_snapshot(db)
            assert before == after
            if scenario == 'forced_timeout' or scenario in failures:
                assert full_after == full_before
                if scenario == 'forced_timeout':
                    assert save_reached == [True], 'timeout must follow saved reorder writes'
                assert state[0] == 'leased' and state[1] == 'randomize_queue' and state[2] == claimed['lease_token']
            else:
                assert state[0] == 'queued' and state[1] == 'quarantine'
            writer_ms = round((finished - writer_started[0]) * 1000, 1) if writer_started else None
            print('PUBLICATION', scenario, outcome, 'total_ms', elapsed, 'writer_ms', writer_ms, 'settings', settings[0], 'stages', stage_times, flush=True)
        assert not failures, f'publication exceeded budget: {failures}'
    finally:
        postgres.close_pools()
