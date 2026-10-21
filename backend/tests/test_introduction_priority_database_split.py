"""Boundaries for the mechanical legacy introduction-priority storage split."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from app.services import introduction_priorities as priorities


def test_introduction_priority_composition_delegates_all_database_operations():
    path = Path(__file__).resolve().parents[1] / 'app/services/introduction_priorities.py'
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {'execute', 'executemany', 'execute_native', 'cursor'}]
    assert not calls


def test_legacy_replacement_keeps_delete_clock_and_record_order(monkeypatch):
    events = []
    database = object()
    records = [priorities.PriorityRecord('rep', card, '[]', 0.2, '[]', 0.4, 0.6, '{}')
               for card in ('second', 'first')]

    def delete_rows(connection, repertoire_id):
        assert connection is database and repertoire_id == 'rep'
        events.append('delete')

    def now():
        events.append('clock')
        return 'replacement-time'

    def insert_rows(connection, rows):
        assert connection is database
        events.append('insert')
        assert rows == [('rep', card, priorities.SCORING_VERSION, '[]', 0.2, '[]',
                         0.4, 0.6, '{}', 'replacement-time')
                        for card in ('second', 'first')]

    monkeypatch.setattr(priorities.publications, 'delete_legacy_rows', delete_rows)
    monkeypatch.setattr(priorities.publications, 'insert_legacy_rows', insert_rows)
    monkeypatch.setattr(priorities, '_now', now)
    priorities._replace_priority_records(database, 'rep', records)
    assert events == ['delete', 'clock', 'insert']


def test_personal_pages_keep_repertoire_color_speed_exclusion_and_cursor_filters(monkeypatch):
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE imported_games(id TEXT,color TEXT,adaptive_excluded INTEGER,speed TEXT,played_at TEXT);
            CREATE TABLE game_position_occurrences(fen_key TEXT,game_id TEXT,ply INTEGER,move_uci TEXT);
            CREATE TABLE current_game_repertoire_matches(game_id TEXT,repertoire_id TEXT);
            INSERT INTO imported_games VALUES
                ('a','white',0,'blitz','2026-10-01'),('b','white',0,'rapid','2026-10-02'),
                ('c','white',0,'classical','2026-10-03'),('d','white',0,'rapid','2026-10-04'),
                ('excluded','white',1,'rapid','2026-10-05'),('other','white',0,'rapid','2026-10-06'),
                ('black','black',0,'rapid','2026-10-07'),('bullet','white',0,'bullet','2026-10-08');
            INSERT INTO game_position_occurrences VALUES
                ('position','a',1,'e2e4'),('position','a',3,'d2d4'),('position','b',1,'e2e4'),
                ('position','c',1,'e2e4'),('position','d',1,NULL),('position','excluded',1,'e2e4'),
                ('position','other',1,'e2e4'),('position','black',1,'e2e4'),('position','bullet',1,'e2e4');
            INSERT INTO current_game_repertoire_matches VALUES
                ('a','rep'),('b','rep'),('c','rep'),('d','rep'),('excluded','rep'),
                ('other','other-rep'),('black','rep'),('bullet','rep');
        ''')
        sections = []

        @contextmanager
        def read_section():
            sections.append('open')
            try:
                yield database
            finally:
                sections.append('close')

        monkeypatch.setattr(priorities, 'PERSONAL_EVIDENCE_ROWS_PER_READ', 2)
        rows = priorities._read_personal_evidence_rows(
            read_section, ['position'], 'white', repertoire_id='rep',
        )
        assert [(row['game_id'], row['ply']) for row in rows] == [
            ('a', 1), ('a', 3), ('b', 1), ('c', 1),
        ]
        assert sections == ['open', 'close'] * 3


def test_priority_claim_preserves_pause_promotion_study_mode_and_affected_row_authority(monkeypatch):
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE settings(id INTEGER PRIMARY KEY,study_only_mode INTEGER);
            CREATE TABLE background_activity(source TEXT,work_id TEXT,paused INTEGER,promoted INTEGER);
            CREATE TABLE repertoire_priority_jobs(repertoire_id TEXT PRIMARY KEY,generation INTEGER,
                status TEXT,attempts INTEGER,next_attempt_at TEXT,updated_at TEXT);
            CREATE TABLE analysis_refresh_requests(kind TEXT,repertoire_id TEXT,pending_since TEXT);
            INSERT INTO settings VALUES(1,0);
            INSERT INTO repertoire_priority_jobs VALUES
                ('paused',1,'queued',0,'0','0'),('ordinary',2,'queued',0,'1','1'),
                ('promoted',3,'queued',0,'2','2'),('future',4,'queued',0,'z','3');
            INSERT INTO background_activity VALUES('priority','paused',1,0),('priority','promoted',0,1);
            INSERT INTO analysis_refresh_requests VALUES
                ('repertoire_priority','paused','pending'),('repertoire_priority','ordinary','pending'),
                ('repertoire_priority','promoted','pending');
        ''')

        @contextmanager
        def connection(*, background=False):
            assert background
            with database:
                yield database

        from tempo_legacy_database import connections
        monkeypatch.setattr(connections, 'connection', connection)
        monkeypatch.setattr(priorities, '_now', lambda: 'now')
        monkeypatch.setattr(priorities.activity_gate, 'wait_for_foreground', lambda: None)
        claimed = priorities.claim_priority_refresh()
        assert (claimed['repertoire_id'], claimed['generation']) == ('promoted', 3)
        assert priorities.claim_priority_refresh()['repertoire_id'] == 'ordinary'
        assert priorities.claim_priority_refresh() is None
        assert [tuple(row) for row in database.execute(
            'SELECT repertoire_id,attempts,status FROM repertoire_priority_jobs ORDER BY repertoire_id'
        )] == [('future', 0, 'queued'), ('ordinary', 1, 'running'),
               ('paused', 0, 'queued'), ('promoted', 1, 'running')]
        assert [tuple(row) for row in database.execute(
            'SELECT repertoire_id,pending_since FROM analysis_refresh_requests ORDER BY repertoire_id'
        )] == [('ordinary', None), ('paused', 'pending'), ('promoted', None)]
        with database:
            database.execute("UPDATE settings SET study_only_mode=1")
            database.execute("UPDATE repertoire_priority_jobs SET next_attempt_at='0' WHERE repertoire_id='future'")
        assert priorities.claim_priority_refresh() is None
