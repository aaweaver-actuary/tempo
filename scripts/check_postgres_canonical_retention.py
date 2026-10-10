"""Populated canonical-prefix retention proofs shared with the regular suite."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter
from threading import Barrier, Event
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import chess
from app import database as application_database, postgres_store
from app.canonical_prefix_api import save_prefix
from app.services import canonical_prefix_preview as previews
from app.services.canonical_prefix import read_prefix, store_positions, validate_scoped_line
from app.services import redis_admission_gate
from app.services.durable_tasks import (
    claim_task, requeue_interrupted_tasks, defer_task_for_contention, defer_task_for_foreground,
)


ROUTE = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'f8c5', 'c2c3',
         'g8f6', 'd2d3', 'd7d6']


def seed_retention_fixture(database, repertoire_id):
    """Twelve previews; three obsolete scans each own 36 child/event rows."""
    for ordinal in range(4):
        database.execute(
            'INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) '
            "VALUES(?,?,?,'white',?,?,'2026-01-01')",
            (f'{repertoire_id}-line-{ordinal}', repertoire_id, f'Line {ordinal}',
             chess.STARTING_FEN, json.dumps(ROUTE)),
        )
    active = previews.request_preview(database, repertoire_id, ROUTE[:5])
    database.execute("UPDATE canonical_prefix_previews SET state='ready' WHERE id=?", (active['preview_id'],))
    save_prefix(database, {'repertoire_id': repertoire_id, 'request': {
        'preview_id': active['preview_id'], 'expected_revision': active['revision']}})
    database.execute("UPDATE background_tasks SET state='complete' WHERE kind='canonical_prefix_preview' AND deduplication_key=?",
                     (active['preview_id'],))
    preview_ids = [active['preview_id']]
    preview_moves = [ROUTE[:5]]
    for length in range(len(ROUTE) + 1):
        preview_ids.append(previews.request_preview(database, repertoire_id, ROUTE[:length])['preview_id'])
        preview_moves.append(ROUTE[:length])
    source_revision = read_prefix(database, repertoire_id)['source_revision']
    task_ids = []
    for ordinal, (preview_id, moves) in enumerate(zip(preview_ids, preview_moves)):
        database.execute('UPDATE canonical_prefix_previews SET created_at=? WHERE id=?',
                         (f'2026-01-01T00:00:{ordinal:02d}+00:00', preview_id))
        validation = validate_scoped_line(chess.STARTING_FEN, ROUTE, moves, [])
        store_positions(database, preview_id, validation['positions'], source_revision=source_revision)
        for line_ordinal in range(4):
            database.execute(
                'INSERT INTO canonical_prefix_results(preview_id,item_id,name,status,origin_json,scope_start_ply) '
                "VALUES(?,?,?,'valid','[]',?)",
                (preview_id, f'line:{repertoire_id}-line-{line_ordinal}', f'Line {line_ordinal}', len(moves)),
            )
        task = database.execute("SELECT id,generation,payload_json FROM background_tasks WHERE kind='canonical_prefix_preview' AND deduplication_key=?",
                                (preview_id,)).fetchone()
        task_ids.append(task['id'])
        for event_ordinal in range(20):
            database.execute(
                'INSERT INTO background_task_events(task_id,generation,event,phase,created_at) '
                "VALUES(?,?,'slice_complete','lines','2026-01-01')", (task['id'], task['generation']),
            )
        if ordinal:
            payload = {**json.loads(task['payload_json']), 'phase': 'retention'}
            database.execute("UPDATE background_tasks SET phase='retention',payload_json=? WHERE id=?",
                             (json.dumps(payload), task['id']))
    database.execute('UPDATE background_tasks SET priority=-1000 WHERE id=?', (task_ids[-1],))
    return {'repertoire_id': repertoire_id, 'active_id': preview_ids[0],
            'preview_ids': preview_ids, 'task_ids': task_ids,
            'owner_id': preview_ids[-1], 'owner_task_id': task_ids[-1],
            'retained_ids': set(preview_ids[-8:]) | {preview_ids[0]},
            'obsolete_ids': preview_ids[1:4]}


class RetentionMeasurements:
    """Measure cleanup separately from unchanged checkpoint/diagnostic writes."""

    def __init__(self, owner_task_id, *, service_module=previews, on_scope_lock=None):
        self.owner_task_id = owner_task_id
        self.service_module = service_module
        self.on_scope_lock = on_scope_lock
        self.slices = []

    @contextmanager
    def capture(self):
        original_connection = self.service_module.connection
        measurement = {'cleanup_rows': 0, 'cleanup_statements': 0, 'auxiliary_trigger_rows': 0}
        owner_task_id = self.owner_task_id
        on_scope_lock = self.on_scope_lock

        class MeasuredConnection:
            def __init__(self, database):
                self.database = database

            def __getattr__(self, name):
                return getattr(self.database, name)

            def execute(self, statement, parameters=()):
                changes_before = getattr(self.database, 'total_changes', None)
                cursor = self.database.execute(statement, parameters)
                if 'FROM repertoires' in statement and 'FOR UPDATE' in statement:
                    measurement['scope_locked_at'] = perf_counter()
                    if on_scope_lock:
                        on_scope_lock()
                match = re.match(r'\s*(?:UPDATE|DELETE FROM)\s+(\w+)', statement, re.IGNORECASE)
                if match and match[1] in {'canonical_prefix_previews', 'canonical_prefix_results',
                                          'canonical_prefix_positions', 'background_tasks', 'background_task_events'}:
                    if match[1].startswith('canonical_prefix_') or owner_task_id not in parameters:
                        measurement['cleanup_statements'] += 1
                        # Deletes include any SQLite cascades. The existing age-origin
                        # UPDATE trigger is bookkeeping, not another cleanup row.
                        changed_rows = max(0, cursor.rowcount)
                        if changes_before is not None:
                            actual_changes = self.database.total_changes - changes_before
                            if statement.lstrip().upper().startswith('DELETE'):
                                changed_rows = actual_changes
                            else:
                                measurement['auxiliary_trigger_rows'] += actual_changes - changed_rows
                        measurement['cleanup_rows'] += changed_rows
                return cursor

        @contextmanager
        def measured_connection(**options):
            started_at = perf_counter()
            try:
                with original_connection(**options) as database:
                    if hasattr(database, 'raw'):
                        settings = tuple(database.raw.execute(
                            "SELECT current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone())
                        assert settings == ('250ms', '25ms'), settings
                        measurement['effective_settings'] = settings
                    yield MeasuredConnection(database)
            finally:
                finished_at = perf_counter()
                measurement['connection_seconds'] = finished_at - started_at
                if 'scope_locked_at' in measurement:
                    measurement['scope_lock_upper_bound_seconds'] = finished_at - measurement.pop('scope_locked_at')

        try:
            with patch.object(self.service_module, 'connection', measured_connection):
                yield measurement
            measurement['outcome'] = 'committed'
        except Exception:
            measurement['outcome'] = 'rolled_back'
            raise
        finally:
            self.slices.append(measurement)


def run_retention_fixture(fixture, *, maximum_slices=500, measurements=None, restart=False):
    measurements = measurements or RetentionMeasurements(fixture['owner_task_id'])
    for _ in range(maximum_slices):
        task = claim_task('canonical_prefix_preview')
        assert task and task['id'] == fixture['owner_task_id'], task
        with measurements.capture():
            assert previews.execute_prefix_preview_slice(task)
        with application_database.read_connection() as database:
            state = database.execute('SELECT state FROM background_tasks WHERE id=?', (task['id'],)).fetchone()[0]
        if state == 'complete':
            return measurements
        if restart:
            postgres_store.close_pools()
    raise AssertionError(f'Retention exceeds {maximum_slices} admitted slices')


def _new_postgres_fixture():
    import uuid
    repertoire_id = 'canonical-retention-' + uuid.uuid4().hex
    with application_database.connection() as database:
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Retention proof','synthetic.pgn','2026-01-01')",
                         (repertoire_id,))
        return seed_retention_fixture(database, repertoire_id)


def _cleanup_postgres_fixture(fixture):
    with application_database.connection() as database:
        database.execute("DELETE FROM background_tasks WHERE json_extract(payload_json,'$.repertoire_id')=?",
                         (fixture['repertoire_id'],))
        database.execute('DELETE FROM repertoires WHERE id=?', (fixture['repertoire_id'],))


def _assert_postgres_survivors(fixture, retained_ids=None):
    retained_ids = retained_ids or fixture['retained_ids']
    with application_database.read_connection() as database:
        assert {row[0] for row in database.execute('SELECT id FROM canonical_prefix_previews WHERE repertoire_id=?',
                                                  (fixture['repertoire_id'],))} == retained_ids
        for preview_id, task_id in zip(fixture['preview_ids'], fixture['task_ids']):
            retained = preview_id in retained_ids
            assert bool(database.execute('SELECT 1 FROM background_tasks WHERE id=?', (task_id,)).fetchone()) == retained
            for table in ('canonical_prefix_results', 'canonical_prefix_positions'):
                assert bool(database.execute(f'SELECT 1 FROM {table} WHERE preview_id=?', (preview_id,)).fetchone()) == retained
            assert bool(database.execute('SELECT 1 FROM background_task_events WHERE task_id=?', (task_id,)).fetchone()) == retained


def _prove_foreground_overlap(fixture, service_module):
    from queue import Queue
    import psycopg
    executor = ThreadPoolExecutor(max_workers=1)
    announced_pid = Queue()
    futures = []
    latency = []
    owner = claim_task('canonical_prefix_preview')

    def request_in_foreground():
        with redis_admission_gate.foreground_lease(), application_database.connection() as database:
            announced_pid.put(database.raw.info.backend_pid)
            started_at = perf_counter()
            response = previews.request_preview(database, fixture['repertoire_id'], ['d2d4'])
        latency.append(perf_counter() - started_at)
        return response

    def on_scope_lock():
        if futures:
            return
        futures.append(executor.submit(request_in_foreground))
        waiting_pid = announced_pid.get(timeout=2)
        # Observe the real lock dependency; no timer sleep establishes overlap.
        with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as observer:
            deadline = perf_counter() + 0.15
            while perf_counter() < deadline:
                if observer.execute('SELECT cardinality(pg_blocking_pids(%s))', (waiting_pid,)).fetchone()[0]:
                    return
        raise AssertionError('Foreground preview did not overlap the retention scope lock')

    measurements = RetentionMeasurements(owner['id'], service_module=service_module, on_scope_lock=on_scope_lock)
    try:
        with measurements.capture():
            assert previews.execute_prefix_preview_slice(owner)
        assert futures[0].result(timeout=2)['preview_id']
        assert latency[0] < 0.25, latency
        return {'foreground_preview_seconds': latency[0], 'retention': measurements.slices[0]}
    finally:
        executor.shutdown(wait=True)


def _prove_postgres_contention_restart_replay(fixture):
    from psycopg.errors import LockNotAvailable
    task = claim_task('canonical_prefix_preview')
    with application_database.read_connection() as database:
        original_positions = database.execute('SELECT COUNT(*) FROM canonical_prefix_positions').fetchone()[0]
    with redis_admission_gate.foreground_lease():
        try:
            previews.execute_prefix_preview_slice(task)
        except redis_admission_gate.BackgroundAdmissionDeferred:
            pass
        else:
            raise AssertionError('Retention ignored foreground admission')
    with postgres_store.connection() as foreground, ThreadPoolExecutor(max_workers=1) as executor:
        read_prefix(foreground, fixture['repertoire_id'], lock=True)
        future = executor.submit(previews.execute_prefix_preview_slice, task)
        try:
            future.result(timeout=2)
        except LockNotAvailable:
            pass
        else:
            raise AssertionError('Retention ignored the held repertoire lock')
    with application_database.read_connection() as database:
        assert database.execute('SELECT COUNT(*) FROM canonical_prefix_positions').fetchone()[0] == original_positions
    def cleanup_state():
        with application_database.read_connection() as database:
            snapshot = {table: [tuple(row) for row in database.execute(
                f'SELECT * FROM {table} ORDER BY 1,2')]
                for table in ('canonical_prefix_previews', 'canonical_prefix_results',
                              'canonical_prefix_positions', 'background_tasks', 'background_task_events')}
        return snapshot

    before_checkpoint = cleanup_state()

    def fail_checkpoint(*args, **kwargs):
        raise RuntimeError('controlled PostgreSQL retention checkpoint interruption')

    with patch.object(previews, 'advance_task_slice_in_transaction', fail_checkpoint):
        try:
            previews.execute_prefix_preview_slice(task)
        except RuntimeError as error:
            assert 'checkpoint interruption' in str(error), error
        else:
            raise AssertionError('Controlled checkpoint interruption did not run')
    assert cleanup_state() == before_checkpoint
    assert previews.execute_prefix_preview_slice(task)
    with application_database.read_connection() as database:
        after_batch_positions = database.execute('SELECT COUNT(*) FROM canonical_prefix_positions').fetchone()[0]
    assert not previews.execute_prefix_preview_slice(task)
    interrupted = claim_task('canonical_prefix_preview')
    with application_database.connection() as database:
        database.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=?", (interrupted['id'],))
    postgres_store.close_pools()
    requeue_interrupted_tasks()
    assert not previews.execute_prefix_preview_slice(interrupted)
    with application_database.read_connection() as database:
        assert database.execute('SELECT COUNT(*) FROM canonical_prefix_positions').fetchone()[0] == after_batch_positions
    run_retention_fixture(fixture, maximum_slices=2, restart=True)
    _assert_postgres_survivors(fixture)


def _prove_postgres_activation_race(fixture):
    task = claim_task('canonical_prefix_preview')
    activated_id = fixture['obsolete_ids'][0]
    original_read = previews.background_read_connection

    @contextmanager
    def activate_after_preparation(**options):
        with original_read(**options) as database:
            yield database
        with application_database.connection() as database:
            database.execute("UPDATE canonical_prefix_previews SET state='ready' WHERE id=?", (activated_id,))
            save_prefix(database, {'repertoire_id': fixture['repertoire_id'], 'request': {
                'preview_id': activated_id, 'expected_revision': 1}})
    with patch.object(previews, 'background_read_connection', activate_after_preparation):
        assert previews.execute_prefix_preview_slice(task)
    run_retention_fixture(fixture, maximum_slices=3, restart=True)
    expected_ids = fixture['retained_ids'] - {fixture['active_id']} | {activated_id}
    _assert_postgres_survivors(fixture, expected_ids)


def _prove_postgres_concurrent_retention(fixture):
    from psycopg.errors import LockNotAvailable
    first = claim_task('canonical_prefix_preview')
    with application_database.connection() as database:
        database.execute('UPDATE background_tasks SET priority=-2000 WHERE id=?', (fixture['task_ids'][-2],))
    second = claim_task('canonical_prefix_preview')
    assert second['id'] == fixture['task_ids'][-2]
    prepared = Barrier(2)
    first_prepared = Event()
    original_read = previews.background_read_connection

    @contextmanager
    def synchronized_preparation(**options):
        with original_read(**options) as database:
            yield database
        first_prepared.set()
        prepared.wait(timeout=2)
    with patch.object(previews, 'background_read_connection', synchronized_preparation), ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(previews.execute_prefix_preview_slice, first)
        assert first_prepared.wait(timeout=2), 'First preparation did not close its database section'
        futures = [first_future, executor.submit(previews.execute_prefix_preview_slice, second)]
        deferred = []
        for task, future in zip((first, second), futures):
            try:
                assert future.result(timeout=2)
            except (LockNotAvailable, redis_admission_gate.BackgroundAdmissionDeferred) as error:
                deferred.append((task, error))
    for task, error in deferred:
        if isinstance(error, redis_admission_gate.BackgroundAdmissionDeferred):
            assert defer_task_for_foreground(task)
        else:
            assert defer_task_for_contention(task['id'], task['generation'], task['lease_token'], kind=task['kind'])
    # Existing admission serializes database sections; denied deliveries keep intent.
    for _ in range(20):
        with application_database.read_connection() as database:
            count = database.execute('SELECT COUNT(*) FROM canonical_prefix_previews WHERE repertoire_id=?',
                                     (fixture['repertoire_id'],)).fetchone()[0]
        if count == 9:
            break
        task = claim_task('canonical_prefix_preview')
        assert task
        assert previews.execute_prefix_preview_slice(task)
    else:
        raise AssertionError('Concurrent retention did not converge')
    _assert_postgres_survivors(fixture)
    assert not previews.execute_prefix_preview_slice(first)
    assert not previews.execute_prefix_preview_slice(second)


def prove_canonical_retention(*, mode='candidate', service_module=previews):
    """Run inside an exclusively owned PostgreSQL/Redis rehearsal namespace."""
    report = {'mode': mode, 'slice_budget': 64, 'victim_budget': 4, 'proofs': []}
    fixture = _new_postgres_fixture()
    try:
        measurements = RetentionMeasurements(fixture['owner_task_id'], service_module=service_module)
        started_at = perf_counter()
        # Measure steady-state cleanup without charging repeated pool creation to
        # every row. Actual process/pool restart has its own interrupted-batch proof.
        run_retention_fixture(fixture, measurements=measurements)
        report['direct_convergence_seconds'] = perf_counter() - started_at
        report['slices'] = measurements.slices
        report['slice_count'] = len(measurements.slices)
        report['cleanup_rows'] = sum(item['cleanup_rows'] for item in measurements.slices)
        _assert_postgres_survivors(fixture)
        assert report['cleanup_rows'] == 120, report
        if mode == 'candidate':
            assert report['slice_count'] <= 3, report
            assert max(item['cleanup_rows'] for item in measurements.slices) <= 64, report
        report['proofs'].append('test_postgres_canonical_prefix_retention_converges_within_three_admitted_slices')
    finally:
        _cleanup_postgres_fixture(fixture)
    fixture = _new_postgres_fixture()
    try:
        report['foreground_overlap'] = _prove_foreground_overlap(fixture, service_module)
        report['proofs'].append('test_postgres_canonical_prefix_retention_foreground_preview_latency')
    finally:
        _cleanup_postgres_fixture(fixture)
    if mode == 'candidate':
        for proof in (_prove_postgres_contention_restart_replay, _prove_postgres_activation_race, _prove_postgres_concurrent_retention):
            fixture = _new_postgres_fixture()
            try:
                proof(fixture)
                report['proofs'].append('test_postgres_canonical_prefix_retention_' + proof.__name__.removeprefix('_prove_postgres_'))
            finally:
                _cleanup_postgres_fixture(fixture)
    for proof in report['proofs']:
        print('PASS ' + proof, flush=True)
    print('CANONICAL_RETENTION_EVIDENCE ' + json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    import check_postgres_graph_retention as fixtures
    parent_database_url = os.environ['TEMPO_DATABASE_WRITE_URL']
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url):
        fixtures.validate_disposable_database()
        with fixtures.owned_fixture_database():
            prove_canonical_retention()
