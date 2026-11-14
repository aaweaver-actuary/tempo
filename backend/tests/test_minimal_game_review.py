"""Minimal Review follows current authored alternatives without Study writes."""

import chess
import pytest
from fastapi import HTTPException

from app.games.comparison import AuthoredLine, GameFacts
from app.games.comparison import first_departure as compare_facts
from app.games.review import Comparison, compare_pgn


def line(moves, fen=chess.STARTING_FEN):
    return AuthoredLine(fen, tuple(moves))


def first_departure(start_fen, moves, lines):
    return compare_facts(GameFacts(start_fen, tuple(moves)), lines)


def test_follows_any_authored_alternative_then_reports_first_departure():
    routes = [line(["e2e4", "e7e5", "g1f3"]), line(["d2d4", "d7d5"])]
    result = first_departure(chess.STARTING_FEN, ["e2e4", "c7c5", "g1f3"], routes)
    assert result["kind"] == "departure"
    assert result["ply"] == 2
    assert result["played_uci"] == "c7c5"
    assert result["expected_uci"] == ["e7e5"]
    assert result["actor"] == "black"
    board = chess.Board()
    board.push_uci("e2e4")
    assert result["fen"] == board.fen()


def test_shorter_alternative_does_not_hide_other_authored_continuations():
    result = first_departure(
        chess.STARTING_FEN, ["e2e4", "c7c5"], [line(["e2e4"]), line(["e2e4", "e7e5"])]
    )
    assert result["kind"] == "departure"
    assert result["ply"] == 2


def test_explicit_coverage_end_is_distinct_from_departure():
    result = first_departure(chess.STARTING_FEN, ["e2e4", "e7e5"], [line(["e2e4"])])
    assert result["kind"] == "coverage_end"
    assert result["ply"] == 2


def test_position_identity_ignores_move_counters():
    fen = " ".join(chess.STARTING_FEN.split()[:4] + ["17", "42"])
    assert first_departure(fen, ["e2e4"], [line(["e2e4"])]) == {"kind": "covered"}


def test_missing_applicable_route_is_truthfully_unavailable():
    assert first_departure(chess.STARTING_FEN, ["e2e4"], [])["kind"] == "unavailable"


def test_illegal_move_cannot_be_reported_as_departure():
    with pytest.raises(ValueError, match="Illegal"):
        first_departure(chess.STARTING_FEN, ["e2e5"], [line(["e2e4"])])


def test_multiple_pgn_games_are_rejected_before_source_read():
    request = Comparison(
        pgn='[Event "One"]\n\n1. e4 *\n\n[Event "Two"]\n\n1. d4 *',
        repertoire_id="r",
        color="white",
    )
    with pytest.raises(HTTPException, match="one PGN"):
        compare_pgn(request)
