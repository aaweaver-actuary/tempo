"""Legal geometry and actual-piece route tracing for defensive knight forks."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

import chess

from .threat_models import (
    ExerciseAnchor, ForkGeometry, ForkTarget, GameSnapshot, KnightHop,
    PositionContext, SourceLine, ThreatPolicy, ThreatSeed,
)


def _color_name(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def defensive_fork_geometry(position: chess.Board, move_uci: str) -> ForkGeometry | None:
    """Return king-and-major geometry only for a legal knight move."""

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as error:
        raise ValueError("Malformed knight move") from error
    if move not in position.legal_moves:
        raise ValueError("Illegal knight move")
    moving_piece = position.piece_at(move.from_square)
    if moving_piece is None or moving_piece.piece_type != chess.KNIGHT:
        return None
    after = position.copy(stack=False)
    after.push(move)
    attacked = after.attacks(move.to_square)
    enemy_color = not moving_piece.color
    king_square = after.king(enemy_color)
    if king_square is None or king_square not in attacked:
        return None
    major_targets = sorted(
        square for square in attacked
        if (piece := after.piece_at(square)) is not None
        and piece.color == enemy_color
        and piece.piece_type in (chess.ROOK, chess.QUEEN)
    )
    if not major_targets:
        return None
    major_square = major_targets[0]
    major_piece = after.piece_at(major_square)
    assert major_piece is not None
    return ForkGeometry(
        attacker_color=_color_name(moving_piece.color),
        knight_from=chess.square_name(move.from_square),
        knight_to=chess.square_name(move.to_square),
        king=ForkTarget(chess.square_name(king_square), "king"),
        major=ForkTarget(
            chess.square_name(major_square),
            "queen" if major_piece.piece_type == chess.QUEEN else "rook",
        ),
        other_majors=tuple(
            ForkTarget(
                chess.square_name(square),
                "queen" if after.piece_at(square).piece_type == chess.QUEEN else "rook",
            ) for square in major_targets[1:]
        ),
    )


def _replay_legal(board: chess.Board, moves: tuple[str, ...]) -> None:
    for move_uci in moves:
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError as error:
            raise ValueError("Malformed source move") from error
        if move not in board.legal_moves:
            raise ValueError("Illegal source move")
        board.push(move)


def find_defensive_knight_forks(
    game: GameSnapshot, source_line: SourceLine,
) -> tuple[ThreatSeed, ...]:
    """Find candidate evidence without claiming the fork wins material."""

    if source_line.start_ply < 0 or source_line.start_ply > len(game.moves_uci):
        raise ValueError("Source line begins outside the played game")
    if source_line.origin == "played":
        expected = game.moves_uci[source_line.start_ply:source_line.start_ply + len(source_line.moves_uci)]
        if expected != source_line.moves_uci:
            raise ValueError("Played evidence differs from the actual game")
    board = chess.Board(game.start_fen)
    _replay_legal(board, game.moves_uci[:source_line.start_ply])
    seeds: list[ThreatSeed] = []
    for line_index, move_uci in enumerate(source_line.moves_uci):
        geometry = defensive_fork_geometry(board, move_uci)
        if geometry and geometry.attacker_color != game.learner_color:
            evidence_key = json.dumps({
                "game_id": game.game_id,
                "analysis_version": game.analysis_version,
                "origin": source_line.origin,
                "start_ply": source_line.start_ply,
                "line_to_fork": source_line.moves_uci[:line_index + 1],
                "geometry": asdict(geometry),
            }, sort_keys=True)
            seeds.append(ThreatSeed(
                seed_id=hashlib.sha256(evidence_key.encode()).hexdigest(),
                game_id=game.game_id,
                analysis_version=game.analysis_version,
                source_line=SourceLine(
                    source_line.origin, source_line.start_ply,
                    source_line.moves_uci[:line_index + 1],
                ),
                fork_line_index=line_index,
                geometry=geometry,
            ))
        _replay_legal(board, (move_uci,))
    return tuple(seeds)


def trace_knight_route(
    game: GameSnapshot, seed: ThreatSeed, *, max_hops: int = 3,
) -> tuple[KnightHop, ...]:
    """Follow the specific knight backward through actual moves and its source PV."""

    if max_hops < 0:
        raise ValueError("max_hops must be nonnegative")
    source = seed.source_line
    combined = game.moves_uci[:source.start_ply] + source.moves_uci[:seed.fork_line_index + 1]
    board = chess.Board(game.start_fen)
    before: list[chess.Board] = []
    for move_uci in combined:
        before.append(board.copy(stack=False))
        _replay_legal(board, (move_uci,))
    tracked_square = chess.parse_square(seed.geometry.knight_to)
    hops: list[KnightHop] = []
    for ply in range(len(combined) - 1, -1, -1):
        move = chess.Move.from_uci(combined[ply])
        if move.to_square != tracked_square:
            continue
        moving_piece = before[ply].piece_at(move.from_square)
        if moving_piece is None or moving_piece.piece_type != chess.KNIGHT:
            break
        hops.append(KnightHop(
            ply=ply,
            from_square=chess.square_name(move.from_square),
            to_square=chess.square_name(move.to_square),
            origin="played" if ply < source.start_ply else source.origin,
        ))
        tracked_square = move.from_square
        if len(hops) >= max_hops:
            break
    return tuple(reversed(hops))


def propose_exercise_anchors(
    game: GameSnapshot, seed: ThreatSeed, policy: ThreatPolicy,
) -> tuple[ExerciseAnchor, ...]:
    """Select only positions the learner really faced, nearest decision first."""

    latest_exclusive_ply = (
        min(seed.fork_ply, len(game.moves_uci))
        if seed.source_line.origin == "played"
        else seed.source_line.start_ply + 1
    )
    board = chess.Board(game.start_fen)
    decisions: list[tuple[int, str]] = []
    for ply, move_uci in enumerate(game.moves_uci[:latest_exclusive_ply]):
        if _color_name(board.turn) == game.learner_color:
            decisions.append((ply, move_uci))
        _replay_legal(board, (move_uci,))
    selected = list(reversed(decisions[-policy.max_prior_decisions:]))
    return tuple(
        ExerciseAnchor(
            seed_id=seed.seed_id,
            player_ply=ply,
            position=PositionContext(game.start_fen, game.moves_uci[:ply], game.learner_color),
            historical_move_uci=move_uci,
            decisions_before_event=index,
        )
        for index, (ply, move_uci) in enumerate(selected)
    )
