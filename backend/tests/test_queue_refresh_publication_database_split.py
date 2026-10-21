"""Queue-refresh direct storage boundary and projection atomicity."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest

from app.services import postgres_queue_refresh as refresh


def test_queue_refresh_composition_has_no_direct_database_driver_calls():
    path = Path(__file__).resolve().parents[1] / 'app/services/postgres_queue_refresh.py'
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
    assert not calls


def test_projection_publication_rolls_back_lost_receipt_and_commits_once(monkeypatch):
    from app.services import prefix_transition_application, study_mode

    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE frozen_opening_activation(id INTEGER PRIMARY KEY,run_id TEXT,epoch INTEGER);
            INSERT INTO frozen_opening_activation VALUES(1,NULL,0);
            CREATE TABLE queue_projections(queue_date TEXT PRIMARY KEY,state TEXT,generation INTEGER,
                refresh_pending INTEGER,last_error TEXT,blocked_count INTEGER,updated_at TEXT);
            INSERT INTO queue_projections VALUES('2026-10-21','refreshing',5,1,NULL,0,'before');
        ''')
        callbacks = []
        lease = {'completed': False}

        class BorrowedDatabase:
            def execute(self, query, parameters=()):
                return database.execute(query, parameters)

            def execute_native(self, query, parameters=()):
                return database.execute(query.replace('%s', '?'), parameters)

        @contextmanager
        def write_section(*, background):
            assert background
            with database:
                yield BorrowedDatabase()

        @contextmanager
        def read_section():
            yield BorrowedDatabase()

        monkeypatch.setattr(refresh.postgres_store, 'configured', lambda: True)
        monkeypatch.setattr(refresh, 'connection', write_section)
        monkeypatch.setattr(refresh, 'background_read_connection', read_section)
        monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
        monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
        monkeypatch.setattr(refresh, 'lock_current_slice', lambda *_: True)
        monkeypatch.setattr(refresh, '_prepare_projection_blocked_count', lambda _: 3)
        monkeypatch.setattr(refresh, 'complete_task_slice_in_transaction', lambda *_: lease['completed'])
        monkeypatch.setattr(prefix_transition_application, 'enqueue_completion',
                            lambda *args: callbacks.append(args[1:]))
        task = {'id': 'task', 'generation': 7,
                'payload': {'queue_date': '2026-10-21', '_queue_phase': 'publish_projection'}}
        with pytest.raises(RuntimeError, match='publication lost its task lease'):
            refresh.execute_postgres_queue_refresh_slice(task)
        assert tuple(database.execute(
            'SELECT state,generation,refresh_pending,blocked_count,updated_at FROM queue_projections'
        ).fetchone()) == ('refreshing', 5, 1, 0, 'before')
        assert callbacks == []
        lease['completed'] = True
        assert refresh.execute_postgres_queue_refresh_slice(task) is False
        assert tuple(database.execute(
            'SELECT state,generation,refresh_pending,blocked_count FROM queue_projections'
        ).fetchone()) == ('ready', 6, 0, 3)
        assert callbacks == [('task', 7, '2026-10-21')]
