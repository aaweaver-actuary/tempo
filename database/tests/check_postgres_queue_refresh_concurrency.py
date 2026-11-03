"""Native queue refresh races using the existing isolated PostgreSQL owner.

Run with pytest and repository backend, database, and src dirs on PYTHONPATH.
Until the migration is registered, TEMPO_W14_PROPOSED_MIGRATION may point to
its reviewed SQL for a provisional run; such a run is not migration acceptance.
The existing owner verifies and disposes its single labelled PostgreSQL 18.6.
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
from tempo_legacy_database.adapters import postgres_store
from app.services import durable_tasks, queue_application, postgres_queue_refresh as refresh


@pytest.fixture
def owned_postgres(tmp_path_factory):
    # A fresh, fully disposed owner per case permits running this file as a suite.
    yield from native_owner.__wrapped__(tmp_path_factory)


def test_queue_refresh_native_interleavings(owned_postgres):
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    os.environ['TEMPO_TEST_INSTANCE'] = 'disposable'
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    os.environ['TEMPO_DATABASE_READ_URL'] = url
    os.environ['TEMPO_DATABASE_WRITE_URL'] = url
    postgres_store.prepare_authoritative_background_pool()

    def raw():
        return psycopg.connect(**options, row_factory=postgres_store.tempo_row_factory)

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
                postgres_store.PostgresConnection(db), day, preserve_through_entry_id=pointer)

    def claim():
        task = durable_tasks.claim_task('daily_queue')
        assert task is not None
        return task

    try:
        with raw() as db:
            columns = db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='queue_projections' AND column_name IN ('requested_refresh_epoch','published_refresh_epoch')").fetchone()[0]
            if columns == 0:
                proposal = os.environ.get('TEMPO_W14_PROPOSED_MIGRATION')
                if not proposal:
                    raise RuntimeError('registered epoch migration required; set TEMPO_W14_PROPOSED_MIGRATION only for provisional proof')
                db.execute(Path(proposal).read_text(), prepare=False)
                print('PROVISIONAL proposed epoch DDL; not migration acceptance')
            else:
                assert columns == 2
            facts = seed_queue_refresh_case(db, queued_count=32, issued_count=2, changed_kind_count=3, frozen_active=False)
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
        day = facts.queue_date
        with raw() as db:
            protected_before = protected_hash(db, (facts.issued_ids[1], facts.blocked_id, facts.completed_id))
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
                    review_commands.submit_review(postgres_store.PostgresConnection(db), payload)
                    raise ValueError('simulated review rollback')
            except ValueError as error:
                assert str(error) == 'simulated review rollback'
            assert snapshot(day) == state_before
            with raw() as db:
                assert db.execute('SELECT count(*) FROM review_attempt_receipts WHERE attempt_id=%s', ('w14-real-review',)).fetchone()[0] == 0
            with raw() as db:
                result = review_commands.submit_review(postgres_store.PostgresConnection(db), payload)
                assert result['persisted']
            with raw() as db:
                replay = review_commands.submit_review(postgres_store.PostgresConnection(db), payload)
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
            assert protected_hash(db, (facts.issued_ids[1], facts.blocked_id, facts.completed_id)) == protected_before
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
        postgres_store.close_pools()
        os.environ.pop('TEMPO_DATABASE_READ_URL', None)
        os.environ.pop('TEMPO_DATABASE_WRITE_URL', None)


def test_queue_refresh_native_publication_budget(owned_postgres):
    """Run separately from the race case, on a fresh owner database."""
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    os.environ['TEMPO_TEST_INSTANCE'] = 'disposable'
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    os.environ['TEMPO_DATABASE_READ_URL'] = url
    os.environ['TEMPO_DATABASE_WRITE_URL'] = url
    def raw():
        return psycopg.connect(**options, row_factory=postgres_store.tempo_row_factory)
    try:
        with raw() as db:
            columns = db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='queue_projections' AND column_name IN ('requested_refresh_epoch','published_refresh_epoch')").fetchone()[0]
            if columns == 0:
                proposal = os.environ.get('TEMPO_W14_PROPOSED_MIGRATION')
                if not proposal:
                    raise RuntimeError('registered epoch migration required')
                db.execute(Path(proposal).read_text(), prepare=False)
                print('PROVISIONAL proposed epoch DDL; not migration acceptance')
            else:
                assert columns == 2
            facts = seed_queue_refresh_case(db, queued_count=1258, issued_count=2, changed_kind_count=16, frozen_active=False)
            db.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
            queue_application.request_queue_refresh_in_transaction(postgres_store.PostgresConnection(db), facts.queue_date)
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
                if scenario == 'reordered':
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
                if scenario == 'forced_timeout':
                    db.execute_native('SELECT pg_sleep(0.65)')
                return original_publish(db, day, plan)
            start = time.perf_counter()
            try:
                with patch.object(refresh, '_prepare_queue_randomization', planned), patch.object(refresh, '_publish_queue_randomization', checked_publish), patch.object(refresh, 'lock_current_slice', measured_lock), patch.object(refresh.queue_application, 'presentation_rows', measured('presentation_rows', refresh.queue_application.presentation_rows)), patch.object(refresh.presentation_storage, 'apply_plan', measured('apply_plan', refresh.presentation_storage.apply_plan)):
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
            assert before == after
            if scenario == 'forced_timeout' or scenario in failures:
                assert state[0] == 'leased' and state[1] == 'randomize_queue' and state[2] == claimed['lease_token']
            else:
                assert state[0] == 'queued' and state[1] == 'quarantine'
            writer_ms = round((finished - writer_started[0]) * 1000, 1) if writer_started else None
            print('PUBLICATION', scenario, outcome, 'total_ms', elapsed, 'writer_ms', writer_ms, 'settings', settings[0], 'stages', stage_times, flush=True)
        assert not failures, f'publication exceeded budget: {failures}'
    finally:
        postgres_store.close_pools()
        os.environ.pop('TEMPO_DATABASE_READ_URL', None)
        os.environ.pop('TEMPO_DATABASE_WRITE_URL', None)


def test_native_tactic_attempt_and_review(owned_postgres):
    """Exercise the actual tactic attempt and saved review on a fresh database."""
    from app import tactic_commands, review_commands
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    os.environ['TEMPO_TEST_INSTANCE'] = 'disposable'
    options = owned_postgres.connection_options
    url = make_conninfo(**options)
    os.environ['TEMPO_DATABASE_READ_URL'] = url
    os.environ['TEMPO_DATABASE_WRITE_URL'] = url
    postgres_store.prepare_authoritative_background_pool()
    fen = 'q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17'
    moves = ['e8d7', 'a2e6', 'd7d8', 'f7f8']
    payload = {'request': {'attempt_id': 'w14-tactic-discovery', 'puzzle_id': 'w14-tactic',
               'deck_id': 'hangingPiece-easy', 'correct': False, 'clean': False,
               'source_fen': fen, 'moves': moves}, 'pack_id': 'hangingPiece-easy',
               'training_fen': fen, 'solution': moves}
    def raw():
        return psycopg.connect(**options, row_factory=postgres_store.tempo_row_factory)
    try:
        with raw() as db:
            result = tactic_commands.submit_tactic_attempt(postgres_store.PostgresConnection(db), payload)
        with raw() as db:
            assert tactic_commands.submit_tactic_attempt(postgres_store.PostgresConnection(db), payload) == result
            row = db.execute("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'", (result['card_id'],)).fetchone()
            assert row is not None
            entry = row[0]
        review = {'card_id': result['card_id'], 'review': {'outcome': 'correct', 'guided': False,
                  'queue_entry_id': entry, 'attempt_id': 'w14-tactic-review', 'expected_revision': 1}}
        try:
            with raw() as db:
                review_commands.submit_review(postgres_store.PostgresConnection(db), review)
                raise ValueError('rollback tactic review')
        except ValueError as error:
            assert str(error) == 'rollback tactic review'
        with raw() as db:
            assert db.execute('SELECT count(*) FROM review_attempt_receipts WHERE attempt_id=%s', ('w14-tactic-review',)).fetchone()[0] == 0
            saved = review_commands.submit_review(postgres_store.PostgresConnection(db), review)
            assert saved['persisted']
        with raw() as db:
            assert review_commands.submit_review(postgres_store.PostgresConnection(db), review) == saved
            assert db.execute('SELECT count(*) FROM review_attempt_receipts WHERE attempt_id=%s', ('w14-tactic-review',)).fetchone()[0] == 1
        print('PASS native tactic attempt and saved review rollback, commit and idempotent replay')
    finally:
        postgres_store.close_pools()
        os.environ.pop('TEMPO_DATABASE_READ_URL', None)
        os.environ.pop('TEMPO_DATABASE_WRITE_URL', None)
