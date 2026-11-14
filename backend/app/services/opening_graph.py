"""Hybrid opening graph construction with cumulative roots and decision descendants."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import chess
from tempo_chess_core import card_id

from ..contracts.opening_graph import GraphStep


@dataclass(frozen=True)
class DecisionSegment:
    card_id: str
    decision_index: int
    segment_kind: str
    first_decision_index: int
    last_decision_index: int
    decision_fen_keys: tuple[str, ...]
    starting_fen: str
    moves: tuple[str, ...]
    parent_card_id: str | None
    trained_color: str


@dataclass(frozen=True)
class GraphInput:
    repertoire_id: str
    lines: tuple[dict, ...]
    default_depth: int
    prefix_overrides: tuple[dict, ...] = ()


def build_graph(graph_input: GraphInput) -> tuple[GraphStep, ...]:
    steps: list[GraphStep] = []
    override_by_source = {
        override["source_card_id"]: override
        for override in graph_input.prefix_overrides
    }
    for line in graph_input.lines:
        maximum_decisions = int(
            line.get("learner_decision_count") or graph_input.default_depth
        )
        segments = decision_segments(
            line["start_fen"],
            tuple(json.loads(line["moves_json"])),
            line["trained_color"],
            maximum_decisions,
        )
        expanded_segments: list[DecisionSegment] = []
        parent_card_id: str | None = None
        for segment in segments:
            for expanded_segment in _expanded_segment(segment, override_by_source):
                expanded_segment = replace(
                    expanded_segment,
                    decision_index=len(expanded_segments),
                    parent_card_id=parent_card_id,
                )
                expanded_segments.append(expanded_segment)
                parent_card_id = expanded_segment.card_id
        for segment in expanded_segments:
            steps.append(
                GraphStep(
                    repertoire_id=graph_input.repertoire_id,
                    line_id=line["id"],
                    decision_index=segment.decision_index,
                    segment_kind=segment.segment_kind,
                    first_decision_index=segment.first_decision_index,
                    last_decision_index=segment.last_decision_index,
                    decision_fen_keys=segment.decision_fen_keys,
                    card_id=segment.card_id,
                    parent_card_id=segment.parent_card_id,
                    decision_fen_key=segment.decision_fen_keys[-1],
                    starting_fen=segment.starting_fen,
                    moves=segment.moves,
                    trained_color=segment.trained_color,
                )
            )
    return tuple(steps)


def _decision_fen_keys(
    starting_fen: str, moves: tuple[str, ...], trained_color: str
) -> tuple[str, ...]:
    board = chess.Board(starting_fen)
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    decision_fen_keys: list[str] = []
    for move_uci in moves:
        if board.turn == trained_chess_color:
            decision_fen_keys.append(" ".join(board.fen().split()[:4]))
        board.push_uci(move_uci)
    return tuple(decision_fen_keys)


def _expanded_segment(
    segment: DecisionSegment, override_by_source: dict[str, dict]
) -> tuple[DecisionSegment, ...]:
    override = override_by_source.get(segment.card_id)
    if segment.segment_kind != "prefix" or override is None:
        return (segment,)

    shortened_moves = tuple(json.loads(override["shortened_moves_json"]))
    continuation_moves = tuple(json.loads(override["continuation_moves_json"]))
    shortened_fen_keys = _decision_fen_keys(
        override["shortened_start_fen"], shortened_moves, segment.trained_color
    )
    continuation_fen_keys = _decision_fen_keys(
        override["continuation_start_fen"], continuation_moves, segment.trained_color
    )
    if not shortened_fen_keys or len(continuation_fen_keys) != 1:
        raise ValueError("Saved prefix split is not a valid hybrid graph override")
    shortened_segment = DecisionSegment(
        card_id=override["shortened_card_id"],
        decision_index=segment.decision_index,
        segment_kind="prefix",
        first_decision_index=segment.first_decision_index,
        last_decision_index=(
            segment.first_decision_index + len(shortened_fen_keys) - 1
        ),
        decision_fen_keys=shortened_fen_keys,
        starting_fen=override["shortened_start_fen"],
        moves=shortened_moves,
        parent_card_id=segment.parent_card_id,
        trained_color=segment.trained_color,
    )
    expanded_shortened = _expanded_segment(shortened_segment, override_by_source)
    continuation_decision_index = segment.first_decision_index + len(shortened_fen_keys)
    continuation_segment = DecisionSegment(
        card_id=override["continuation_card_id"],
        decision_index=segment.decision_index + len(expanded_shortened),
        segment_kind="decision",
        first_decision_index=continuation_decision_index,
        last_decision_index=continuation_decision_index,
        decision_fen_keys=continuation_fen_keys,
        starting_fen=override["continuation_start_fen"],
        moves=continuation_moves,
        parent_card_id=expanded_shortened[-1].card_id,
        trained_color=segment.trained_color,
    )
    return (*expanded_shortened, continuation_segment)


def decision_segments(
    starting_fen: str,
    moves_uci: list[str] | tuple[str, ...],
    trained_color: str,
    maximum_decisions: int,
) -> tuple[DecisionSegment, ...]:
    """Build one cumulative route prefix followed by one-decision descendants."""

    if trained_color not in {"white", "black"}:
        raise ValueError("trained_color must be white or black")
    if maximum_decisions < 0:
        raise ValueError("maximum_decisions must be nonnegative")

    board = chess.Board(starting_fen)
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    legal_moves: list[str] = []
    learner_move_offsets: list[int] = []
    learner_decision_fen_keys: list[str] = []

    for move_uci in moves_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal repertoire move {move_uci}")
        if board.turn == trained_chess_color:
            learner_move_offsets.append(len(legal_moves))
            learner_decision_fen_keys.append(" ".join(board.fen().split()[:4]))
        legal_moves.append(move_uci)
        board.push(move)

    if not learner_move_offsets or maximum_decisions == 0:
        return ()

    prefix_decision_count = min(maximum_decisions, len(learner_move_offsets))
    prefix_end_offset = learner_move_offsets[prefix_decision_count - 1] + 1
    prefix_moves = tuple(legal_moves[:prefix_end_offset])
    prefix_identifier = card_id(starting_fen, prefix_moves)
    segments: list[DecisionSegment] = []
    segments.append(
        DecisionSegment(
            card_id=prefix_identifier,
            decision_index=0,
            segment_kind="prefix",
            first_decision_index=0,
            last_decision_index=prefix_decision_count - 1,
            decision_fen_keys=tuple(learner_decision_fen_keys[:prefix_decision_count]),
            starting_fen=starting_fen,
            moves=prefix_moves,
            parent_card_id=None,
            trained_color=trained_color,
        )
    )

    board = chess.Board(starting_fen)
    for move_uci in legal_moves[:prefix_end_offset]:
        board.push_uci(move_uci)
    segment_starting_fen = board.fen()
    segment_moves: list[str] = []
    parent_card_id = prefix_identifier
    learner_decision_index = prefix_decision_count

    for move_uci in legal_moves[prefix_end_offset:]:
        moving_color = board.turn
        decision_fen_key = " ".join(board.fen().split()[:4])
        segment_moves.append(move_uci)
        board.push_uci(move_uci)
        if moving_color != trained_chess_color:
            continue
        identifier = card_id(segment_starting_fen, segment_moves)
        segments.append(
            DecisionSegment(
                card_id=identifier,
                decision_index=len(segments),
                segment_kind="decision",
                first_decision_index=learner_decision_index,
                last_decision_index=learner_decision_index,
                decision_fen_keys=(decision_fen_key,),
                starting_fen=segment_starting_fen,
                moves=tuple(segment_moves),
                parent_card_id=parent_card_id,
                trained_color=trained_color,
            )
        )
        learner_decision_index += 1
        parent_card_id = identifier
        segment_starting_fen = board.fen()
        segment_moves = []

    return tuple(segments)
