"""Derived-publication storage boundaries and daily-readiness projections."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest

from app.services import postgres_daily_statistics


def test_two_derived_publication_compositions_delegate_all_sql_to_database_owner():
    services = Path(__file__).resolve().parents[1] / 'app/services'
    for name in ('postgres_next_opponent', 'postgres_daily_statistics'):
        calls = [node for node in ast.walk(ast.parse((services / f'{name}.py').read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{name} still executes SQL directly'


@pytest.mark.parametrize('status', ['waiting-sync', 'waiting-analysis', 'complete'])
def test_daily_readiness_projection_keeps_watermark_priority_and_read_only_metrics(monkeypatch, status):
    local_day = '2026-09-28'
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE game_accounts(provider TEXT,username TEXT);
            CREATE TABLE game_sync_state(provider TEXT,last_success_at TEXT);
            CREATE TABLE imported_games(id TEXT,played_at TEXT,analysis_state TEXT,adaptive_excluded INTEGER);
            CREATE TABLE game_feature_rows(game_id TEXT,local_day TEXT,outcome_score REAL,
                tactical_opportunities INTEGER,tactical_found INTEGER);
            INSERT INTO game_accounts VALUES('lichess','Alice'),('unconfigured','');
            INSERT INTO imported_games VALUES('game','2026-09-28T12:00:00Z','ready',0),
                ('excluded','2026-09-28T12:00:00Z','ready',1),
                ('failed','2026-09-28T12:00:00Z','failed',0);
        ''')
        watermark = None if status == 'waiting-sync' else '2026-09-29T00:00:00Z'
        database.execute('INSERT INTO game_sync_state VALUES(?,?)', ('lichess', watermark))
        if status == 'complete':
            database.execute('INSERT INTO game_feature_rows VALUES(?,?,?,?,?)', ('game', local_day, 0.5, 12, 4))
        else:
            monkeypatch.setattr(postgres_daily_statistics.storage, 'daily_feature_rows',
                                lambda *_: pytest.fail('Incomplete day loaded aggregate features'))
        database.commit()
        changes_before_read = database.total_changes
        sections = []

        @contextmanager
        def reader():
            sections.append('opened')
            try:
                yield database
            finally:
                sections.append('closed')

        monkeypatch.setattr(postgres_daily_statistics, 'background_read_connection', reader)
        result = postgres_daily_statistics._prepare_daily_statistics(local_day)
        assert sections == ['opened', 'closed']
        assert database.total_changes == changes_before_read and not database.in_transaction
        if status == 'complete':
            assert result == {'local_day': local_day, 'status': status, 'games': 1, 'score': 0.5,
                              'tactical_found': 4, 'tactical_opportunities': 12}
        else:
            assert result == {'local_day': local_day, 'status': status,
                              'missing_provider_watermarks': ['lichess'] if status == 'waiting-sync' else [],
                              'blocked_game_ids': ['game']}
