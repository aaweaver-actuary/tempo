"""Scoped settings/evidence/coverage composition and savepoint ownership checks."""
import ast
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import repertoire_settings, review_commands
from app.contracts.review_conflicts import ReviewConflict
from app.services import postgres_coverage_candidates, study_mode


def test_six_control_compositions_delegate_all_sql_and_savepoints_to_database_owner():
    application = Path(__file__).resolve().parents[1] / 'app'
    for relative in ('repertoire_settings.py', 'review_commands.py', 'opening_evidence_api.py',
                     'services/study_mode.py', 'services/postgres_coverage_candidates.py',
                     'services/postgres_coverage_recovery.py'):
        path = application / relative
        calls = [node for node in ast.walk(ast.parse(path.read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{relative} still executes SQL or savepoint statements'


def test_repertoire_limit_and_study_mode_facts_keep_inheritance_and_missing_settings_error():
    with sqlite3.connect(':memory:') as database:
        database.executescript('''
            CREATE TABLE settings(id INTEGER PRIMARY KEY,new_cards_per_day INTEGER,study_only_mode INTEGER);
            CREATE TABLE repertoires(id TEXT PRIMARY KEY,new_cards_per_day INTEGER);
            INSERT INTO settings VALUES(1,10,0);
            INSERT INTO repertoires VALUES('inherited',NULL),('overridden',3);
        ''')
        assert repertoire_settings.effective_opening_limits(database) == {'inherited': 10, 'overridden': 3}
        assert repertoire_settings.repertoire_settings_response(database, 'inherited') == {
            'repertoire_id': 'inherited', 'new_cards_per_day': None, 'effective_new_cards_per_day': 10,
        }
        assert repertoire_settings.repertoire_settings_response(database, 'overridden') == {
            'repertoire_id': 'overridden', 'new_cards_per_day': 3, 'effective_new_cards_per_day': 3,
        }
        assert study_mode.study_only_enabled(database) is False
        database.execute('UPDATE settings SET study_only_mode=1')
        assert study_mode.study_only_enabled(database) is True
        database.execute('DELETE FROM settings')
        with pytest.raises(HTTPException) as error:
            study_mode.study_only_enabled(database)
        assert error.value.status_code == 503


def test_system_repertoire_settings_rejection_precedes_storage_read():
    class NoRead:
        def execute(self, *_arguments):
            raise AssertionError('System-repertoire guard read storage')
    with pytest.raises(HTTPException) as error:
        repertoire_settings.repertoire_settings_response(NoRead(), next(iter(repertoire_settings.SYSTEM_REPERTOIRES)))
    assert error.value.status_code == 404


@pytest.mark.parametrize('candidate_rows', [[], [
    {'move_uci': 'e2e4', 'explorer_probability': 0.6, 'maia_probability': 0.4},
    {'move_uci': 'd2d4', 'explorer_probability': None, 'maia_probability': 0.5},
    {'move_uci': 'c2c4', 'explorer_probability': 0.4, 'maia_probability': None},
    {'move_uci': 'g1f3', 'explorer_probability': None, 'maia_probability': None},
]])
def test_coverage_math_stays_between_candidate_read_and_single_projection_write(candidate_rows):
    operations = []
    class Database:
        def execute_native(self, statement, parameters=()):
            operations.append((statement, parameters))
            return SimpleNamespace(fetchall=lambda: candidate_rows)
    postgres_coverage_candidates.recalculate_coverage_node(
        Database(), 'node', {'reply_denominator': 2, 'cumulative_target': 0.9}, explorer_games=100,
    )
    assert operations[0][1] == ('node',)
    if not candidate_rows:
        assert len(operations) == 1
        return
    assert len(operations) == 2 and operations[1][1][1] == 'node'
    stored = json.loads(operations[1][1][0])
    assert [row['move_uci'] for row in stored] == [row['move_uci'] for row in candidate_rows]
    assert [row['source_state'] for row in stored] == ['blended', 'maia-only', 'explorer-only', 'unknown']
    assert sum(row['blended'] or 0 for row in stored) == pytest.approx(1)
    assert stored[-1]['blended'] is None and stored[-1]['required'] == 0


def test_handled_review_conflict_rolls_back_only_savepoint_and_keeps_outer_transaction(monkeypatch):
    with sqlite3.connect(':memory:') as database:
        database.execute('CREATE TABLE evidence(value TEXT)')
        database.execute("INSERT INTO evidence VALUES('saved-checkpoint')")
        assert database.in_transaction
        def submit(_database, _payload):
            database.execute("INSERT INTO evidence VALUES('new-conflicting-effect')")
            raise ReviewConflict('stale_review', 'Stored attempt changed')
        monkeypatch.setattr(review_commands, 'submit_review', submit)
        result = review_commands.reconcile_review(SimpleNamespace(raw=database), {})
        assert result == {'persisted': False, 'conflict': {
            'code': 'stale_review', 'message': 'Stored attempt changed', 'retryable': False,
        }}
        assert database.execute('SELECT value FROM evidence').fetchall() == [('saved-checkpoint',)]
        assert database.in_transaction
        database.rollback()
        assert database.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == 0
