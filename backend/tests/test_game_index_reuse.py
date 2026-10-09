"""Scope-only refresh preserves verified game-position publications."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import chess
import pytest

from app.services import repertoire_game_refresh as refresh
from app.services import game_position_sources
from app import database
from app.services.durable_tasks import enqueue_task_in_transaction


def fingerprint(start_fen, moves_json):
    return hashlib.sha256(json.dumps([start_fen, moves_json], separators=(',', ':')).encode()).hexdigest()


@pytest.fixture
def scope_refresh(monkeypatch):
    state = {'connection_open': False, 'lease_current': True, 'version': 9,
             'source': {'id': 'game', 'start_fen': chess.STARTING_FEN, 'moves_json': '["e2e4"]'},
             'receipt': {'source_fingerprint': fingerprint(chess.STARTING_FEN, '["e2e4"]'),
                         'algorithm_version': 1, 'verified_from_start': 1,
                         'published_at': 'accepted', 'position_count': 2, 'published_position_version': 7},
             'queued': [], 'writes': []}
    class Database:
        def execute(self, statement, parameters=()):
            if 'FROM imported_games' in statement:
                return SimpleNamespace(fetchone=lambda: dict(state['source']) if state['source'] else None)
            if 'game_position_index_sources' in statement and statement.startswith('SELECT'):
                return SimpleNamespace(fetchone=lambda: state['receipt'])
            if 'SELECT derivation_version FROM game_derivation_jobs' in statement:
                return SimpleNamespace(fetchone=lambda: {'derivation_version': state['version']})
            state['writes'].append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: None, rowcount=1)
    @contextmanager
    def write(*, background):
        assert background and not state['connection_open']
        state['connection_open'] = True
        try:
            yield Database()
        finally:
            state['connection_open'] = False
    @contextmanager
    def read(**options):
        assert options == {'authoritative': True}
        assert not state['connection_open']
        state['connection_open'] = True
        try:
            yield Database()
        finally:
            state['connection_open'] = False
    monkeypatch.setattr(refresh.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(refresh, 'connection', write)
    monkeypatch.setattr(refresh, 'background_read_connection', read, raising=False)
    monkeypatch.setattr(refresh, 'lock_current_slice', lambda *_: state['lease_current'])
    monkeypatch.setattr(refresh, 'enqueue_compact_postgres_task_in_transaction',
                        lambda _database, kind, key, payload, **_: state['queued'].append((kind, key, payload)))
    return state, {'id': 'scope', 'generation': 2, 'lease_token': 'current', 'payload': {'after_game_id': ''}}


def test_scope_refresh_reuses_only_verified_unchanged_published_position_index(scope_refresh):
    state, task = scope_refresh
    assert refresh.execute_repertoire_game_refresh_slice(task)
    assert state['queued'][0] == ('game_derivation_compare', 'game',
                                 {'game_id': 'game', 'derivation_version': 9, 'phase': 'matches', 'cursor': 0})
    assert any('completed_phases=1' in statement for statement, _ in state['writes'])


@pytest.mark.parametrize('obsolete_field,obsolete_value', [
    ('source_fingerprint','changed'), ('algorithm_version',0), ('verified_from_start',0),
    ('published_at',None), ('position_count',0), ('position_count',None), ('published_position_version',0),
])
def test_scope_refresh_reindexes_when_publication_proof_is_obsolete(scope_refresh, obsolete_field, obsolete_value):
    state, task = scope_refresh
    state['receipt'][obsolete_field] = obsolete_value
    assert refresh.execute_repertoire_game_refresh_slice(task)
    assert state['queued'][0][0] == 'game_derivation_positions'
    assert not any('completed_phases=1' in statement for statement, _ in state['writes'])


def test_scope_refresh_missing_receipt_keeps_conservative_full_index(scope_refresh):
    state, task = scope_refresh
    state['receipt'] = None
    assert refresh.execute_repertoire_game_refresh_slice(task)
    assert state['queued'][0][0] == 'game_derivation_positions'


def test_scope_refresh_source_changes_during_preparation_cannot_reuse_old_index(scope_refresh, monkeypatch):
    state, task = scope_refresh
    def mutate_source(start_fen, moves_json):
        assert not state['connection_open'], 'Fingerprinting held a database connection'
        state['source']['moves_json'] = '["d2d4"]'
        return game_position_sources.source_fingerprint(start_fen, moves_json)
    monkeypatch.setattr(refresh, 'source_fingerprint', mutate_source)
    assert refresh.execute_repertoire_game_refresh_slice(task)
    assert state['queued'][0][0] == 'game_derivation_positions'


def test_stale_scope_refresh_cannot_advance_job_or_sweep_cursor(scope_refresh):
    state, task = scope_refresh
    state['lease_current'] = False
    assert not refresh.execute_repertoire_game_refresh_slice(task)
    assert state['queued'] == []
    assert not any(statement.startswith(('INSERT', 'UPDATE')) for statement, _ in state['writes'])


def test_deleted_scope_refresh_game_has_no_synthetic_replacement(scope_refresh):
    state, task = scope_refresh
    state['source'] = None
    assert not refresh.execute_repertoire_game_refresh_slice(task)
    assert state['queued'] == []


def test_sqlite_scope_refresh_retains_legacy_positions_and_requires_full_index(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'legacy-source.sqlite')
    monkeypatch.setattr(refresh.postgres_store, 'configured', lambda: False)
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        connection.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('game','lichess','proof',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]')", (now, chess.STARTING_FEN))
        connection.execute("INSERT INTO game_derivation_jobs(game_id,status,completed_phases,updated_at) VALUES('game','complete',6,?)", (now,))
        connection.execute("INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci) VALUES('game',0,?,'e2e4')", (' '.join(chess.STARTING_FEN.split()[:4]),))
        enqueue_task_in_transaction(connection, 'repertoire_game_refresh', 'all', {'after_game_id':''})
        connection.execute("UPDATE background_tasks SET state='leased',lease_token='legacy-proof' WHERE kind='repertoire_game_refresh'")
        task = dict(connection.execute("SELECT * FROM background_tasks WHERE kind='repertoire_game_refresh'").fetchone())
        task['payload'] = json.loads(task['payload_json'])
    assert refresh.execute_repertoire_game_refresh_slice(task)
    with database.read_connection() as connection:
        assert tuple(connection.execute("SELECT derivation_version,completed_phases FROM game_derivation_jobs WHERE game_id='game'").fetchone()) == (2,0)
        assert connection.execute('SELECT COUNT(*) FROM game_position_occurrences').fetchone()[0] == 1
