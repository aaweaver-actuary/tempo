"""SQLite frozen-order DDL has a database owner and needs no application import."""
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tempo_legacy_database.ddl.frozen_opening_schema import GUARDED_TABLES, SCHEMA, SQLITE_GUARDS


def test_frozen_sqlite_schema_import_is_independent_of_application_and_connections():
    probe = r'''
from importlib.abc import MetaPathFinder
import sys
sys.path.insert(0, sys.argv[1])
class RejectApplication(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app' or fullname.startswith('app.'):
            raise AssertionError('DDL imported application code: ' + fullname)
def reject_effects(event, arguments):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect', 'subprocess.Popen'}:
        raise AssertionError('DDL opened infrastructure: ' + event)
sys.meta_path.insert(0, RejectApplication())
sys.addaudithook(reject_effects)
from tempo_legacy_database.ddl.frozen_opening_schema import SCHEMA, SQLITE_GUARDS
assert len(SCHEMA) == 6 and len(SQLITE_GUARDS) == 51
assert 'tempo_legacy_database.connections' not in sys.modules
'''
    database_root = Path(__file__).resolve().parents[2] / 'database'
    result = subprocess.run([sys.executable, '-I', '-c', probe, str(database_root)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_frozen_sqlite_schema_replay_preserves_activation_and_foreign_keys():
    with sqlite3.connect(':memory:') as database:
        database.execute('PRAGMA foreign_keys=ON')
        for statement in [*SCHEMA, *SCHEMA]:
            database.execute(statement)
        assert database.execute('SELECT id,run_id,epoch FROM frozen_opening_activation').fetchall() == [(1, None, 0)]
        database.execute("INSERT INTO frozen_opening_runs VALUES('run','checksum','{}',NULL,'now','reader',0)")
        database.execute("INSERT INTO frozen_opening_pending VALUES('run')")
        for statement in SCHEMA:
            database.execute(statement)
        assert database.execute('SELECT run_id FROM frozen_opening_pending').fetchall() == [('run',)]
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("INSERT INTO frozen_opening_pending VALUES('absent-run')")
        database.execute("INSERT INTO frozen_opening_ranks VALUES('run','card',1,'fingerprint','[]','{}')")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("INSERT INTO frozen_opening_ranks VALUES('run','other-card',1,'fingerprint','[]','{}')")


@pytest.mark.parametrize('operation', ['INSERT', 'UPDATE', 'DELETE'])
def test_frozen_sqlite_guards_invalidate_only_pending_runs_in_the_callers_transaction(operation):
    with sqlite3.connect(':memory:') as database:
        for statement in SCHEMA:
            database.execute(statement)
        database.execute("INSERT INTO frozen_opening_runs VALUES('pending','pending','{}',NULL,'now','reader',0)")
        database.execute("INSERT INTO frozen_opening_runs VALUES('accepted','accepted','{}',NULL,'now','reader',0)")
        database.execute("INSERT INTO frozen_opening_pending VALUES('pending')")
        for table in GUARDED_TABLES:
            database.execute(f'CREATE TABLE {table}(id INTEGER)')
            database.execute(f'INSERT INTO {table} VALUES(1)')
        for statement in SQLITE_GUARDS:
            database.execute(statement)
        database.commit()
        for table in GUARDED_TABLES:
            database.execute('BEGIN')
            statement = {'INSERT': f'INSERT INTO {table} VALUES(2)',
                         'UPDATE': f'UPDATE {table} SET id=2',
                         'DELETE': f'DELETE FROM {table}'}[operation]
            database.execute(statement)
            assert dict(database.execute('SELECT run_id,invalidated FROM frozen_opening_runs')) == {'pending': 1, 'accepted': 0}
            database.rollback()
            assert database.execute("SELECT invalidated FROM frozen_opening_runs WHERE run_id='pending'").fetchone() == (0,)
