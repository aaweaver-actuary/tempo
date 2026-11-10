"""Regression for repeated setup moves in the retained puzzle validator."""

import chess
import pytest

from app.services.puzzles import validate_puzzle_record as validate


@pytest.mark.parametrize("as_string", [True, False], ids=["string", "list"])
def test_repeated_setup_move_keeps_first_position_and_complete_solution(as_string):
    moves = "g1f3 g8f6 f3g1 f6g8 b1c3 a7a6 g1f3".split()
    fen, solution = validate({"FEN": chess.STARTING_FEN, "Moves": " ".join(moves) if as_string else moves})
    assert fen == "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1"
    assert solution == moves[1:]
    board = chess.Board(fen)
    for value in solution:
        move = chess.Move.from_uci(value)
        assert move in board.legal_moves
        board.push(move)


@pytest.mark.parametrize("moves", ["", "g1f3", [], ["g1f3"]])
def test_incomplete_puzzle_is_rejected(moves):
    with pytest.raises(ValueError):
        validate({"FEN": chess.STARTING_FEN, "Moves": moves})


@pytest.mark.parametrize("moves", ["g1f3 g8f6 f3g1 f6g8 b1c3 a7a6 g1f3 e7e4", ["g1f3", "g8f6", "f3g1", "f6g8", "b1c3", "a7a6", "g1f3", "e7e4"]])
def test_illegal_later_move_is_rejected(moves):
    with pytest.raises(ValueError, match="Illegal puzzle move"):
        validate({"FEN": chess.STARTING_FEN, "Moves": moves})
