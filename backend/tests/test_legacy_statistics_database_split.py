"""Legacy statistics persistence keeps date guards, grouping and claim controls."""
import ast
from contextlib import contextmanager, nullcontext
from datetime import datetime as real_datetime
from pathlib import Path
import sqlite3

import pytest

from app.services import statistics


def test_statistics_composition_delegates_all_sql_to_database_owner():
    path = Path(__file__).resolve().parents[1] / 'app/services/statistics.py'
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
    assert not calls


def test_unknown_statistics_dimension_is_rejected_before_clock_or_database(monkeypatch):
    def unexpected(*_arguments, **_options):
        raise AssertionError('Unknown dimension reached clock or database')
    monkeypatch.setattr(statistics, 'datetime', type('Clock', (), {'now': unexpected}))
    monkeypatch.setattr(statistics, 'connection', unexpected)
    with pytest.raises(ValueError, match='Unknown statistics dimension'):
        statistics.statistics_breakdown('unknown', 30)


def test_breakdown_captures_projection_before_clock_and_connection(monkeypatch):
    events = []

    class Dimensions(dict):
        def get(self, key):
            events.append('lookup')
            return super().get(key)

    dimensions = Dimensions(color='g.color')
    monkeypatch.setattr(statistics.storage, 'DIMENSION_EXPRESSIONS', dimensions)
    monkeypatch.setattr(statistics, 'DIMENSION_EXPRESSIONS', dimensions)

    class Clock:
        @staticmethod
        def now(timezone):
            events.append('clock')
            dimensions['color'] = 'g.provider'
            return real_datetime(2026, 10, 21, tzinfo=timezone)

    class Database:
        def execute(self, query, parameters):
            events.append('query')
            assert 'SELECT g.color AS segment' in query
            assert parameters == ('2026-09-22',)
            return self

        def fetchall(self):
            return []

    @contextmanager
    def connection():
        events.append('connection')
        dimensions['color'] = 'g.speed'
        yield Database()

    monkeypatch.setattr(statistics, 'datetime', Clock)
    monkeypatch.setattr(statistics, 'connection', connection)
    assert statistics.statistics_breakdown('color', 30)['segments'] == []
    assert events == ['lookup', 'clock', 'connection', 'query']


@pytest.mark.parametrize('dimension', ['color', 'repertoire'])
def test_breakdown_keeps_classification_scope_and_application_score_math(monkeypatch, dimension):
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE imported_games(id TEXT,color TEXT,adaptive_excluded INTEGER);
            CREATE TABLE game_feature_rows(game_id TEXT,local_day TEXT,outcome_score REAL,
                mean_loss_cp REAL,tactical_found INTEGER,tactical_opportunities INTEGER);
            CREATE TABLE current_game_repertoire_matches(game_id TEXT,repertoire_id TEXT,is_primary INTEGER);
            CREATE TABLE current_repertoire_comparisons(game_id TEXT);
            CREATE TABLE repertoires(id TEXT,name TEXT);
            INSERT INTO imported_games VALUES('white','white',0),('black','black',0);
            INSERT INTO game_feature_rows VALUES('white','2026-09-28',1,20,2,3),('black','2026-09-28',0,80,0,4);
            INSERT INTO repertoires VALUES('rep','White repertoire');
            INSERT INTO current_game_repertoire_matches VALUES('white','rep',1);
            INSERT INTO current_repertoire_comparisons VALUES('white');
        ''')
        monkeypatch.setattr(statistics, 'connection', lambda: nullcontext(database))
        result = statistics.statistics_breakdown(dimension, 3650)
        by_segment = {row['segment']: row for row in result['segments']}
        if dimension == 'repertoire':
            assert list(by_segment) == ['White repertoire']
            assert by_segment['White repertoire']['score'] == 1
        else:
            assert set(by_segment) == {'white', 'black'}
            assert by_segment['white']['score'] == 1 and by_segment['black']['score'] == 0
        assert sum(row['games'] for row in by_segment.values()) == (1 if dimension == 'repertoire' else 2)


def test_daily_statistics_claim_retains_pause_promotion_and_study_mode_controls(monkeypatch):
    with sqlite3.connect(':memory:') as database:
        database.executescript('''
            CREATE TABLE settings(id INTEGER PRIMARY KEY,study_only_mode INTEGER);
            CREATE TABLE daily_statistics_jobs(local_day TEXT PRIMARY KEY,status TEXT,last_error TEXT,updated_at TEXT);
            CREATE TABLE background_activity(source TEXT,work_id TEXT,paused INTEGER,promoted INTEGER);
            INSERT INTO settings VALUES(1,0);
            INSERT INTO daily_statistics_jobs VALUES('2026-09-01','queued',NULL,'1'),
                ('2026-09-02','queued',NULL,'2'),('2026-09-03','queued',NULL,'3');
            INSERT INTO background_activity VALUES('statistics','2026-09-01',1,0),
                ('statistics','2026-09-03',0,1);
        ''')

        @contextmanager
        def connection(*, background=False):
            with database:
                yield database

        monkeypatch.setattr(statistics, 'connection', connection)
        assert statistics.claim_daily_snapshot() == '2026-09-03'
        assert statistics.claim_daily_snapshot() == '2026-09-02'
        assert statistics.claim_daily_snapshot() is None
        with database:
            database.execute('UPDATE settings SET study_only_mode=1')
        assert statistics.enqueue_daily_snapshot('2026-09-04') == '2026-09-04'
        assert statistics.claim_daily_snapshot() is None
        assert database.execute('SELECT local_day,status FROM daily_statistics_jobs ORDER BY local_day').fetchall() == [
            ('2026-09-01', 'queued'), ('2026-09-02', 'running'),
            ('2026-09-03', 'running'), ('2026-09-04', 'queued'),
        ]
