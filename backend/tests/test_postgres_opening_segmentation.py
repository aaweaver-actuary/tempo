"""AS-14,19,21: advisory worker generation/transaction/lifecycle boundaries."""
from contextlib import contextmanager
import json
from pathlib import Path
import re

import chess

from app.services import postgres_opening_segmentation as worker
from app.services import opening_segmentation


def test_segmentation_migration_has_unique_number_and_matches_schema_readiness():
    from app.schema_version import POSTGRES_SCHEMA_VERSION

    migration_directory = Path(__file__).resolve().parents[1] / 'migrations'
    migration_paths = sorted(migration_directory.glob('[0-9][0-9][0-9]_*.sql'))
    migration_numbers = [int(path.name[:3]) for path in migration_paths]
    assert len(migration_numbers) == len(set(migration_numbers))
    assert max(migration_numbers) == POSTGRES_SCHEMA_VERSION
    segmentation_migration, = migration_directory.glob('*_opening_segmentation.sql')
    recorded_version = re.search(
        r'INSERT INTO tempo_schema_migrations\(version\) VALUES \((\d+)\)',
        segmentation_migration.read_text(),
    )
    assert recorded_version is not None
    assert int(recorded_version.group(1)) == int(segmentation_migration.name[:3])


def test_segmentation_analysis_yields_restarts_and_replays_idempotently(monkeypatch):
    events = []
    read_open = False
    source = {'id': 'card', 'start_fen': chess.STARTING_FEN, 'trained_color': 'white',
              'moves_json': json.dumps(['e2e4','e7e5','g1f3']), 'revision': 1}
    class Cursor:
        def fetchone(self): return source
    class Read:
        def execute_native(self, *_args): return Cursor()
    @contextmanager
    def read_connection():
        nonlocal read_open
        read_open = True
        try: yield Read()
        finally: read_open = False
    original_traverse = opening_segmentation.presentation_occurrences
    def traverse(*args):
        assert not read_open
        events.append('traverse')
        return original_traverse(*args)
    monkeypatch.setattr(worker, 'background_read_connection', read_connection)
    monkeypatch.setattr(worker, 'presentation_occurrences', traverse)
    task = {'id': 'task', 'generation': 1, 'lease_token': 'first', 'payload': {
        'repertoire_id': 'rep', 'graph_generation': 2, 'content_version': 3}}
    prepared = worker.prepare_presentation(task)
    class WriterCursor:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def executemany(self, statement, values): events.append(('write', len(values), statement))
    class Database:
        raw = type('Raw', (), {'cursor': lambda _: WriterCursor()})()
    lease_current = True
    monkeypatch.setattr(worker, 'current_slice', lambda *_args: lease_current)
    def checkpoint(_database, _task, *, next_phase, next_payload):
        events.append(('checkpoint', next_phase, next_payload['after_card_id']))
        return True
    monkeypatch.setattr(worker, 'advance_task_slice_in_transaction', checkpoint)
    assert worker.stage_presentation(Database(), task, prepared)
    writes = len(events)
    lease_current = False  # The first process was superseded/reclaimed before replay.
    assert not worker.stage_presentation(Database(), task, prepared)
    assert len(events) == writes
    assert events[0] == 'traverse' and events[-1] == ('checkpoint', 'scan', 'card')
    assert all(event[1] <= 40 for event in events if isinstance(event, tuple) and event[0] == 'write')


def test_superseded_segmentation_generation_cannot_publish(monkeypatch):
    events = []
    class Cursor:
        def fetchone(self): return None
    class Database:
        def execute_native(self, statement, _parameters):
            events.append(statement)
            assert statement.startswith('SELECT')
            return Cursor()
    monkeypatch.setattr(worker, 'lock_current_slice', lambda *_args: True)
    monkeypatch.setattr(worker, 'complete_task_slice_in_transaction', lambda *_args: events.append('complete stale') or True)
    task = {'id': 'task', 'generation': 1, 'payload': {'repertoire_id': 'rep', 'content_version': 2, 'graph_generation': 1}}
    assert not worker.publish_recommendations(Database(), task)
    assert events[-1] == 'complete stale'


def test_group_preparation_reads_only_eight_indexed_occurrences_and_closes_connection(monkeypatch):
    statements = []
    class Cursor:
        def fetchall(self): return []
    class Read:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters)); return Cursor()
    @contextmanager
    def read(): yield Read()
    monkeypatch.setattr(worker, 'background_read_connection', read)
    task = {'id': 'task', 'generation': 1, 'payload': {'group_key': 'key', 'group_kind': 'transposition'}}
    assert worker.prepare_group_page(task) == ('transposition', 'key', (), ())
    assert statements[0][1][-1] == 8
    assert 'suffix_key=%s' in statements[0][0]


def test_advisory_service_never_writes_legacy_learning_or_queue_tables():
    from pathlib import Path
    source = Path(worker.__file__).read_text()
    for table in ['cards', 'reviews', 'daily_queue', 'prefix_splits']:
        assert f'UPDATE {table} ' not in source
        assert f'INSERT INTO {table}(' not in source
        assert f'DELETE FROM {table} ' not in source
