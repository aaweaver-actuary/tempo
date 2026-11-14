"""Persisted canonical scope still constrains current repertoire lines."""

import chess

from app.services.canonical_prefix import (
    game_in_scope,
    prefix_projection,
    validate_scoped_line,
)

ITALIAN = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]


def test_canonical_prefix_requires_exact_complete_game_history():
    assert game_in_scope(chess.STARTING_FEN, [*ITALIAN, "g8f6"], ITALIAN)
    assert not game_in_scope(chess.STARTING_FEN, ITALIAN[:-1], ITALIAN)
    assert not game_in_scope(
        chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3", "d7d6"], ITALIAN
    )
    assert not game_in_scope(
        chess.STARTING_FEN, ["g1f3", "b8c6", "e2e4", "e7e5", "f1c4"], ITALIAN
    )
    assert not game_in_scope(
        prefix_projection(ITALIAN)["ending_fen"], ["g8f6"], ITALIAN
    )
    assert game_in_scope(chess.STARTING_FEN, ["d2d4"], [])


def test_canonical_prefix_accepts_matching_stubs_and_verified_continuations():
    assert (
        validate_scoped_line(chess.STARTING_FEN, ITALIAN[:3], ITALIAN, [])["status"]
        == "valid"
    )
    ending_fen = prefix_projection(ITALIAN)["ending_fen"]
    continuation = validate_scoped_line(ending_fen, ["f8c5", "c2c3"], ITALIAN, ITALIAN)
    assert continuation["status"] == "valid"
    assert continuation["scope_start_ply"] == 0
    assert (
        validate_scoped_line(ending_fen, ["f8c5"], ITALIAN, None)["status"] == "pending"
    )
