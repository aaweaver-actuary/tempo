"""Legacy statistics persistence keeps date guards, grouping and claim controls."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from app.services import statistics

def test_statistics_composition_delegates_all_sql_to_database_owner():
    path = Path(__file__).resolve().parents[1] / 'app/services/statistics.py'
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
    assert not calls


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
