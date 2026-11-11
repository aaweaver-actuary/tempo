"""Frozen occurrence reader boundary using one disposable PostgreSQL owner.

The injected delay is deterministic; it does not measure live graph latency.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
import json
import sys
import time
from unittest.mock import patch

import psycopg
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/tests'), str(ROOT / 'backend/tests')]
from test_product_game_lapse_postgres import owned_postgres
from queue_refresh_scale_fixture import seed_queue_refresh_case
from tempo_storage import postgres
from tempo_storage.queue_refresh import reads
from app.services import durable_tasks, postgres_queue_refresh as refresh, queue_application
from app.product_composition.database_infrastructure import bind_database_infrastructure


def test_frozen_occurrence_snapshot_continuation(owned_postgres, monkeypatch):
    bind_database_infrastructure()
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    options = owned_postgres.connection_options
    dsn = make_conninfo(**options)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', dsn)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', dsn)
    postgres.prepare_authoritative_background_pool()
    try:
        with psycopg.connect(**options, row_factory=postgres.tempo_row_factory) as raw:
            facts = seed_queue_refresh_case(raw, queued_count=32, issued_count=2, frozen_active=True)
            raw.execute("UPDATE cards SET state='new',introduced_at=NULL WHERE id=ANY(%s)", ([f'queue-refresh-scale-{number:05}' for number in range(1, 33)],))
            raw.execute("DELETE FROM reviews WHERE card_id LIKE 'queue-refresh-scale-%'")
            raw.execute("UPDATE settings SET new_cards_per_day=100 WHERE id=1")
            raw.execute("UPDATE repertoires SET new_cards_per_day=100 WHERE id='queue-refresh-scale'")
            raw.execute('DELETE FROM background_tasks WHERE id=%s', (facts.task_id,))
            task = queue_application.request_queue_refresh_in_transaction(postgres.PostgresConnection(raw), facts.queue_date)
            epoch = raw.execute('SELECT requested_refresh_epoch FROM queue_projections WHERE queue_date=%s', (facts.queue_date,)).fetchone()[0]
            payload = {'queue_date': facts.queue_date, '_queue_phase': 'study_opening_page',
                       '_refresh_cycle_epoch': epoch, 'frozen_token': ['queue-refresh-scale', 1]}
            raw.execute("UPDATE background_tasks SET phase='study_opening_page',payload_json=%s WHERE id=%s", (json.dumps(payload), task['id']))
            # Independent compact oracle from actual persisted rank and route
            # facts, rather than assuming an ordering from generated IDs.
            expected = [row[0] for row in raw.execute('''SELECT c.id FROM cards c
                JOIN opening_graph_steps step ON step.card_id=c.id
                JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
                JOIN repertoires r ON r.id=step.repertoire_id
                JOIN repertoire_lines line ON line.id=step.line_id AND line.repertoire_id=r.id
                JOIN frozen_opening_activation active ON active.id=1
                JOIN frozen_opening_ranks rank ON rank.run_id=active.run_id AND rank.card_id=c.id
                WHERE c.state='new' AND c.introduced_at IS NULL AND publication.state='ready'
                ORDER BY rank.ordinal,r.created_at,r.id,line.created_at,line.id,step.decision_index,c.id''')]
            assert len(expected) == 32
        original_execute = postgres.PostgresConnection.execute_native
        original_read = getattr(reads, 'read_connection', None)
        original_lock = refresh.lock_current_slice
        held, release = Event(), Event()
        active = [False]
        in_background = [False]
        original_gate = refresh.activity_gate.background_database_section
        @contextmanager
        def gate():
            with original_gate():
                in_background[0] = True
                try:
                    yield
                finally:
                    in_background[0] = False
        observations, results, writers, foreground = [], [], [], []
        class CursorCapture:
            def __init__(self, cursor, label):
                self.cursor, self.label = cursor, label
            def fetchall(self):
                rows = self.cursor.fetchall()
                results.append((self.label, [tuple(row) for row in rows]))
                return rows
        def execute(db, statement, parameters=()):
            if statement.lstrip().startswith('SELECT repertoire.created_at') and 'opening_graph_steps' in statement:
                label = 'upper' if ' DESC LIMIT 1' in statement else 'page'
                settings = tuple(original_execute(db, "SELECT current_setting('transaction_read_only'),current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone())
                observations.append((label, settings))
                if original_read is not None:
                    assert active[0] and not in_background[0]
                if label == 'upper':
                    held.set()
                    assert release.wait(10)
                original_execute(db, 'SELECT pg_sleep(0.32)')
                return CursorCapture(original_execute(db, statement, parameters), label)
            return original_execute(db, statement, parameters)
        @contextmanager
        def reader(*args, **kwargs):
            assert kwargs.get('repertoire_snapshot') is True
            active[0] = True
            try:
                with original_read(*args, **kwargs) as database:
                    yield database
            finally:
                active[0] = False
        def writer(db, delivery):
            assert not active[0], 'source snapshot must close before the task writer'
            writers.append(tuple(db.execute_native("SELECT current_setting('transaction_read_only'),current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone()))
            return original_lock(db, delivery)
        first = durable_tasks.claim_task('daily_queue')
        assert first
        reader_patch = patch.object(reads, 'read_connection', reader) if original_read else __import__('contextlib').nullcontext()
        with patch.object(refresh.activity_gate, 'background_database_section', gate), patch.object(postgres.PostgresConnection, 'execute_native', execute), reader_patch, patch.object(refresh, 'lock_current_slice', writer):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(refresh.execute_postgres_queue_refresh_slice, first)
                assert held.wait(10)
                started = time.perf_counter()
                with postgres.connection() as db:
                    queue_application.request_queue_refresh_in_transaction(db, facts.queue_date)
                foreground.append(round((time.perf_counter()-started)*1000, 1))
                assert not future.done()
                release.set()
                assert future.result(timeout=30)
            second = durable_tasks.claim_task('daily_queue')
            assert second and second['payload']['_queue_phase'] == 'study_opening_page'
            assert refresh.execute_postgres_queue_refresh_slice(second)
        assert [name for name, _ in observations] == ['upper', 'page', 'page']
        assert all(settings == ('on', '0', '100ms') for _, settings in observations)
        assert writers == [('off', '250ms', '25ms')] * 2
        upper = results[0][1]
        pages = [row for label, batch in results if label == 'page' for row in batch]
        assert len(upper) == 1 and len(pages) == 32
        assert [row[5] for row in pages] == expected
        assert upper[0][5] == expected[-1]
        assert len({row[5] for row in pages}) == 32
        print('PASS frozen upper and two ordered continuation pages; snapshot', observations,
              'writer', writers, 'foreground_ms', foreground)
    finally:
        postgres.close_pools()
