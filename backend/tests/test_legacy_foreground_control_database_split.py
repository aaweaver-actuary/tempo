"""Foreground persistence boundaries preserve split guards, replay and rollback."""
import ast
from datetime import date
from pathlib import Path
import sqlite3

from fastapi import HTTPException
import pytest

from app import prefix_split_commands


class PrefixSqlite:
    """Exercise portable extracted statements; native lock behavior is unverified."""
    def __init__(self, database):
        self.database = database
        self.statements = []

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        return self.database.execute(statement.replace(' FOR UPDATE', ''), parameters)

    def execute_native(self, statement, parameters=()):
        return self.execute(statement.replace('%s', '?'), parameters)


@pytest.fixture
def prefix_database():
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE cards(id TEXT PRIMARY KEY,pending_validation INTEGER,
                prefix_split_rejected_after_review_id INTEGER);
            CREATE TABLE repertoire_cards(repertoire_id TEXT,card_id TEXT);
            CREATE TABLE opening_graph_publications(repertoire_id TEXT,generation INTEGER);
            CREATE TABLE opening_graph_steps(repertoire_id TEXT,generation INTEGER,line_id TEXT,card_id TEXT);
            CREATE TABLE reviews(id INTEGER PRIMARY KEY,card_id TEXT,rating TEXT,source_kind TEXT,invalidated_at TEXT);
            INSERT INTO cards VALUES('parent',0,0),('child',0,0);
            INSERT INTO repertoire_cards VALUES('z','parent'),('a','parent'),('z','child');
            INSERT INTO opening_graph_publications VALUES('a',1),('z',2);
            INSERT INTO opening_graph_steps VALUES('a',1,'line-a','parent'),('a',1,'line-b','parent'),
                ('z',2,'line-z','parent'),('a',0,'old-line','parent');
            INSERT INTO reviews VALUES(3,'parent','again','study',NULL),(4,'parent','good','study',NULL),
                (5,'parent','again','practice',NULL),(6,'parent','again','study','invalid');
        ''')
        yield PrefixSqlite(database)


def test_three_foreground_control_compositions_delegate_all_sql_to_database_owner():
    application = Path(__file__).resolve().parents[1] / 'app'
    for name in ('settings_commands', 'repertoire_commands', 'prefix_split_commands'):
        calls = [node for node in ast.walk(ast.parse((application / f'{name}.py').read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{name} still executes SQL directly'


@pytest.mark.parametrize('idempotent', [False, True])
def test_prefix_accept_retains_lock_order_linked_invalidation_and_caller_rollback(prefix_database, monkeypatch, idempotent):
    effects = []
    monkeypatch.setattr(prefix_split_commands, 'lock_queue_date_for_position',
                        lambda _database, day: effects.append(('queue-lock', day)))

    def split(_database, card_id, revision):
        assert prefix_database.statements[0][0] == 'SELECT id FROM cards WHERE id=? FOR UPDATE'
        assert effects == [('queue-lock', date.today().isoformat())]
        return {'idempotent': idempotent, 'parent': {'card_id': card_id}, 'continuation': {'card_id': 'child'}}

    monkeypatch.setattr(prefix_split_commands, 'apply_prefix_split', split)
    monkeypatch.setattr(prefix_split_commands, 'invalidate_integrity_in_transaction',
                        lambda _database, repertoire: effects.append(('integrity', repertoire)))
    monkeypatch.setattr(prefix_split_commands, 'request_graph_rebuild_in_transaction',
                        lambda _database, repertoire, day: effects.append(('graph', repertoire, day)))
    result = prefix_split_commands.accept_prefix_split(prefix_database, {
        'card_id': 'parent', 'request': {'expected_revision': 1},
    })
    assert result['shared_line_count'] == 3 and result['shared_repertoire_count'] == 2
    assert [row[0] for row in prefix_database.database.execute('SELECT pending_validation FROM cards ORDER BY id')] == [int(not idempotent)] * 2
    if idempotent:
        assert effects == [('queue-lock', date.today().isoformat())]
        assert not prefix_database.database.in_transaction
    else:
        assert effects[1:] == [('integrity', 'a'), ('graph', 'a', date.today().isoformat()),
                               ('integrity', 'z'), ('graph', 'z', date.today().isoformat())]
        assert prefix_database.database.in_transaction
        prefix_database.database.rollback()
        assert [row[0] for row in prefix_database.database.execute('SELECT pending_validation FROM cards')] == [0, 0]


@pytest.mark.parametrize('current_revision', [1, 2])
def test_prefix_rejection_checks_revision_before_storing_only_valid_study_failure(prefix_database, monkeypatch, current_revision):
    monkeypatch.setattr(prefix_split_commands, 'preview_prefix_split',
                        lambda *_arguments: {'source_revision': current_revision})
    payload = {'card_id': 'parent', 'request': {'expected_revision': 1}}
    if current_revision != 1:
        with pytest.raises(HTTPException) as error:
            prefix_split_commands.reject_prefix_split(prefix_database, payload)
        assert error.value.status_code == 409
        assert len(prefix_database.statements) == 1
        assert not prefix_database.database.in_transaction
    else:
        assert prefix_split_commands.reject_prefix_split(prefix_database, payload) == {'rejected_after_review_id': 3}
        assert prefix_database.database.execute("SELECT prefix_split_rejected_after_review_id FROM cards WHERE id='parent'").fetchone()[0] == 3
        assert prefix_database.database.in_transaction
        prefix_database.database.rollback()
    assert prefix_database.database.execute("SELECT prefix_split_rejected_after_review_id FROM cards WHERE id='parent'").fetchone()[0] == 0


def test_missing_prefix_card_stops_before_queue_lock_or_split(prefix_database, monkeypatch):
    def unexpected(*_arguments):
        raise AssertionError('Missing-card guard reached a later effect')
    monkeypatch.setattr(prefix_split_commands, 'lock_queue_date_for_position', unexpected)
    monkeypatch.setattr(prefix_split_commands, 'apply_prefix_split', unexpected)
    with pytest.raises(HTTPException) as error:
        prefix_split_commands.accept_prefix_split(prefix_database, {
            'card_id': 'missing', 'request': {'expected_revision': 1},
        })
    assert error.value.status_code == 404
    assert len(prefix_database.statements) == 1
