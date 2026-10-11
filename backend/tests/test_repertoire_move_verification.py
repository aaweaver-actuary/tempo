"""Study-only moves require current evidence, never an empty paused index."""
from contextlib import contextmanager
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import database, main
from app.services import repertoire_move_verification as verification
from test_queue_attempt_issuance import queued_attempts, START_FEN


@pytest.fixture
def saved_sources(queued_attempts):
    with database.connection() as connection:
        connection.execute('UPDATE settings SET study_only_mode=1 WHERE id=1')
        for identifier, moves in [('a', ['e2e4']), ('b', ['d2d4', 'd7d5'])]:
            connection.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) '
                               "VALUES(?,'issuance',?,'white',?,?, '2026-10-11')",
                               (identifier, identifier, START_FEN, json.dumps(moves)))


def test_study_only_mode_verifies_non_expected_move_from_saved_sources_without_bulk_preparation(saved_sources):
    client = TestClient(main.app)
    response = client.get('/api/repertoires/issuance/move-membership', params={'fen': START_FEN, 'move_uci': 'd2d4'})
    assert response.status_code == 200, response.text
    assert response.json()['verified'] is True
    assert verification.verify_repertoire_move('issuance', START_FEN, 'c2c4')['verified'] is False


def test_saved_move_verification_closes_reads_before_replay_and_restarts_pages(saved_sources, monkeypatch):
    monkeypatch.setattr(verification, 'SOURCE_PAGE_SIZE', 1)
    original_read = verification.read_connection
    original_replay = verification._line_contains_move
    opened_connections = 0

    @contextmanager
    def tracked_read():
        nonlocal opened_connections
        with original_read() as connection:
            opened_connections += 1
            try:
                yield connection
            finally:
                opened_connections -= 1

    def replay(*args):
        assert opened_connections == 0
        # A foreground write/reconnect remains available during computation.
        with database.connection() as connection:
            connection.execute('UPDATE settings SET initial_depth=initial_depth WHERE id=1')
        return original_replay(*args)

    monkeypatch.setattr(verification, 'read_connection', tracked_read)
    monkeypatch.setattr(verification, '_line_contains_move', replay)
    first = verification.verify_repertoire_move('issuance', START_FEN, 'd2d4')
    assert first['verified'] is None and first['next_cursor'] == 'a'
    database.initialize()
    final = verification.verify_repertoire_move('issuance', START_FEN, 'd2d4', first['next_cursor'], first['source_token'])
    assert final['verified'] is True
    assert verification.verify_repertoire_move('issuance', START_FEN, 'd2d4', first['next_cursor'], first['source_token']) == final


@pytest.mark.parametrize('failure', ['missing', 'malformed', 'changed', 'changed_during_replay', 'budget'])
def test_saved_move_verification_never_grades_missing_invalid_or_stale_evidence(saved_sources, monkeypatch, failure):
    token = verification.verify_repertoire_move('issuance', START_FEN, 'c2c4')['source_token']
    if failure in {'missing', 'malformed', 'changed'}:
        with database.connection() as connection:
            if failure == 'missing':
                connection.execute('DELETE FROM repertoire_lines')
            else:
                connection.execute('UPDATE repertoire_lines SET moves_json=? WHERE id=\'a\'',
                                   ('{}' if failure == 'malformed' else '["g1f3"]',))
    if failure == 'changed_during_replay':
        original_replay = verification._line_contains_move
        def changed_replay(*args):
            result = original_replay(*args)
            with database.connection() as connection:
                connection.execute('UPDATE repertoire_lines SET moves_json=\'["g1f3"]\' WHERE id=\'a\'')
            return result
        monkeypatch.setattr(verification, '_line_contains_move', changed_replay)
    if failure == 'budget':
        monkeypatch.setattr(verification, 'SOURCE_REPLAY_SECONDS', 0)
    params = {'fen': START_FEN, 'move_uci': 'c2c4'}
    if failure == 'changed':
        params['source_token'] = token
    if failure == 'changed_during_replay':
        with pytest.raises(HTTPException) as error:
            verification.verify_repertoire_move('issuance', START_FEN, 'c2c4')
        assert error.value.status_code == 409
        return
    response = TestClient(main.app).get('/api/repertoires/issuance/move-membership', params=params)
    assert response.status_code == (409 if failure.startswith('changed') else 503)
