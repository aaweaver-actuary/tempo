"""Native queue refresh races using the existing isolated PostgreSQL owner.

Run with pytest and repository backend, database, and src dirs on PYTHONPATH.
The existing owner verifies and disposes its single labelled PostgreSQL 18.6.
The migration upgrade case also requires the pinned local remediation package
installed in an isolated target for its registered distribution resource.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier, Event
import json
import os
import sys
import time
from unittest.mock import patch

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/tests'), str(ROOT / 'backend/tests')]
from test_product_game_lapse_postgres import owned_postgres as native_owner
from queue_refresh_scale_fixture import seed_queue_refresh_case, protected_hash
from tempo_storage import postgres
from app.services import durable_tasks, queue_application, postgres_queue_refresh as refresh


@pytest.fixture
def owned_postgres(tmp_path_factory):
    # A fresh, fully disposed owner per case permits running this file as a suite.
    yield from native_owner.__wrapped__(tmp_path_factory)


def test_queue_refresh_native_interleavings(owned_postgres, monkeypatch):
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()

    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)

    def snapshot(day):
        with raw() as db:
            task = db.execute("SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
            projection = db.execute('SELECT * FROM queue_projections WHERE queue_date=%s', (day,)).fetchone()
            return (dict(task) if task else None, dict(projection) if projection else None)

    def request(day, pointer=None, barrier=None):
        with raw() as db:
            if barrier:
                barrier.wait(timeout=10)
            return queue_application.request_queue_refresh_in_transaction(
                postgres.PostgresConnection(db), day, preserve_through_entry_id=pointer)

    def claim():
        task = durable_tasks.claim_task('daily_queue')
        assert task is not None
        return task

    try:
        with raw() as db:
            columns = db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='queue_projections' AND column_name IN ('requested_refresh_epoch','published_refresh_epoch')").fetchone()[0]
            assert columns == 2
            assert db.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == 59
            facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, changed_kind_count=3, frozen_active=False)
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
        day = facts.queue_date
        with raw() as db:
            protected_before = {row[0]: (row[1], row[2]) for row in db.execute("SELECT q.id,to_jsonb(q)::text,to_jsonb(o)::text FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.id=ANY(%s) ORDER BY q.id", ([facts.issued_ids[1], facts.blocked_id, facts.completed_id],))}
        # Race creation of the singleton, then preserve its identity and checkpoint.
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in (pool.submit(request, day, 1, barrier), pool.submit(request, day, 2, barrier))]
        task, projection = snapshot(day)
        assert results[0]['id'] == results[1]['id'] == task['id']
        assert projection['requested_refresh_epoch'] == 2
        assert json.loads(task['payload_json'])['preserve_through_entry_id'] == 2
        first = claim()
        # Legacy checkpoint must pin before doing any prepared work.
        assert refresh.execute_postgres_queue_refresh_slice(first)
        task, projection = snapshot(day)
        payload = json.loads(task['payload_json'])
        assert payload['_queue_phase'] == 'unlock_opening' and payload['_refresh_cycle_epoch'] == 2
        assert payload['preserve_through_entry_id'] == 2
        active = claim()
        before, _ = snapshot(day)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda i: request(day, 10 + i), range(20)))
        after, projection = snapshot(day)
        for key in ('id', 'generation', 'state', 'phase', 'lease_token', 'lease_expires_at'):
            assert after[key] == before[key], key
        assert json.loads(after['payload_json'])['preserve_through_entry_id'] == 29
        assert projection['requested_refresh_epoch'] == 22 and projection['published_refresh_epoch'] == 0
        assert refresh.execute_postgres_queue_refresh_slice(active)
        after, _ = snapshot(day)
        assert json.loads(after['payload_json'])['preserve_through_entry_id'] == 29
        # Advance via real slices under quiescence, not by setting readiness.
        slices = 0
        while True:
            task, projection = snapshot(day)
            if task['state'] == 'complete':
                break
            assert slices < 300, (task['phase'], task['payload_json'])
            delivery = claim()
            refresh.execute_postgres_queue_refresh_slice(delivery)
            slices += 1
        assert projection['state'] == 'ready' and projection['refresh_pending'] == 0
        assert projection['requested_refresh_epoch'] == projection['published_refresh_epoch'] == 22
        # Crash/retry fence at final publication: actual blocked-count preparation
        # finishes, then a second connection commits a review and request before
        # the worker can lock its lease and projection.
        request(day, 30)
        task, projection = snapshot(day)
        epoch = projection['requested_refresh_epoch']
        checkpoint = {'queue_date': day, '_queue_phase': 'publish_projection',
                      '_refresh_cycle_epoch': epoch, 'preserve_through_entry_id': 30}
        with raw() as db:
            db.execute("UPDATE background_tasks SET phase='publish_projection',payload_json=%s WHERE id=%s",
                       (json.dumps(checkpoint), task['id']))
        final_claim = claim()
        prepared, release = Event(), Event()
        original_prepare = refresh._prepare_projection_blocked_count
        def held_prepare(queue_date):
            value = original_prepare(queue_date)
            prepared.set()
            assert release.wait(10)
            return value
        def late_review():
            from app import review_commands
            payload = {'card_id': 'queue-refresh-scale-00001',
                       'review': {'outcome': 'correct', 'guided': False,
                                  'queue_entry_id': 1, 'attempt_id': 'w14-real-review',
                                  'expected_revision': 1}}
            state_before = snapshot(day)
            try:
                with raw() as db:
                    review_commands.submit_review(postgres.PostgresConnection(db), payload)
                    raise ValueError('simulated review rollback')
            except ValueError as error:
                assert str(error) == 'simulated review rollback'
            assert snapshot(day) == state_before
            with raw() as db:
                assert db.execute('SELECT count(*) FROM review_attempt_receipts WHERE attempt_id=%s', ('w14-real-review',)).fetchone()[0] == 0
            with raw() as db:
                result = review_commands.submit_review(postgres.PostgresConnection(db), payload)
                assert result['persisted']
            with raw() as db:
                replay = review_commands.submit_review(postgres.PostgresConnection(db), payload)
                assert replay == result
            return result
        with patch.object(refresh, '_prepare_projection_blocked_count', held_prepare):
            with ThreadPoolExecutor(max_workers=1) as pool:
                delivery = pool.submit(refresh.execute_postgres_queue_refresh_slice, final_claim)
                assert prepared.wait(10)
                late_review()
                release.set()
                assert delivery.result(timeout=10)
        task, projection = snapshot(day)
        payload = json.loads(task['payload_json'])
        assert task['state'] == 'queued' and payload['_queue_phase'] == 'unlock_opening'
        assert payload['_refresh_cycle_epoch'] == epoch + 1 and payload['preserve_through_entry_id'] >= 30
        assert projection['state'] == 'refreshing' and projection['refresh_pending'] == 1
        assert projection['published_refresh_epoch'] < projection['requested_refresh_epoch'] == epoch + 1
        # Reusing a stale pre-commit lease after the checkpoint must be inert.
        assert not refresh.execute_postgres_queue_refresh_slice(final_claim)
        assert snapshot(day)[1]['state'] == 'refreshing'
        steps = 0
        while snapshot(day)[0]['state'] != 'complete':
            assert steps < 300
            refresh.execute_postgres_queue_refresh_slice(claim())
            steps += 1
        task, projection = snapshot(day)
        assert (projection['state'], projection['refresh_pending']) == ('ready', 0)
        assert projection['published_refresh_epoch'] == projection['requested_refresh_epoch'] == epoch + 1
        assert not refresh.execute_postgres_queue_refresh_slice(final_claim)
        # A completion failure rolls back the epoch and ready writes together.
        request(day)
        task, projection = snapshot(day)
        epoch = projection['requested_refresh_epoch']
        checkpoint.update(_refresh_cycle_epoch=epoch)
        with raw() as db:
            db.execute("UPDATE background_tasks SET phase='publish_projection',payload_json=%s WHERE id=%s",
                       (json.dumps(checkpoint), task['id']))
        delivery = claim()
        before = snapshot(day)
        with patch.object(refresh, 'complete_task_slice_in_transaction', return_value=False):
            try:
                refresh.execute_postgres_queue_refresh_slice(delivery)
            except RuntimeError as error:
                assert 'lost its task lease' in str(error)
            else:
                raise AssertionError('completion failure did not abort publication')
        assert snapshot(day) == before
        # Replay the still-valid lease: ready commits; replay after ack is inert.
        assert not refresh.execute_postgres_queue_refresh_slice(delivery)
        task, projection = snapshot(day)
        assert task['state'] == 'complete' and projection['state'] == 'ready'
        assert projection['published_refresh_epoch'] == projection['requested_refresh_epoch'] == epoch
        assert not refresh.execute_postgres_queue_refresh_slice(delivery)
        with raw() as db:
            protected_after = {row[0]: (row[1], row[2]) for row in db.execute("SELECT q.id,to_jsonb(q)::text,to_jsonb(o)::text FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.id=ANY(%s) ORDER BY q.id", ([facts.issued_ids[1], facts.blocked_id, facts.completed_id],))}
            # The existing planner derives new/review presentation kind even
            # for an issued row; its retained origin and attempt fields remain.
            # Eligible blocked rows intentionally restore in restore_due.
            for identifier, permitted in ((facts.issued_ids[1], {'admission_kind'}),
                                           (facts.blocked_id, {'status', 'admission_kind'}),
                                           (facts.completed_id, set())):
                before_q, before_o = map(json.loads, protected_before[identifier])
                after_q, after_o = map(json.loads, protected_after[identifier])
                assert {k: v for k, v in before_q.items() if k not in permitted} == {k: v for k, v in after_q.items() if k not in permitted}
                allowed_origin = {'last_status'} if identifier == facts.blocked_id else set()
                assert {k: v for k, v in before_o.items() if k not in allowed_origin} == {k: v for k, v in after_o.items() if k not in allowed_origin}
                if identifier == facts.issued_ids[1]:
                    assert (before_q['admission_kind'], after_q['admission_kind']) == ('due', 'new')
                    assert before_o['admission_kind'] == after_o['admission_kind'] == 'due'
                if identifier == facts.blocked_id:
                    assert (before_q['status'], after_q['status']) == ('blocked', 'queued')
                    assert (before_o['last_status'], after_o['last_status']) == ('blocked', 'queued')
                    assert (before_q['admission_kind'], after_q['admission_kind']) == ('due', 'new')
        # Rollover replaces the singleton and cannot use the prior day's lease.
        tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        request(tomorrow)
        next_task, next_projection = snapshot(tomorrow)
        assert next_task['generation'] > task['generation']
        assert json.loads(next_task['payload_json'])['queue_date'] == tomorrow
        assert next_projection['state'] == 'refreshing' and next_projection['refresh_pending'] == 1
        assert not refresh.execute_postgres_queue_refresh_slice(delivery)
        print('PASS native queue refresh concurrent signals, late review fence, rollback, replay and rollover')
    finally:
        postgres.close_pools()


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
            assert db.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] == 59
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


def test_native_tactic_attempt_and_review(owned_postgres, monkeypatch):
    """Exercise the actual tactic attempt and saved review on a fresh database."""
    from app import tactic_commands, review_commands
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()
    fen = 'q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17'
    moves = ['e8d7', 'a2e6', 'd7d8', 'f7f8']
    payload = {'request': {'attempt_id': 'w14-tactic-discovery', 'puzzle_id': 'w14-tactic',
               'deck_id': 'hangingPiece-easy', 'correct': False, 'clean': False,
               'source_fen': fen, 'moves': moves}, 'pack_id': 'hangingPiece-easy',
               'training_fen': fen, 'solution': moves}
    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)
    try:
        with raw() as db:
            result = tactic_commands.submit_tactic_attempt(postgres.PostgresConnection(db), payload)
        with raw() as db:
            assert tactic_commands.submit_tactic_attempt(postgres.PostgresConnection(db), payload) == result
            row = db.execute("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'", (result['card_id'],)).fetchone()
            assert row is not None
            entry = row[0]
        review = {'card_id': result['card_id'], 'review': {'outcome': 'correct', 'guided': False,
                  'queue_entry_id': entry, 'attempt_id': 'w14-tactic-review', 'expected_revision': 1}}
        try:
            with raw() as db:
                review_commands.submit_review(postgres.PostgresConnection(db), review)
                raise ValueError('rollback tactic review')
        except ValueError as error:
            assert str(error) == 'rollback tactic review'
        with raw() as db:
            assert db.execute('SELECT count(*) FROM review_attempt_receipts WHERE attempt_id=%s', ('w14-tactic-review',)).fetchone()[0] == 0
            saved = review_commands.submit_review(postgres.PostgresConnection(db), review)
            assert saved['persisted']
        with raw() as db:
            assert review_commands.submit_review(postgres.PostgresConnection(db), review) == saved
            assert db.execute('SELECT count(*) FROM review_attempt_receipts WHERE attempt_id=%s', ('w14-tactic-review',)).fetchone()[0] == 1
        print('PASS native tactic attempt and saved review rollback, commit and idempotent replay')
    finally:
        postgres.close_pools()


def test_schema_58_to_59_preserves_existing_state(owned_postgres, monkeypatch):
    """Reconstruct schema 58 only inside this verified, fresh disposable owner."""
    import importlib.util
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    with psycopg.connect(**options) as db:
        facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, changed_kind_count=3)
        db.execute('ALTER TABLE queue_projections DROP CONSTRAINT queue_refresh_epoch_order')
        db.execute('ALTER TABLE queue_projections DROP COLUMN requested_refresh_epoch, DROP COLUMN published_refresh_epoch')
        db.execute('DELETE FROM tempo_schema_migrations WHERE version=59')
    def snapshots():
        with psycopg.connect(**options) as db:
            return {table: db.execute('SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) FROM '+table+' t').fetchone()[0]
                    for table in ('daily_queue', 'queue_attempt_origins', 'reviews', 'background_tasks', 'queue_projections')}
    before = snapshots()
    spec = importlib.util.spec_from_file_location('w14_migration_owner', ROOT / 'database/tooling/apply_postgres_migrations.py')
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    owner.apply_migrations(make_conninfo(**options))
    after = snapshots()
    projections = after.pop('queue_projections')
    assert len(projections) == 1
    assert projections[0].pop('requested_refresh_epoch') == 0
    assert projections[0].pop('published_refresh_epoch') == 0
    assert projections == before.pop('queue_projections') and after == before
    with psycopg.connect(**options) as db:
        assert [r[0] for r in db.execute('SELECT version FROM tempo_schema_migrations ORDER BY version')] == list(range(1, 60))
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                db.execute('UPDATE queue_projections SET published_refresh_epoch=1 WHERE queue_date=%s', (facts.queue_date,))
    print('PASS schema 58 to 59 preserves queue, origins, reviews, task and projection')


def test_native_review_after_randomization_prepare_rejects_stale_plan(owned_postgres, monkeypatch):
    from app import review_commands
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()
    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)
    try:
        with raw() as db:
            facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, frozen_active=False)
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
            queue_application.request_queue_refresh_in_transaction(postgres.PostgresConnection(db), facts.queue_date)
            row = db.execute("SELECT id FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
            epoch = db.execute('SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()[0]
            payload = {'queue_date': facts.queue_date, '_queue_phase': 'randomize_queue', '_refresh_cycle_epoch': epoch}
            db.execute("UPDATE background_tasks SET phase='randomize_queue',payload_json=%s WHERE id=%s", (json.dumps(payload), row[0]))
            protected_before = protected_hash(db, (facts.issued_ids[1],))
        claimed = durable_tasks.claim_task('daily_queue')
        assert claimed
        prepared, release = Event(), Event()
        prepare = refresh._prepare_queue_randomization
        def held(*args, **kwargs):
            plan = prepare(*args, **kwargs)
            prepared.set()
            assert release.wait(10)
            return plan
        with patch.object(refresh, '_prepare_queue_randomization', held):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(refresh.execute_postgres_queue_refresh_slice, claimed)
                assert prepared.wait(10)
                with raw() as db:
                    result = review_commands.submit_review(postgres.PostgresConnection(db),
                        {'card_id': 'queue-refresh-scale-00001', 'review': {'outcome': 'correct',
                         'guided': False, 'queue_entry_id': 1, 'attempt_id': 'w14-stale-plan-review', 'expected_revision': 1}})
                    assert result['persisted']
                release.set()
                assert future.result(timeout=10)
        with raw() as db:
            task = db.execute('SELECT state,phase,payload_json FROM background_tasks WHERE id=%s', (claimed['id'],)).fetchone()
            projection = db.execute('SELECT state,refresh_pending,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()
            assert (task[0], task[1]) == ('queued', 'randomize_queue')
            assert (projection[0], projection[1]) == ('refreshing', 1)
            assert projection[2] > projection[3]
            assert protected_hash(db, (facts.issued_ids[1],)) == protected_before
        for _ in range(300):
            with raw() as db:
                state = db.execute('SELECT state FROM background_tasks WHERE id=%s', (claimed['id'],)).fetchone()[0]
            if state == 'complete':
                break
            delivery = durable_tasks.claim_task('daily_queue')
            assert delivery
            refresh.execute_postgres_queue_refresh_slice(delivery)
        else:
            raise AssertionError('finite refresh did not converge')
        with raw() as db:
            projection = db.execute('SELECT state,refresh_pending,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()
            assert (projection[0], projection[1], projection[2] == projection[3]) == ('ready', 0, True)
        print('PASS real late review rejects stale randomization and converges')
    finally:
        postgres.close_pools()


def test_native_priority_fast_path_and_date_lock(owned_postgres, monkeypatch):
    from tempo_storage.queue_position_lock import lock_queue_date_for_position
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()
    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)
    def snapshot(db):
        return tuple(tuple(row) for row in db.execute("SELECT id,position,card_bucket,admission_kind,gameplay_priority_reason FROM daily_queue WHERE queue_date=%s ORDER BY id", (facts.queue_date,)))
    try:
        with raw() as db:
            facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, frozen_active=False)
        plan = refresh._prepare_queue_randomization(facts.queue_date)
        # Compare changed path to the previous always-run policy using identical
        # snapshots and transaction rollback, including protected rows.
        actual_priority = refresh.prioritize_queued_misses
        outcomes = []
        calls = []
        for force in (False, True):
            with raw() as db:
                with patch.object(refresh.presentation_storage, 'has_queue_priority', return_value=True) if force else patch.object(refresh.presentation_storage, 'has_queue_priority', wraps=refresh.presentation_storage.has_queue_priority):
                    with patch.object(refresh, 'prioritize_queued_misses', wraps=actual_priority) as observed:
                        assert refresh._publish_queue_randomization(postgres.PostgresConnection(db), facts.queue_date, plan)
                        outcomes.append(snapshot(db))
                        calls.append(observed.call_count)
                db.rollback()
        assert calls == [0, 1] and outcomes[0] == outcomes[1]
        with raw() as db:
            db.execute("UPDATE daily_queue SET gameplay_priority_reason='unknown-retained-reason' WHERE id=%s", (facts.queued_ids[3],))
        plan = refresh._prepare_queue_randomization(facts.queue_date)
        with raw() as db:
            with patch.object(refresh, 'prioritize_queued_misses', wraps=actual_priority) as observed:
                assert refresh._publish_queue_randomization(postgres.PostgresConnection(db), facts.queue_date, plan)
                assert observed.call_count == 1
            db.rollback()
        # The exact queue-date lock makes a same-day reason writer wait until
        # publication commits; this does not fake a real miss obligation.
        with raw() as db:
            db.execute('UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE id=%s', (facts.queued_ids[3],))
        plan = refresh._prepare_queue_randomization(facts.queue_date)
        held, release, writer_started, writer_finished = Event(), Event(), Event(), Event()
        query = refresh.presentation_storage.has_queue_priority
        def pause(db, day):
            held.set()
            assert release.wait(10)
            return query(db, day)
        def publisher():
            with raw() as db:
                return refresh._publish_queue_randomization(postgres.PostgresConnection(db), facts.queue_date, plan)
        def reason_writer():
            with raw() as db:
                writer_started.set()
                lock_queue_date_for_position(postgres.PostgresConnection(db), facts.queue_date)
                db.execute("UPDATE daily_queue SET gameplay_priority_reason='unknown-retained-reason' WHERE id=%s", (facts.queued_ids[3],))
            writer_finished.set()
        with patch.object(refresh.presentation_storage, 'has_queue_priority', pause):
            with ThreadPoolExecutor(max_workers=2) as pool:
                publication = pool.submit(publisher)
                assert held.wait(10)
                writer = pool.submit(reason_writer)
                assert writer_started.wait(10)
                assert not writer_finished.wait(0.1)
                release.set()
                assert publication.result(timeout=10)
                writer.result(timeout=10)
        assert writer_finished.is_set()
        with raw() as db:
            assert db.execute('SELECT gameplay_priority_reason FROM daily_queue WHERE id=%s', (facts.queued_ids[3],)).fetchone()[0] == 'unknown-retained-reason'
        plan = refresh._prepare_queue_randomization(facts.queue_date)
        with raw() as db:
            with patch.object(refresh, 'prioritize_queued_misses', wraps=actual_priority) as observed:
                assert refresh._publish_queue_randomization(postgres.PostgresConnection(db), facts.queue_date, plan)
                assert observed.call_count == 1
            db.rollback()
        print('PASS priority no-reason parity, unknown reason and same-day lock ordering')
    finally:
        postgres.close_pools()


def test_native_http_task_diagnostics(owned_postgres, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()
    try:
        with psycopg.connect(**options) as db:
            facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, frozen_active=False)
            prior = (date.fromisoformat(facts.queue_date) - timedelta(days=1)).isoformat()
            db.execute("INSERT INTO queue_projections(queue_date,state,refresh_pending) VALUES(%s,'refreshing',1)", (prior,))
            payload = {'queue_date': facts.queue_date, '_queue_phase': 'study_opening_item', 'study_opening_index': 3, 'secret': 'private-pgn'}
            checkpoint = [1, 'queued', payload]
            db.execute("""UPDATE background_tasks SET deduplication_key='current',state='retrying',phase='queued',
                payload_json=%s,transaction_timeout_count=8,transaction_timeout_checkpoint=%s,
                last_error='transaction timed out',next_attempt_at='2099-01-01T00:00:00+00:00' WHERE id=%s""",
                (json.dumps(payload), json.dumps(checkpoint), facts.task_id))
        # Instantiation without context deliberately avoids lifespan/provider startup.
        response = TestClient(app).get('/api/system/tasks')
        assert response.status_code == 200
        data = response.json()
        task = next(row for row in data['tasks'] if row['id'] == facts.task_id)
        assert task['queue_date'] == facts.queue_date and task['queue_phase'] == 'study_opening_item'
        assert task['transaction_timeout_count'] == 8 and task['last_error'] == 'transaction timed out'
        assert task['next_retry'] == '2099-01-01T00:00:00+00:00'
        assert task['timeout_checkpoint'] == {'queue_phase': 'study_opening_item', 'study_opening_index': 3}
        assert 'private-pgn' not in json.dumps(data) and 'payload_json' not in task
        projections = {row['queue_date']: row for row in data['queue_projections']}
        assert projections[prior]['worker_status'] == 'historical'
        assert projections[facts.queue_date]['worker_status'] == 'current'
        print('PASS native HTTP task timeout diagnostics and historical annotation')
    finally:
        postgres.close_pools()


def test_native_foreground_review_waits_and_lock_timeout_rolls_back(owned_postgres, monkeypatch):
    from app import review_commands
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()
    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)
    try:
        with raw() as db:
            facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, frozen_active=False)
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
            task = queue_application.request_queue_refresh_in_transaction(postgres.PostgresConnection(db), facts.queue_date)
            epoch = db.execute('SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()[0]
            payload = {'queue_date': facts.queue_date, '_queue_phase': 'randomize_queue', '_refresh_cycle_epoch': epoch}
            db.execute("UPDATE background_tasks SET phase='randomize_queue',payload_json=%s WHERE id=%s", (json.dumps(payload), task['id']))
        claimed = durable_tasks.claim_task('daily_queue')
        assert claimed
        with raw() as blocker:
            blocker.execute('SELECT id FROM background_tasks WHERE id=%s FOR UPDATE', (task['id'],))
            try:
                refresh.execute_postgres_queue_refresh_slice(claimed)
            except psycopg.errors.LockNotAvailable as error:
                assert error.sqlstate == '55P03'
            else:
                raise AssertionError('conflicting task lock did not reach 25ms limit')
            with raw() as observer:
                state = observer.execute('SELECT state,phase,lease_token FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()
                assert tuple(state) == ('leased', 'randomize_queue', claimed['lease_token'])
        locked, release, review_started, review_done = Event(), Event(), Event(), Event()
        original = refresh._publish_queue_randomization
        def paused(*args):
            locked.set()
            assert release.wait(5)
            return original(*args)
        def review():
            review_started.set()
            started = time.perf_counter()
            with raw() as db:
                result = review_commands.submit_review(postgres.PostgresConnection(db),
                    {'card_id': 'queue-refresh-scale-00001', 'review': {'outcome': 'correct', 'guided': False,
                     'queue_entry_id': 1, 'attempt_id': 'w14-contention-review', 'expected_revision': 1}})
            review_done.set()
            return result, round((time.perf_counter()-started)*1000, 1)
        with patch.object(refresh, '_publish_queue_randomization', paused):
            with ThreadPoolExecutor(max_workers=2) as pool:
                background = pool.submit(refresh.execute_postgres_queue_refresh_slice, claimed)
                assert locked.wait(5)
                foreground = pool.submit(review)
                assert review_started.wait(5)
                assert not review_done.wait(0.1)
                release.set()
                try:
                    background.result(timeout=5)
                except psycopg.errors.LockNotAvailable as error:
                    assert error.sqlstate == '55P03'
                else:
                    raise AssertionError('worker should yield on the review queue-date lock')
                saved, wait_ms = foreground.result(timeout=5)
        assert saved['persisted']
        with raw() as db:
            assert db.execute("SELECT count(*) FROM review_attempt_receipts WHERE attempt_id='w14-contention-review'").fetchone()[0] == 1
            row = db.execute('SELECT requested_refresh_epoch,published_refresh_epoch,state FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()
            assert row[0] > row[1] and row[2] == 'refreshing'
        # Simulate process loss after the lease has expired; use the real claim
        # and slice machinery to reclaim and converge without deleting work.
        with raw() as db:
            db.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=%s", (task['id'],))
        reclaimed = durable_tasks.claim_task('daily_queue')
        assert reclaimed and reclaimed['lease_token'] != claimed['lease_token']
        assert not refresh.execute_postgres_queue_refresh_slice(claimed)
        refresh.execute_postgres_queue_refresh_slice(reclaimed)
        for _ in range(300):
            with raw() as db:
                state = db.execute('SELECT state FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()[0]
            if state == 'complete':
                break
            delivery = durable_tasks.claim_task('daily_queue')
            assert delivery
            refresh.execute_postgres_queue_refresh_slice(delivery)
        else:
            raise AssertionError('reclaimed refresh did not converge')
        with raw() as db:
            projection = db.execute('SELECT state,refresh_pending,requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()
            assert (projection[0], projection[1], projection[2] == projection[3]) == ('ready', 0, True)
        print('PASS 25ms lock conflicts, foreground review, expired-lease recovery; review_ms', wait_ms)
    finally:
        postgres.close_pools()


def test_native_scale_active_frozen_and_stored_priority(owned_postgres, monkeypatch):
    """Exercise the retained priority pass with a valid active frozen order."""
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    from app.study.openings.frozen_math import content_fingerprint
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', url)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', url)
    postgres.prepare_authoritative_background_pool()
    def raw():
        return psycopg.connect(**options, row_factory=postgres.tempo_row_factory)
    try:
        with raw() as db:
            facts = seed_queue_refresh_case(db, queued_count=1258, issued_count=2, frozen_active=True)
            # The scale helper is intentionally synthetic; make its captured
            # fingerprints match the real content and membership reader so
            # the active run represents valid ranked cards, not stale ranks.
            cards = db.execute('SELECT * FROM cards WHERE repertoire_id=%s ORDER BY id', ('queue-refresh-scale',)).fetchall()
            db.cursor().executemany('UPDATE frozen_opening_ranks SET fingerprint=%s WHERE run_id=%s AND card_id=%s',
                [(content_fingerprint(dict(card), ['queue-refresh-scale']), 'queue-refresh-scale', card['id']) for card in cards])
            db.execute("UPDATE daily_queue SET gameplay_priority_reason='unknown-retained-reason' WHERE id=%s", (facts.queued_ids[3],))
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
            task = queue_application.request_queue_refresh_in_transaction(postgres.PostgresConnection(db), facts.queue_date)
            epoch = db.execute('SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()[0]
            payload = {'queue_date': facts.queue_date, '_queue_phase': 'randomize_queue',
                       '_refresh_cycle_epoch': epoch, 'frozen_token': ['queue-refresh-scale', 1]}
            db.execute("UPDATE background_tasks SET phase='randomize_queue',payload_json=%s WHERE id=%s", (json.dumps(payload), task['id']))
            protected = tuple(tuple(row) for row in db.execute("SELECT q.id,q.position,q.status,q.attempt_state,q.attempt_failed,q.review_result_json,to_jsonb(o)::text FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.id=ANY(%s) ORDER BY q.id", (list(facts.issued_ids),)))
        claimed = durable_tasks.claim_task('daily_queue')
        assert claimed
        prepared, settings, calls, stages = [], [], [], []
        original_prepare = refresh._prepare_queue_randomization
        original_priority = refresh.prioritize_queued_misses
        original_publish = refresh._publish_queue_randomization
        def capture_prepare(*args, **kwargs):
            start = time.perf_counter()
            plan = original_prepare(*args, **kwargs)
            prepared.append((plan, round((time.perf_counter()-start)*1000, 1)))
            return plan
        def capture_publish(db, day, plan):
            settings.append(tuple(db.execute_native("SELECT current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone()))
            started = time.perf_counter()
            try:
                return original_publish(db, day, plan)
            finally:
                stages.append(round((time.perf_counter()-started)*1000, 1))
        def capture_priority(*args):
            calls.append(True)
            return original_priority(*args)
        with patch.object(refresh, '_prepare_queue_randomization', capture_prepare), patch.object(refresh, '_publish_queue_randomization', capture_publish), patch.object(refresh, 'prioritize_queued_misses', capture_priority):
            assert refresh.execute_postgres_queue_refresh_slice(claimed)
        assert prepared[0][0]['frozen_token'] == ['queue-refresh-scale', 1]
        assert len(prepared[0][0]['entries']) == 1258
        assert settings == [('500ms', '25ms')] and calls == [True]
        with raw() as db:
            after = tuple(tuple(row) for row in db.execute("SELECT q.id,q.position,q.status,q.attempt_state,q.attempt_failed,q.review_result_json,to_jsonb(o)::text FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id WHERE q.id=ANY(%s) ORDER BY q.id", (list(facts.issued_ids),)))
            assert after == protected
            state = db.execute('SELECT state,phase FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()
            assert tuple(state) == ('queued', 'quarantine')
            assert db.execute('SELECT gameplay_priority_reason FROM daily_queue WHERE id=%s', (facts.queued_ids[3],)).fetchone()[0] == 'unknown-retained-reason'
        print('PASS 1258 active frozen valid ranks and retained priority pass', 'prepare_ms', prepared[0][1], 'publish_ms', stages[0], 'settings', settings[0])
    finally:
        postgres.close_pools()
