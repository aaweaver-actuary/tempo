"""Synthetic Black import proof; no installed storage or services."""
import json

import chess
import pytest
from fastapi import HTTPException

from app.chess.pgn import parse_pgn
from app.pgn_import_validation import validate_sources


BLACK_PGN = '[Event "Synthetic Black branches"]\n\n1. e4 c5 2. Nf3 (2. Nc3 e6) e6 3. d4 cxd4 4. Nxd4 Nc6 *'


def sources(lines):
    return [dict(id=str(index), start_fen=line.starting_fen,
                 moves_json=json.dumps(line.moves), trained_color='black')
            for index, line in enumerate(lines)]


def test_branching_black_pgn_preserves_both_opponent_routes_and_retry_snapshot():
    games, lines = parse_pgn(BLACK_PGN)
    assert games == 1 and len(lines) == 2
    assert lines[0].moves[-2:] == ['f3d4', 'b8c6']
    assert lines[1].moves == ['e2e4', 'c7c5', 'b1c3', 'e7e6']
    original = sources(lines)
    validate_sources('taimanov-fixture', original)
    validate_sources('taimanov-fixture', original + sources(lines))
    assert original == sources(lines)


@pytest.mark.parametrize('pgn', [
    '1. e4 e5 2. Bh6 *',
    '[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]\n\n*',
])
def test_invalid_pgn_is_rejected_without_importing_legal_prefix(pgn):
    with pytest.raises(ValueError):
        parse_pgn(pgn)


def test_competing_black_response_is_a_visible_conflict():
    _, lines = parse_pgn('1. e4 c5 (1... e5) *')
    with pytest.raises(HTTPException) as error:
        validate_sources('rep', sources(lines))
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'repertoire_conflict'
    assert {move['uci'] for move in error.value.detail['conflicts'][0]['moves']} == {'c7c5', 'e7e5'}


def test_existing_invalid_tail_is_blocked_before_conflict_scanner_can_truncate():
    rows = [dict(id='old-source', start_fen=chess.STARTING_FEN,
                 moves_json='["e2e4","e7e5","f1h6"]', trained_color='black')]
    with pytest.raises(HTTPException) as error:
        validate_sources('rep', rows)
    assert error.value.status_code == 422
    assert error.value.detail['source_id'] == 'old-source'


def test_route_bound_is_finite_and_no_truncation():
    # Repeated reversible moves keep the fixture legal beyond the route bound.
    pgn = ' '.join(f'{number}. Nf3 Nf6 {number+1}. Ng1 Ng8' for number in range(1, 503, 2)) + ' *'
    with pytest.raises(ValueError, match='bounded route'):
        parse_pgn(pgn)
