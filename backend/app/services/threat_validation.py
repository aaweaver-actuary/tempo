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
    "needs_analysis", "engine_supported", "lesson_only", "rejected", "inconclusive"
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
        states = _replay_line(board, historical_line)
    except ValueError:
        return ValidationResult("rejected", "Engine line is illegal or anchor history is invalid", report_ids)
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
