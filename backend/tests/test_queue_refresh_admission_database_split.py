"""Focused coverage for the first queue-refresh database extraction slice."""
import ast
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from app.services import postgres_queue_refresh as refresh


def test_queue_admission_writers_delegate_database_operations_without_moving_policy():
    path = Path(__file__).resolve().parents[1] / 'app/services/postgres_queue_refresh.py'
    tree = ast.parse(path.read_text())
    affected = {'_publish_tactical_introduction', '_reconcile_one_unseen_entry',
                '_admit_one_due_card', '_admit_one_study_card'}
    found = set()
    for function in tree.body:
        if isinstance(function, ast.FunctionDef) and function.name in affected:
            found.add(function.name)
            assert not [node for node in ast.walk(function)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
    assert found == affected


class NativeSqlite:
    def __init__(self, database):
        self.database = database
        self.locks = []

    def execute(self, query, parameters=()):
        return self.database.execute(query, parameters)

    def execute_native(self, query, parameters=()):
        if 'pg_advisory_xact_lock' in query:
            self.locks.append(parameters)
            return SimpleNamespace(fetchone=lambda: None)
        return self.database.execute(query.replace('%s', '?').replace('FOR UPDATE', ''), parameters)


def test_tactical_publication_keeps_reservation_replay_rotation_and_daily_quota():
    with sqlite3.connect(':memory:') as database:
        database.executescript('''
            CREATE TABLE settings(id INTEGER PRIMARY KEY,tactics_new_per_day INTEGER);
            CREATE TABLE deleted_cards(card_id TEXT PRIMARY KEY);
            CREATE TABLE tactic_rotation(id INTEGER PRIMARY KEY,last_pack_id TEXT);
            CREATE TABLE tactic_pack_activation(pack_id TEXT PRIMARY KEY,active INTEGER);
            CREATE TABLE tactic_progress(puzzle_id TEXT PRIMARY KEY,deck_id TEXT,card_id TEXT,
                admitted_at TEXT,admission_mode TEXT);
            CREATE TABLE tactic_introductions(puzzle_id TEXT,pack_id TEXT,introduction_date TEXT);
            CREATE TABLE repertoires(id TEXT PRIMARY KEY,name TEXT,source_name TEXT,created_at TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,repertoire_id TEXT,kind TEXT,start_fen TEXT,
                moves_json TEXT,state TEXT,due_date TEXT,content_type TEXT,scheduling_mode TEXT,
                source_ref TEXT,source_fen TEXT,introduced_at TEXT,archived INTEGER DEFAULT 0,superseded_by TEXT);
            CREATE TABLE daily_queue(queue_date TEXT,card_id TEXT,position INTEGER);
            INSERT INTO settings VALUES(1,2);
            INSERT INTO tactic_rotation VALUES(1,'');
            INSERT INTO tactic_pack_activation VALUES('pack',1);
        ''')
        borrowed = NativeSqlite(database)

        def candidate(number, rotation):
            return {'card_id': f'card-{number}', 'puzzle_id': f'puzzle-{number}',
                    'training_fen': 'training', 'source_fen': 'source',
                    'solution_json': '["e2e4"]', 'pack_id': 'pack', 'rotation_cursor': rotation}

        assert refresh._publish_tactical_introduction(borrowed, '2026-10-21', None) is False
        assert refresh._publish_tactical_introduction(borrowed, '2026-10-21', candidate(1, '')) is True
        # An unchanged prepared occurrence observes its changed rotation and exits inertly.
        assert refresh._publish_tactical_introduction(borrowed, '2026-10-21', candidate(1, '')) is True
        assert refresh._publish_tactical_introduction(borrowed, '2026-10-21', candidate(2, 'pack')) is True
        assert refresh._publish_tactical_introduction(borrowed, '2026-10-21', candidate(3, 'pack')) is False
        assert database.execute('SELECT card_id,position FROM daily_queue ORDER BY position').fetchall() == [('card-1', 0), ('card-2', 1)]
        assert database.execute('SELECT COUNT(*) FROM tactic_progress').fetchone()[0] == 2
        assert database.execute('SELECT COUNT(*) FROM tactic_introductions').fetchone()[0] == 2
        assert database.execute('SELECT last_pack_id FROM tactic_rotation WHERE id=1').fetchone()[0] == 'pack'
        assert borrowed.locks[0] == ('tempo:card-edit:card-1',)
        assert borrowed.locks[1] == ('tempo:tactic-daily-admission:2026-10-21',)
