"""Deterministic, evidence-based tactical motif detectors.

The detector contract deliberately stays in Python until the same fixtures can
be evaluated by tempo-core.  Detectors return evidence objects rather than a
single label so secondary motifs remain reviewable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import chess


PIECE_VALUES_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
MINIMUM_MATERIAL_GAIN_CP = 300
MINIMUM_EVALUATION_SWING_CP = 100
MATE_SCORE_CP = 1_000_000

MOTIF_PRECEDENCE = (
    "matingTactic",
    "pin",
    "fork",
    "skewer",
    "discoveredAttack",
    "hangingPiece",
)


@dataclass(frozen=True)
class EngineCandidate:
    """One legal engine candidate, optionally annotated with its score."""

    move_uci: str
    cp: int | None = None
    mate: int | None = None
    is_played: bool = False


@dataclass(frozen=True)
class MotifEvidence:
    """Structured proof that one motif occurs in one candidate line."""

    motif: str
    confidence: float
    candidate_move_uci: str
    played_move_uci: str | None
    involved_squares: dict[str, str]
    involved_pieces: tuple[dict[str, Any], ...]
    existed_before: bool
    created_by_candidate_move: bool
    proving_pv_segment: tuple[str, ...]
    concrete_outcome: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "motif": self.motif,
            "confidence": self.confidence,
            "candidate_move_uci": self.candidate_move_uci,
            "played_move_uci": self.played_move_uci,
            "involved_squares": dict(self.involved_squares),
            "involved_pieces": [dict(piece) for piece in self.involved_pieces],
            "existed_before": self.existed_before,
            "created_by_candidate_move": self.created_by_candidate_move,
            "proving_pv_segment": list(self.proving_pv_segment),
            "concrete_outcome": dict(self.concrete_outcome),
        }


@dataclass(frozen=True)
class _PinGeometry:
    pinner_square: chess.Square
    pinned_square: chess.Square
    target_square: chess.Square
    pin_type: str

    @property
    def key(self) -> tuple[int, int, int, str]:
        return (
            self.pinner_square,
            self.pinned_square,
            self.target_square,
            self.pin_type,
        )


def _piece_value_cp(piece: chess.Piece | None) -> int:
    return PIECE_VALUES_CP.get(piece.piece_type, 0) if piece else 0


def _piece_evidence(board: chess.Board, square: chess.Square, role: str) -> dict[str, Any]:
    piece = board.piece_at(square)
    return {
        "role": role,
        "square": chess.square_name(square),
        "piece": piece.symbol() if piece else None,
        "color": "white" if piece and piece.color else "black" if piece else None,
    }


def _direction_between(
    first: chess.Square, second: chess.Square
) -> tuple[int, int] | None:
    file_delta = chess.square_file(second) - chess.square_file(first)
    rank_delta = chess.square_rank(second) - chess.square_rank(first)
    if file_delta and rank_delta and abs(file_delta) != abs(rank_delta):
        return None
    if not file_delta and not rank_delta:
        return None
    return (
        0 if file_delta == 0 else 1 if file_delta > 0 else -1,
        0 if rank_delta == 0 else 1 if rank_delta > 0 else -1,
    )


def _step(square: chess.Square, direction: tuple[int, int]) -> chess.Square | None:
    file_index = chess.square_file(square) + direction[0]
    rank_index = chess.square_rank(square) + direction[1]
    if not (0 <= file_index < 8 and 0 <= rank_index < 8):
        return None
    return chess.square(file_index, rank_index)


def _line_between(
    first: chess.Square, second: chess.Square
) -> tuple[chess.Square, ...]:
    direction = _direction_between(first, second)
    if direction is None:
        return ()
    squares: list[chess.Square] = []
    current = _step(first, direction)
    while current is not None and current != second:
        squares.append(current)
        current = _step(current, direction)
    return tuple(squares) if current == second else ()


def _directions_for_piece(piece_type: chess.PieceType) -> tuple[tuple[int, int], ...]:
    orthogonal = ((1, 0), (-1, 0), (0, 1), (0, -1))
    diagonal = ((1, 1), (1, -1), (-1, 1), (-1, -1))
    if piece_type == chess.ROOK:
        return orthogonal
    if piece_type == chess.BISHOP:
        return diagonal
    if piece_type == chess.QUEEN:
        return orthogonal + diagonal
    return ()


def _find_pins(board: chess.Board) -> tuple[_PinGeometry, ...]:
    geometries: list[_PinGeometry] = []
    for pinner_color in (chess.WHITE, chess.BLACK):
        for piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            for pinner_square in board.pieces(piece_type, pinner_color):
                for direction in _directions_for_piece(piece_type):
                    pinned_square = _step(pinner_square, direction)
                    while pinned_square is not None and board.piece_at(pinned_square) is None:
                        pinned_square = _step(pinned_square, direction)
                    if pinned_square is None:
                        continue
                    pinned_piece = board.piece_at(pinned_square)
                    if not pinned_piece or pinned_piece.color == pinner_color:
                        continue
                    target_square = _step(pinned_square, direction)
                    while target_square is not None and board.piece_at(target_square) is None:
                        target_square = _step(target_square, direction)
                    if target_square is None:
                        continue
                    target_piece = board.piece_at(target_square)
                    if not target_piece or target_piece.color != pinned_piece.color:
                        continue
                    if target_piece.piece_type == chess.KING:
                        pin_type = "absolute"
                    elif _piece_value_cp(target_piece) > _piece_value_cp(pinned_piece):
                        pin_type = "relative"
                    else:
                        continue
                    geometries.append(
                        _PinGeometry(
                            pinner_square,
                            pinned_square,
                            target_square,
                            pin_type,
                        )
                    )
    return tuple(geometries)


def _replay_line(
    position: chess.Board, resulting_line: Sequence[str]
) -> tuple[tuple[chess.Board, ...], tuple[chess.Move, ...]]:
    boards: list[chess.Board] = [position.copy()]
    moves: list[chess.Move] = []
    board = position.copy()
    for move_uci in resulting_line:
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        moves.append(move)
        board.push(move)
        boards.append(board.copy())
    return tuple(boards), tuple(moves)


def _captured_square(board: chess.Board, move: chess.Move) -> chess.Square | None:
    if not board.is_capture(move):
        return None
    if board.is_en_passant(move):
        return move.to_square - 8 if board.turn == chess.WHITE else move.to_square + 8
    return move.to_square


def _material_balance_cp(board: chess.Board, color: chess.Color) -> int:
    balance = 0
    for piece_type, value in PIECE_VALUES_CP.items():
        if piece_type == chess.KING:
            continue
        balance += len(board.pieces(piece_type, color)) * value
        balance -= len(board.pieces(piece_type, not color)) * value
    return balance


def _score_cp(candidate: EngineCandidate) -> int | None:
    if candidate.mate is not None:
        if candidate.mate == 0:
            return MATE_SCORE_CP
        return (MATE_SCORE_CP - abs(candidate.mate)) * (1 if candidate.mate > 0 else -1)
    return candidate.cp


def _evaluation_outcome(
    candidate: EngineCandidate,
    engine_candidates: Sequence[EngineCandidate],
) -> dict[str, Any] | None:
    candidate_score = _score_cp(candidate)
    played = next((item for item in engine_candidates if item.is_played), None)
    played_score = _score_cp(played) if played else None
    if candidate_score is None or played_score is None:
        return None
    swing_cp = candidate_score - played_score
    if swing_cp < MINIMUM_EVALUATION_SWING_CP:
        return None
    return {
        "type": "evaluation_swing",
        "evaluation_swing_cp": swing_cp,
        "candidate_score_cp": candidate_score,
        "played_score_cp": played_score,
    }


def _line_consequence(
    boards: Sequence[chess.Board],
    moves: Sequence[chess.Move],
    line: Sequence[str],
    *,
    baseline_state_index: int,
    actor_color: chess.Color,
    relevant_squares: set[chess.Square] | None = None,
    candidate: EngineCandidate | None = None,
    engine_candidates: Sequence[EngineCandidate] = (),
) -> tuple[int, dict[str, Any]] | None:
    baseline = boards[baseline_state_index]
    for move_index, move in enumerate(moves):
        before = boards[move_index]
        after = boards[move_index + 1]
        if before.turn != actor_color:
            continue
        if after.is_checkmate() and after.turn != actor_color:
            return move_index, {
                "type": "mate",
                "mate_for": "white" if actor_color else "black",
                "mate_in_plies": move_index + 1 - baseline_state_index,
            }
        captured_square = _captured_square(before, move)
        if captured_square is None:
            continue
        if relevant_squares is not None and captured_square not in relevant_squares:
            continue
        captured_piece = before.piece_at(captured_square)
        if not captured_piece or captured_piece.color == actor_color:
            continue
        material_gain_cp = _material_balance_cp(after, actor_color) - _material_balance_cp(
            baseline, actor_color
        )
        if material_gain_cp >= MINIMUM_MATERIAL_GAIN_CP:
            return move_index, {
                "type": "material_gain",
                "material_gain_cp": material_gain_cp,
                "captured_square": chess.square_name(captured_square),
                "captured_piece": captured_piece.symbol(),
            }
    if candidate is not None:
        evaluation_outcome = _evaluation_outcome(candidate, engine_candidates)
        if evaluation_outcome:
            return max(0, len(moves) - 1), evaluation_outcome
    return None


def _evidence(
    *,
    motif: str,
    confidence: float,
    position: chess.Board,
    played_move: chess.Move | None,
    candidate_move: chess.Move,
    involved_squares: dict[str, chess.Square],
    involved_roles: dict[str, chess.Square],
    existed_before: bool,
    created_by_candidate_move: bool,
    line: Sequence[str],
    consequence_index: int,
    concrete_outcome: dict[str, Any],
) -> MotifEvidence:
    return MotifEvidence(
        motif=motif,
        confidence=confidence,
        candidate_move_uci=candidate_move.uci(),
        played_move_uci=played_move.uci() if played_move else None,
        involved_squares={
            name: chess.square_name(square) for name, square in involved_squares.items()
        },
        involved_pieces=tuple(
            _piece_evidence(position, square, role)
            for role, square in involved_roles.items()
        ),
        existed_before=existed_before,
        created_by_candidate_move=created_by_candidate_move,
        proving_pv_segment=tuple(line[: consequence_index + 1]),
        concrete_outcome=concrete_outcome,
    )


def detect_pins(
    position: chess.Board,
    played_move: chess.Move | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    """Detect only pins that the candidate line concretely converts."""

    boards, moves = _replay_line(position, resulting_line)
    if not moves:
        return []
    candidate_move = moves[0]
    candidate = next(
        (item for item in engine_candidates if item.move_uci == candidate_move.uci()),
        EngineCandidate(candidate_move.uci()),
    )
    before_pins = {geometry.key: geometry for geometry in _find_pins(boards[0])}
    line_pins: dict[tuple[int, int, int, str], tuple[_PinGeometry, int]] = {}
    for state_index, line_board in enumerate(boards[1:], start=1):
        for geometry in _find_pins(line_board):
            line_pins.setdefault(geometry.key, (geometry, state_index))
    detections: list[MotifEvidence] = []
    for geometry_key in set(before_pins) | set(line_pins):
        geometry = before_pins.get(geometry_key) or line_pins[geometry_key][0]
        existed_before = geometry.key in before_pins
        created_by_candidate_move = not existed_before
        pin_state_index = 0 if existed_before else line_pins[geometry.key][1]
        if not existed_before and pin_state_index != 1:
            continue
        consequence = _line_consequence(
            boards,
            moves,
            resulting_line,
            baseline_state_index=pin_state_index,
            actor_color=position.turn,
            relevant_squares={geometry.pinned_square, geometry.target_square},
            candidate=candidate,
            engine_candidates=engine_candidates,
        )
        if consequence is None:
            continue
        consequence_index, concrete_outcome = consequence
        concrete_outcome = {
            **concrete_outcome,
            "pin_type": geometry.pin_type,
            "pin_state_ply": pin_state_index,
        }
        detections.append(
            _evidence(
                motif="pin",
                confidence=0.98 if concrete_outcome["type"] == "mate" else 0.95,
                position=boards[pin_state_index],
                played_move=played_move,
                candidate_move=candidate_move,
                involved_squares={
                    "pinner": geometry.pinner_square,
                    "pinned_piece": geometry.pinned_square,
                    "protected_target": geometry.target_square,
                },
                involved_roles={
                    "pinner": geometry.pinner_square,
                    "pinned_piece": geometry.pinned_square,
                    "protected_target": geometry.target_square,
                },
                existed_before=existed_before,
                created_by_candidate_move=created_by_candidate_move,
                line=resulting_line,
                consequence_index=consequence_index,
                concrete_outcome=concrete_outcome,
            )
        )
    return detections


def _attacked_targets(
    board: chess.Board, attacker_square: chess.Square, attacker_color: chess.Color
) -> tuple[chess.Square, ...]:
    return tuple(
        square
        for square in board.attacks(attacker_square)
        if (piece := board.piece_at(square))
        and piece.color != attacker_color
        and piece.piece_type != chess.KING
        and _piece_value_cp(piece) >= 300
    )


def detect_forks(
    position: chess.Board,
    played_move: chess.Move | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    boards, moves = _replay_line(position, resulting_line)
    if not moves:
        return []
    candidate_move = moves[0]
    after = boards[1]
    moved_piece = after.piece_at(candidate_move.to_square)
    if not moved_piece:
        return []
    targets = _attacked_targets(after, candidate_move.to_square, position.turn)
    if len(targets) < 2:
        return []
    candidate = next(
        (item for item in engine_candidates if item.move_uci == candidate_move.uci()),
        EngineCandidate(candidate_move.uci()),
    )
    consequence = _line_consequence(
        boards,
        moves,
        resulting_line,
        baseline_state_index=1,
        actor_color=position.turn,
        relevant_squares=set(targets),
        candidate=candidate,
        engine_candidates=engine_candidates,
    )
    if consequence is None:
        return []
    consequence_index, concrete_outcome = consequence
    target_pieces = tuple(
        _piece_evidence(after, square, "fork_target") for square in targets
    )
    return [
        MotifEvidence(
            motif="fork",
            confidence=0.94,
            candidate_move_uci=candidate_move.uci(),
            played_move_uci=played_move.uci() if played_move else None,
            involved_squares={
                "forking_piece": chess.square_name(candidate_move.to_square),
                "target_1": chess.square_name(targets[0]),
                "target_2": chess.square_name(targets[1]),
            },
            involved_pieces=(
                _piece_evidence(after, candidate_move.to_square, "forking_piece"),
                *target_pieces,
            ),
            existed_before=False,
            created_by_candidate_move=True,
            proving_pv_segment=tuple(resulting_line[: consequence_index + 1]),
            concrete_outcome=concrete_outcome,
        )
    ]


def _find_skewers(board: chess.Board, attacker_color: chess.Color) -> tuple[tuple[chess.Square, chess.Square, chess.Square], ...]:
    skewers: list[tuple[chess.Square, chess.Square, chess.Square]] = []
    for piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        for pinner_square in board.pieces(piece_type, attacker_color):
            for direction in _directions_for_piece(piece_type):
                front_square = _step(pinner_square, direction)
                while front_square is not None and board.piece_at(front_square) is None:
                    front_square = _step(front_square, direction)
                if front_square is None:
                    continue
                front_piece = board.piece_at(front_square)
                back_square = _step(front_square, direction)
                while back_square is not None and board.piece_at(back_square) is None:
                    back_square = _step(back_square, direction)
                if back_square is None:
                    continue
                back_piece = board.piece_at(back_square)
                if (
                    front_piece
                    and back_piece
                    and front_piece.color != attacker_color
                    and back_piece.color == front_piece.color
                    and front_piece.piece_type != chess.KING
                    and back_piece.piece_type != chess.KING
                    and _piece_value_cp(front_piece) > _piece_value_cp(back_piece)
                ):
                    skewers.append((pinner_square, front_square, back_square))
    return tuple(skewers)


def detect_skewers(
    position: chess.Board,
    played_move: chess.Move | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    boards, moves = _replay_line(position, resulting_line)
    if not moves:
        return []
    candidate_move = moves[0]
    before = {item for item in _find_skewers(boards[0], position.turn)}
    after = _find_skewers(boards[1], position.turn)
    candidate = next(
        (item for item in engine_candidates if item.move_uci == candidate_move.uci()),
        EngineCandidate(candidate_move.uci()),
    )
    detections: list[MotifEvidence] = []
    for pinner_square, front_square, back_square in after:
        existed_before = (pinner_square, front_square, back_square) in before
        baseline_state_index = 0 if existed_before else 1
        consequence = _line_consequence(
            boards,
            moves,
            resulting_line,
            baseline_state_index=baseline_state_index,
            actor_color=position.turn,
            relevant_squares={back_square},
            candidate=candidate,
            engine_candidates=engine_candidates,
        )
        if consequence is None:
            continue
        consequence_index, concrete_outcome = consequence
        detections.append(
            _evidence(
                motif="skewer",
                confidence=0.92,
                position=boards[baseline_state_index],
                played_move=played_move,
                candidate_move=candidate_move,
                involved_squares={
                    "skewer_piece": pinner_square,
                    "front_target": front_square,
                    "back_target": back_square,
                },
                involved_roles={
                    "skewer_piece": pinner_square,
                    "front_target": front_square,
                    "back_target": back_square,
                },
                existed_before=existed_before,
                created_by_candidate_move=not existed_before,
                line=resulting_line,
                consequence_index=consequence_index,
                concrete_outcome=concrete_outcome,
            )
        )
    return detections


def detect_discovered_attacks(
    position: chess.Board,
    played_move: chess.Move | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    boards, moves = _replay_line(position, resulting_line)
    if not moves:
        return []
    candidate_move = moves[0]
    before, after = boards[0], boards[1]
    detections: list[MotifEvidence] = []
    for piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        for slider_square in after.pieces(piece_type, position.turn):
            for target_square in _attacked_targets(after, slider_square, position.turn):
                if target_square in before.attacks(slider_square):
                    continue
                if candidate_move.from_square not in _line_between(slider_square, target_square):
                    continue
                candidate = next(
                    (item for item in engine_candidates if item.move_uci == candidate_move.uci()),
                    EngineCandidate(candidate_move.uci()),
                )
                consequence = _line_consequence(
                    boards,
                    moves,
                    resulting_line,
                    baseline_state_index=0,
                    actor_color=position.turn,
                    relevant_squares={target_square},
                    candidate=candidate,
                    engine_candidates=engine_candidates,
                )
                if consequence is None:
                    continue
                consequence_index, concrete_outcome = consequence
                detections.append(
                    _evidence(
                        motif="discoveredAttack",
                        confidence=0.91,
                        position=position,
                        played_move=played_move,
                        candidate_move=candidate_move,
                        involved_squares={
                            "discovered_slider": slider_square,
                            "discovered_target": target_square,
                            "vacated_square": candidate_move.from_square,
                        },
                        involved_roles={
                            "discovered_slider": slider_square,
                            "discovered_target": target_square,
                            "vacated_square": candidate_move.from_square,
                        },
                        existed_before=False,
                        created_by_candidate_move=True,
                        line=resulting_line,
                        consequence_index=consequence_index,
                        concrete_outcome=concrete_outcome,
                    )
                )
    return detections


def detect_hanging_pieces(
    position: chess.Board,
    played_move: chess.Move | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    boards, moves = _replay_line(position, resulting_line)
    if not moves:
        return []
    candidate_move = moves[0]
    captured_square = _captured_square(position, candidate_move)
    if captured_square is None:
        return []
    captured_piece = position.piece_at(captured_square)
    if not captured_piece or position.attackers(captured_piece.color, captured_square):
        return []
    candidate = next(
        (item for item in engine_candidates if item.move_uci == candidate_move.uci()),
        EngineCandidate(candidate_move.uci()),
    )
    consequence = _line_consequence(
        boards,
        moves,
        resulting_line,
        baseline_state_index=0,
        actor_color=position.turn,
        relevant_squares={captured_square},
        candidate=candidate,
        engine_candidates=engine_candidates,
    )
    if consequence is None:
        return []
    consequence_index, concrete_outcome = consequence
    return [
        _evidence(
            motif="hangingPiece",
            confidence=0.9,
            position=position,
            played_move=played_move,
            candidate_move=candidate_move,
            involved_squares={"hanging_piece": captured_square},
            involved_roles={"hanging_piece": captured_square},
            existed_before=True,
            created_by_candidate_move=False,
            line=resulting_line,
            consequence_index=consequence_index,
            concrete_outcome=concrete_outcome,
        )
    ]


def detect_mating_patterns(
    position: chess.Board,
    played_move: chess.Move | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    boards, moves = _replay_line(position, resulting_line)
    if not moves:
        return []
    candidate_move = moves[0]
    for move_index, after in enumerate(boards[1:]):
        if not after.is_checkmate() or after.turn == position.turn:
            continue
        losing_king = after.king(after.turn)
        if losing_king is None:
            continue
        pattern = (
            "backRankMate"
            if losing_king is not None and chess.square_rank(losing_king) in {0, 7}
            else "forcedMate"
        )
        return [
            _evidence(
                motif="matingTactic",
                confidence=0.99,
                position=position,
                played_move=played_move,
                candidate_move=candidate_move,
                involved_squares={
                    "mating_move_from": moves[move_index].from_square,
                    "mating_move_to": moves[move_index].to_square,
                    "mated_king": losing_king,
                },
                involved_roles={"mated_king": losing_king},
                existed_before=False,
                created_by_candidate_move=move_index == 0,
                line=resulting_line,
                consequence_index=move_index,
                concrete_outcome={
                    "type": "mate",
                    "pattern": pattern,
                    "mate_in_plies": move_index + 1,
                },
            )
        ]
    return []


MotifDetector = Callable[
    [chess.Board, chess.Move | None, Sequence[EngineCandidate], Sequence[str]],
    list[MotifEvidence],
]

DETECTORS: tuple[MotifDetector, ...] = (
    detect_pins,
    detect_forks,
    detect_skewers,
    detect_discovered_attacks,
    detect_hanging_pieces,
    detect_mating_patterns,
)


def classify_tactical_motifs(
    position: chess.Board,
    played_move_uci: str | None,
    engine_candidates: Sequence[EngineCandidate],
    resulting_line: Sequence[str],
) -> list[MotifEvidence]:
    """Run every deterministic detector and preserve all matching evidence."""

    played_move: chess.Move | None = None
    if played_move_uci:
        try:
            candidate_move = chess.Move.from_uci(played_move_uci)
            if candidate_move in position.legal_moves:
                played_move = candidate_move
        except ValueError:
            played_move = None
    evidence: list[MotifEvidence] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]]] = set()
    for detector in DETECTORS:
        for item in detector(position.copy(), played_move, engine_candidates, resulting_line):
            key = (
                item.motif,
                tuple(sorted(item.involved_squares.items())),
                item.proving_pv_segment,
            )
            if key in seen:
                continue
            seen.add(key)
            evidence.append(item)
    return evidence


def select_primary_motif(evidence: Sequence[MotifEvidence]) -> MotifEvidence | None:
    """Select the documented primary label without discarding secondary evidence."""

    precedence = {motif: index for index, motif in enumerate(MOTIF_PRECEDENCE)}
    return min(
        evidence,
        key=lambda item: (precedence.get(item.motif, len(precedence)), -item.confidence),
        default=None,
    )


def _normalized_candidate_score(value: int | None, mover_color: str) -> int | None:
    if value is None:
        return None
    return value if mover_color == "white" else -value


def classify_candidate_lines(
    position: chess.Board,
    played_move_uci: str | None,
    candidate_lines: Sequence[dict[str, Any]],
    *,
    best_move_uci: str | None = None,
    principal_variation: Sequence[str] = (),
    actual_after_cp: int | None = None,
    mover_color: str | None = None,
) -> list[MotifEvidence]:
    """Run the common detector contract over bounded MultiPV lines."""

    analyzed_color = mover_color or ("white" if position.turn else "black")
    normalized_candidates: list[EngineCandidate] = []
    lines: list[tuple[EngineCandidate, Sequence[str]]] = []
    for raw_candidate in candidate_lines:
        move_uci = raw_candidate.get("uci") or raw_candidate.get("move_uci")
        line = raw_candidate.get("pv") or raw_candidate.get("principal_variation") or []
        if not move_uci or not line or line[0] != move_uci:
            continue
        candidate = EngineCandidate(
            move_uci=move_uci,
            cp=_normalized_candidate_score(raw_candidate.get("cp"), analyzed_color),
            mate=_normalized_candidate_score(raw_candidate.get("mate"), analyzed_color),
            is_played=move_uci == played_move_uci,
        )
        normalized_candidates.append(candidate)
        lines.append((candidate, line))
    if not lines and best_move_uci and principal_variation:
        fallback = EngineCandidate(best_move_uci)
        normalized_candidates.append(fallback)
        lines.append((fallback, principal_variation))
    if played_move_uci and not any(item.move_uci == played_move_uci for item in normalized_candidates):
        normalized_candidates.append(
            EngineCandidate(
                move_uci=played_move_uci,
                cp=_normalized_candidate_score(actual_after_cp, analyzed_color),
                is_played=True,
            )
        )
    all_evidence: list[MotifEvidence] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for candidate, line in lines:
        for item in classify_tactical_motifs(
            position,
            played_move_uci,
            normalized_candidates,
            line,
        ):
            key = (item.motif, tuple(sorted(item.involved_squares.items())))
            if key in seen:
                continue
            seen.add(key)
            all_evidence.append(item)
    return all_evidence
