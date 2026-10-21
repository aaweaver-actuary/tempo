"""Focused boundaries for legacy priority preparation/retention relocation."""
import ast
from pathlib import Path
import sqlite3

import pytest

from app.services import postgres_priority, priority_retention
from tempo_legacy_database.adapters.priority_lifecycle import preparation


def test_priority_lifecycle_application_delegates_database_and_dialect_operations():
    base = Path(__file__).resolve().parents[1] / 'app/services'
    for filename in ('postgres_priority.py', 'priority_retention.py'):
        calls = [node for node in ast.walk(ast.parse((base / filename).read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'executemany', 'execute_native', 'cursor', 'configured'}]
        assert not calls, filename


@pytest.mark.parametrize('source_version', ['7:2', None])
def test_priority_lease_keeps_source_job_and_task_lock_order(monkeypatch, source_version):
    database = object()
    task = {'id': 'task', 'generation': 1, 'lease_token': 'lease'}
    events = []

    def lease_row(connection, task_id):
        assert connection is database and task_id == 'task'
        events.append('lease')
        return {'generation': 1, 'lease_token': 'lease', 'state': 'leased'}

    def epoch_version(connection, repertoire_id, *, lock=False):
        assert connection is database and repertoire_id == 'rep' and lock
        events.append('source')
        return source_version

    def job_row(connection, repertoire_id):
        assert connection is database and repertoire_id == 'rep'
        events.append('job')
        return {'generation': 5, 'status': 'running'}

    def lock_task(connection, current_task):
        assert connection is database and current_task is task
        events.append('task')
        return True

    monkeypatch.setattr(preparation, 'task_lease', lease_row)
    monkeypatch.setattr(postgres_priority, '_priority_source_version', epoch_version)
    monkeypatch.setattr(preparation, 'locked_current_job', job_row)
    monkeypatch.setattr(postgres_priority, 'lock_current_slice', lock_task)
    assert postgres_priority._has_current_priority_lease(database, task, 'rep', 5) is (source_version is not None)
    assert events == (['lease', 'source', 'job', 'task'] if source_version else ['lease', 'source'])


def test_source_epoch_identity_creates_only_existing_repertoire_and_keeps_shared_version():
    with sqlite3.connect(':memory:') as database:
        database.executescript('''
            CREATE TABLE repertoires(id TEXT PRIMARY KEY);
            CREATE TABLE priority_source_epoch(id INTEGER PRIMARY KEY,version INTEGER);
            CREATE TABLE priority_repertoire_source_epochs(repertoire_id TEXT PRIMARY KEY,version INTEGER);
            INSERT INTO repertoires VALUES('existing');
            INSERT INTO priority_source_epoch VALUES(1,7);
        ''')
        assert postgres_priority._priority_source_version(database, 'existing') == '7:0'
        database.execute("UPDATE priority_repertoire_source_epochs SET version=2 WHERE repertoire_id='existing'")
        assert postgres_priority._priority_source_version(database, 'existing') == '7:2'
        assert postgres_priority._priority_source_version(database, 'missing') is None
        assert database.execute('SELECT repertoire_id,version FROM priority_repertoire_source_epochs').fetchall() == [('existing', 2)]
        assert database.execute('SELECT version FROM priority_source_epoch WHERE id=1').fetchone()[0] == 7
