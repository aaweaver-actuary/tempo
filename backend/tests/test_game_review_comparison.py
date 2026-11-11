"""Typed comparison facts and adoption by the retained Review handlers."""
import json
import subprocess
import sys
from contextlib import contextmanager

import chess
import pytest
from fastapi import HTTPException

from app.games import review
from app.games.comparison import AuthoredLine, GameFacts, first_departure


def test_typed_alternatives_are_sorted_and_match_start_identity():
    start = ' '.join(chess.STARTING_FEN.split()[:4] + ['17', '42'])
    result = first_departure(GameFacts(start, ('c2c4',)), [
        AuthoredLine(chess.STARTING_FEN, ('e2e4',)),
        AuthoredLine(chess.STARTING_FEN, ('d2d4',)),
    ])
    assert result == {'kind': 'departure', 'ply': 1, 'fen': chess.Board(start).fen(),
                      'played_uci': 'c2c4', 'expected_uci': ['d2d4', 'e2e4'], 'actor': 'white'}


def test_no_matching_start_is_unavailable_and_empty_route_ends_coverage():
    game = GameFacts(chess.STARTING_FEN, ('e2e4',))
    assert first_departure(game, [AuthoredLine(chess.Board.empty().fen(), ())])['kind'] == 'unavailable'
    assert first_departure(game, [AuthoredLine(chess.STARTING_FEN, ())]) == {
        'kind': 'coverage_end', 'ply': 1, 'fen': chess.Board().fen()}


def test_pure_module_fresh_import_excludes_application_infrastructure():
    code = '''import sys
import app.games.comparison
for prefix in ("fastapi", "tempo_storage", "app.main", "app.product_composition", "app.games.review"):
    assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules), prefix
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_post_and_saved_get_invoke_comparator_with_current_authored_facts(monkeypatch):
    calls = []
    reads = []
    @contextmanager
    def connection():
        yield 'read-only-db'
    def authored(db, repertoire_id, color):
        reads.append((db, repertoire_id, color))
        return [{'start_fen': chess.STARTING_FEN, 'moves_json': json.dumps(['e2e4', 'e7e5'])}]
    def saved(db, game_id):
        assert (db, game_id) == ('read-only-db', 'game-1')
        return {'start_fen': chess.STARTING_FEN, 'moves_json': json.dumps(['e2e4', 'c7c5']), 'color': 'black'}
    real = review.first_departure
    def observe(game, lines):
        calls.append((game, lines))
        return real(game, lines)
    monkeypatch.setattr(review, 'read_connection', connection)
    monkeypatch.setattr(review.storage, 'authored_lines', authored)
    monkeypatch.setattr(review.storage, 'game', saved)
    monkeypatch.setattr(review, 'first_departure', observe)
    post = review.compare_pgn(review.Comparison(pgn='1. e4 c5 *', repertoire_id='r', color='white'))
    get = review.game_detail('game-1', 'r')
    assert post['comparison'] == get['comparison']
    assert post['comparison']['kind'] == 'departure'
    assert post['comparison']['ply'] == 2
    assert post['positions'] == get['positions']
    assert reads == [('read-only-db', 'r', 'white'), ('read-only-db', 'r', 'black')]
    assert calls == [(GameFacts(chess.STARTING_FEN, ('e2e4', 'c7c5')),
                      [AuthoredLine(chess.STARTING_FEN, ('e2e4', 'e7e5'))])] * 2


def test_saved_game_full_replay_rejects_illegal_move_after_departure(monkeypatch):
    @contextmanager
    def connection():
        yield object()
    monkeypatch.setattr(review, 'read_connection', connection)
    monkeypatch.setattr(review.storage, 'game', lambda db, game_id: {
        'start_fen': chess.STARTING_FEN, 'moves_json': json.dumps(['d2d4', 'e7e5', 'e2e5']), 'color': 'white'})
    monkeypatch.setattr(review.storage, 'authored_lines', lambda *args: pytest.fail('read before full replay'))
    with pytest.raises(HTTPException) as error:
        review.game_detail('game-1', 'r')
    assert error.value.status_code == 422


def test_pgn_full_replay_rejects_illegal_move_after_departure(monkeypatch):
    monkeypatch.setattr(review, 'read_connection', lambda: pytest.fail('read before full replay'))
    with pytest.raises(HTTPException) as error:
        review.compare_pgn(review.Comparison(pgn='1. d4 e5 2. e5 *', repertoire_id='r', color='white'))
    assert error.value.status_code == 422
