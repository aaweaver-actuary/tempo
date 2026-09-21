"""Canonical one-learner-decision opening graph construction."""

from __future__ import annotations

from dataclasses import dataclass

import chess

from .cards import card_id


@dataclass(frozen=True)
class DecisionSegment:
    card_id: str
    decision_index: int
    starting_fen: str
    moves: tuple[str, ...]
    parent_card_id: str | None
    trained_color: str


def decision_segments(
    starting_fen: str,
    moves_uci: list[str] | tuple[str, ...],
    trained_color: str,
    maximum_decisions: int,
) -> tuple[DecisionSegment, ...]:
    """Split a legal route into contextual cards testing one learner move each."""

    if trained_color not in {"white", "black"}:
        raise ValueError("trained_color must be white or black")
    if maximum_decisions < 0:
        raise ValueError("maximum_decisions must be nonnegative")

    board = chess.Board(starting_fen)
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    segment_starting_fen = board.fen()
    segment_moves: list[str] = []
    segments: list[DecisionSegment] = []
    parent_card_id: str | None = None

    for move_uci in moves_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal repertoire move {move_uci}")
        moving_color = board.turn
        segment_moves.append(move_uci)
        board.push(move)
        if moving_color != trained_chess_color:
            continue

        identifier = card_id(segment_starting_fen, segment_moves)
        segments.append(
            DecisionSegment(
                card_id=identifier,
                decision_index=len(segments),
                starting_fen=segment_starting_fen,
                moves=tuple(segment_moves),
                parent_card_id=parent_card_id,
                trained_color=trained_color,
            )
        )
        parent_card_id = identifier
        segment_starting_fen = board.fen()
        segment_moves = []
        if len(segments) >= maximum_decisions:
            break

    return tuple(segments)
