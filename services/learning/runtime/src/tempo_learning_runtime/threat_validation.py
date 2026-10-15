"""Conservative engine-evidence validation for defensive knight-fork anchors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Literal

import chess

from .threat_detection import defensive_fork_geometry
from .threat_models import ExerciseAnchor, ThreatPolicy, ThreatSeed


@dataclass(frozen=True)
class EngineScore:
    """A score from White's perspective; mate is never converted to centipawns."""

    cp: int | None = None
    mate: int | None = None

    def __post_init__(self) -> None:
        if (self.cp is None) == (self.mate is None):
            raise ValueError("Exactly one score type is required")


@dataclass(frozen=True)
class AnalysisRequest:
    position_start_fen: str
    position_prefix_uci: tuple[str, ...]
    engine_version: str
    network_version: str
    depth: int
    multipv: int
    root_move_uci: str | None = None

    @property
    def request_id(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class AnalysisLine:
    root_move_uci: str
    pv_uci: tuple[str, ...]
    score: EngineScore
    depth: int


@dataclass(frozen=True)
class AnalysisReport:
    request: AnalysisRequest
    lines: tuple[AnalysisLine, ...]
    complete: bool

    @property
    def report_id(self) -> str:
        payload = {
            "request": asdict(self.request),
            "lines": [asdict(line) for line in self.lines],
            "complete": self.complete,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


ValidationState = Literal[
    "needs_analysis", "engine_supported", "validated_control", "lesson_only", "rejected", "inconclusive"
]


@dataclass(frozen=True)
class ValidationPlan:
    best_request: AnalysisRequest
    historical_request: AnalysisRequest


@dataclass(frozen=True)
class ValidationResult:
    state: ValidationState
    diagnostic: str
    report_ids: tuple[str, ...] = ()
    loss_cp: int | None = None
    net_material_cp: int | None = None
    refutation_uci: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecognitionPreview:
    proposed_move_uci: str
    proposed_move_san: str
    position_fen: str
    fork_move_uci: str
    fork_move_san: str


def make_validation_plan(
    anchor: ExerciseAnchor, *, engine_version: str, network_version: str,
    policy: ThreatPolicy,
) -> ValidationPlan:
    common = dict(
        position_start_fen=anchor.position.start_fen,
        position_prefix_uci=anchor.position.prefix_uci,
        engine_version=engine_version,
        network_version=network_version,
        depth=policy.minimum_depth,
    )
    return ValidationPlan(
        AnalysisRequest(**common, multipv=5),
        AnalysisRequest(**common, multipv=1, root_move_uci=anchor.historical_move_uci),
    )


def _anchor_board(anchor: ExerciseAnchor) -> chess.Board:
    board = chess.Board(anchor.position.start_fen)
    for move_uci in anchor.position.prefix_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Anchor history contains an illegal move")
        board.push(move)
    if ("white" if board.turn else "black") != anchor.position.learner_color:
        raise ValueError("Anchor is not a learner decision")
    return board


def _replay_line(board: chess.Board, line: AnalysisLine) -> tuple[chess.Board, ...]:
    if not line.pv_uci or line.pv_uci[0] != line.root_move_uci:
        raise ValueError("Analysis line lacks its root move")
    states = [board.copy(stack=False)]
    for move_uci in line.pv_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Analysis line contains an illegal move")
        board.push(move)
        states.append(board.copy(stack=False))
    return tuple(states)


def recognition_preview(
    anchor: ExerciseAnchor, seed: ThreatSeed, historical_line: AnalysisLine,
) -> RecognitionPreview | None:
    """Describe an immediate fork using pieces visible after the proposed move."""
    if (len(historical_line.pv_uci) < 2
            or historical_line.pv_uci[0] != anchor.historical_move_uci):
        return None
    board = _anchor_board(anchor)
    proposed_move = chess.Move.from_uci(anchor.historical_move_uci)
    if proposed_move not in board.legal_moves:
        return None
    proposed_move_san = board.san(proposed_move)
    board.push(proposed_move)
    expected = seed.geometry
    if (board.piece_at(chess.parse_square(expected.knight_from))
            != chess.Piece(chess.KNIGHT, expected.attacker_color == "white")
            or board.piece_at(chess.parse_square(expected.king.square))
            != chess.Piece(chess.KING, anchor.position.learner_color == "white")
            or board.piece_at(chess.parse_square(expected.major.square))
            != chess.Piece(chess.QUEEN if expected.major.piece == "queen" else chess.ROOK,
                           anchor.position.learner_color == "white")):
        return None
    fork_move = chess.Move.from_uci(historical_line.pv_uci[1])
    if fork_move not in board.legal_moves:
        return None
    if defensive_fork_geometry(board, fork_move.uci()) != expected:
        return None
    return RecognitionPreview(
        proposed_move_uci=proposed_move.uci(), proposed_move_san=proposed_move_san,
        position_fen=board.fen(), fork_move_uci=fork_move.uci(),
        fork_move_san=board.san(fork_move),
    )


def _learner_loss(
    best: EngineScore, historical: EngineScore, learner_color: str,
) -> int | None:
    sign = 1 if learner_color == "white" else -1
    if best.cp is not None and historical.cp is not None:
        return sign * (best.cp - historical.cp)
    return None


def _mate_outcome(score: EngineScore, learner_color: str) -> int | None:
    if score.mate is None:
        return None
    return (1 if score.mate > 0 else -1) * (1 if learner_color == "white" else -1)


def _fork_consequence(
    states: tuple[chess.Board, ...], line: AnalysisLine, seed: ThreatSeed,
) -> tuple[Literal["proved", "capturable", "absent", "incomplete"], int | None]:
    expected = seed.geometry
    for index, move_uci in enumerate(line.pv_uci):
        before = states[index]
        try:
            geometry = defensive_fork_geometry(before, move_uci)
        except ValueError:
            return "absent", None
        if geometry != expected:
            continue
        fork_board = states[index + 1]
        knight_square = chess.parse_square(expected.knight_to)
        if any(
            move.to_square == knight_square and fork_board.is_capture(move)
            for move in fork_board.legal_moves
        ):
            return "capturable", None
        tracked_square = knight_square
        target_values = {
            chess.parse_square(target.square): 900 if target.piece == "queen" else 500
            for target in (expected.major, *expected.other_majors)
        }
        captured_value: int | None = None
        capture_index: int | None = None
        knight_was_recaptured = False
        for later_index in range(index + 1, len(line.pv_uci)):
            move = chess.Move.from_uci(line.pv_uci[later_index])
            mover = states[later_index].piece_at(move.from_square)
            if mover and mover.color != (expected.attacker_color == "white"):
                if move.to_square == tracked_square and states[later_index].is_capture(move):
                    knight_was_recaptured = True
                    break
            elif mover and mover.piece_type == chess.KNIGHT and move.from_square == tracked_square:
                if move.to_square in target_values and states[later_index].is_capture(move):
                    capture_index = later_index
                    captured_value = target_values[move.to_square]
                tracked_square = move.to_square
        if capture_index is None:
            return "absent" if knight_was_recaptured else "incomplete", None
        if capture_index == len(line.pv_uci) - 1:
            return "incomplete", None
        return "proved", captured_value - (300 if knight_was_recaptured else 0)
    return "absent", None


def _capturable_control_line(
    states: tuple[chess.Board, ...], line: AnalysisLine, seed: ThreatSeed,
) -> tuple[str, ...] | None:
    """Return a concrete capture of the apparent forking knight, if present."""
    for index, move_uci in enumerate(line.pv_uci):
        if defensive_fork_geometry(states[index], move_uci) != seed.geometry:
            continue
        fork_position = states[index + 1]
        knight_square = chess.parse_square(seed.geometry.knight_to)
        captures = sorted(move.uci() for move in fork_position.legal_moves
                          if move.to_square == knight_square and fork_position.is_capture(move))
        if captures:
            return (*line.pv_uci[:index + 1], captures[0])
    return None


def _validated_control(
    board: chess.Board, seed: ThreatSeed, best_report: AnalysisReport,
    historical_line: AnalysisLine, historical_states: tuple[chess.Board, ...],
    loss_cp: int | None, policy: ThreatPolicy,
) -> tuple[str, ...] | None:
    """Require complete, distinct top routes and a legal fork refutation."""
    if (loss_cp is None or loss_cp > policy.correct_tolerance_cp
            or best_report.request.multipv < 5):
        return None
    required_roots = min(5, board.legal_moves.count())
    if len({line.root_move_uci for line in best_report.lines}) < required_roots:
        return None
    minimum_horizon = len(historical_line.pv_uci)
    if minimum_horizon < 4:
        return None
    refutation = _capturable_control_line(historical_states, historical_line, seed)
    if refutation is None:
        return None
    for line in best_report.lines:
        if line.depth < policy.minimum_depth or len(line.pv_uci) < minimum_horizon:
            return None
        try:
            states = _replay_line(board.copy(stack=False), line)
        except ValueError:
            return None
        consequence, _ = _fork_consequence(states, line, seed)
        if consequence in {"proved", "incomplete"}:
            return None
    return refutation


def validate_threat_anchor(
    anchor: ExerciseAnchor, seed: ThreatSeed, plan: ValidationPlan,
    best_report: AnalysisReport | None, historical_report: AnalysisReport | None,
    policy: ThreatPolicy,
) -> ValidationResult:
    """Approve only comparable evidence connecting a bad move to this exact fork."""

    if best_report is None or historical_report is None:
        return ValidationResult("needs_analysis", "Both compatible engine searches are required")
    if (best_report.request != plan.best_request
            or historical_report.request != plan.historical_request):
        return ValidationResult("needs_analysis", "Engine report does not match this request")
    report_ids = (best_report.report_id, historical_report.report_id)
    if not best_report.complete or not historical_report.complete:
        return ValidationResult("inconclusive", "Engine search was incomplete", report_ids)
    if not best_report.lines or not historical_report.lines:
        return ValidationResult("inconclusive", "Engine returned no usable line", report_ids)
    best_line = best_report.lines[0]
    historical_line = historical_report.lines[0]
    if (best_line.depth < policy.minimum_depth
            or historical_line.depth < policy.minimum_depth
            or historical_line.root_move_uci != anchor.historical_move_uci):
        return ValidationResult("inconclusive", "Analysis depth or root move is insufficient", report_ids)
    try:
        board = _anchor_board(anchor)
        _replay_line(board.copy(stack=False), best_line)
        states = _replay_line(board.copy(stack=False), historical_line)
    except ValueError:
        return ValidationResult("rejected", "Engine line is illegal or anchor history is invalid", report_ids)
    if recognition_preview(anchor, seed, historical_line) is None:
        return ValidationResult(
            "lesson_only", "Fork is not an immediate, board-visible reply to the proposed move",
            report_ids,
        )
    loss_cp = _learner_loss(best_line.score, historical_line.score,
                            anchor.position.learner_color)
    if loss_cp is None:
        best_mate = _mate_outcome(best_line.score, anchor.position.learner_color)
        historical_mate = _mate_outcome(historical_line.score, anchor.position.learner_color)
        if historical_mate == -1 and best_mate != -1:
            materially_worse = True
        else:
            return ValidationResult("inconclusive", "Mate and centipawn results are not comparable", report_ids)
    else:
        materially_worse = loss_cp >= policy.minimum_loss_cp
    if not materially_worse:
        control_refutation = _validated_control(
            board, seed, best_report, historical_line, states, loss_cp, policy,
        )
        if control_refutation:
            return ValidationResult(
                "validated_control", "Apparent checking fork is refuted by a legal capture",
                report_ids, loss_cp=loss_cp, refutation_uci=control_refutation,
            )
        return ValidationResult("lesson_only", "Earlier move was not materially worse", report_ids,
                                loss_cp=loss_cp)
    consequence, net_material_cp = _fork_consequence(states, historical_line, seed)
    if consequence == "capturable":
        return ValidationResult("rejected", "Forking knight can be captured legally", report_ids,
                                loss_cp=loss_cp)
    if consequence == "incomplete":
        return ValidationResult("inconclusive", "Refutation does not finish the fork consequence",
                                report_ids, loss_cp=loss_cp)
    if consequence != "proved" or net_material_cp is None or net_material_cp < 200:
        return ValidationResult("lesson_only", "Loss is not proved by this knight fork", report_ids,
                                loss_cp=loss_cp)
    return ValidationResult("engine_supported", "Historical move permits a concrete knight fork",
                            report_ids, loss_cp=loss_cp, net_material_cp=net_material_cp,
                            refutation_uci=historical_line.pv_uci)
