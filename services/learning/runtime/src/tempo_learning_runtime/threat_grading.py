"""Engine-backed grading for a defensive move, independent of card scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import chess

from .threat_models import ExerciseAnchor, ThreatPolicy, ThreatSeed
from .threat_validation import (
    AnalysisLine, AnalysisReport, AnalysisRequest,
    _fork_consequence, _learner_loss, _mate_outcome, _replay_line,
)


GradeStatus = Literal["correct", "incorrect", "needs_analysis", "ambiguous", "illegal"]


@dataclass(frozen=True)
class DefenseExercise:
    candidate_id: str
    finding_id: str
    exercise_revision: int
    anchor: ExerciseAnchor
    seed: ThreatSeed
    policy: ThreatPolicy
    best_report: AnalysisReport
    evaluated_reports: tuple[AnalysisReport, ...]
    refutation_uci: tuple[str, ...]


@dataclass(frozen=True)
class MoveGrade:
    status: GradeStatus
    diagnostic: str
    move_uci: str
    loss_cp: int | None = None
    allows_target_fork: bool = False
    analysis_request: AnalysisRequest | None = None


def _board_at_anchor(anchor: ExerciseAnchor) -> chess.Board:
    board = chess.Board(anchor.position.start_fen)
    for move_uci in anchor.position.prefix_uci:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Exercise history is illegal")
        board.push(move)
    return board


def _compatible_root_report(
    exercise: DefenseExercise, move_uci: str, additional: AnalysisReport | None,
) -> tuple[AnalysisLine, AnalysisReport] | None:
    best_request = exercise.best_report.request
    reports = (exercise.best_report,) + exercise.evaluated_reports + ((additional,) if additional else ())
    for report in reports:
        request = report.request
        if (not report.complete
                or request.position_start_fen != best_request.position_start_fen
                or request.position_prefix_uci != best_request.position_prefix_uci
                or request.engine_version != best_request.engine_version
                or request.network_version != best_request.network_version
                or request.depth < exercise.policy.minimum_depth):
            continue
        if request.root_move_uci not in (None, move_uci):
            continue
        for line in report.lines:
            if line.root_move_uci == move_uci and line.depth >= exercise.policy.minimum_depth:
                return line, report
    return None


def _needed_request(exercise: DefenseExercise, move_uci: str) -> AnalysisRequest:
    best = exercise.best_report.request
    return AnalysisRequest(
        position_start_fen=best.position_start_fen,
        position_prefix_uci=best.position_prefix_uci,
        engine_version=best.engine_version,
        network_version=best.network_version,
        depth=best.depth,
        multipv=1,
        root_move_uci=move_uci,
    )


def grade_defense_move(
    exercise: DefenseExercise, move_uci: str,
    *, additional_analysis: AnalysisReport | None = None,
) -> MoveGrade:
    board = _board_at_anchor(exercise.anchor)
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return MoveGrade("illegal", "Move is malformed", move_uci)
    if move not in board.legal_moves:
        return MoveGrade("illegal", "Move is illegal from this position", move_uci)
    best = exercise.best_report
    if not best.complete or not best.lines or best.lines[0].depth < exercise.policy.minimum_depth:
        return MoveGrade("needs_analysis", "Best-move evidence is incomplete", move_uci)
    evaluated = _compatible_root_report(exercise, move_uci, additional_analysis)
    if evaluated is None:
        return MoveGrade(
            "needs_analysis", "This legal move needs its own engine search", move_uci,
            analysis_request=_needed_request(exercise, move_uci),
        )
    line, _ = evaluated
    try:
        states = _replay_line(board, line)
    except ValueError:
        return MoveGrade("needs_analysis", "Saved line is illegal; retry analysis", move_uci,
                         analysis_request=_needed_request(exercise, move_uci))
    loss_cp = _learner_loss(best.lines[0].score, line.score,
                            exercise.anchor.position.learner_color)
    if loss_cp is None:
        best_mate = _mate_outcome(best.lines[0].score,
                                  exercise.anchor.position.learner_color)
        move_mate = _mate_outcome(line.score, exercise.anchor.position.learner_color)
        if move_mate == -1 and best_mate != -1:
            grade = "incorrect"
        elif best_mate == move_mate and best_mate is not None:
            grade = "ambiguous"
        else:
            grade = "ambiguous"
    elif loss_cp <= exercise.policy.correct_tolerance_cp:
        grade = "correct"
    elif loss_cp >= exercise.policy.incorrect_loss_cp:
        grade = "incorrect"
    else:
        grade = "ambiguous"
    if grade != "incorrect":
        return MoveGrade(grade, "Sound defense" if grade == "correct" else
                         "Engine evidence is too close to grade", move_uci, loss_cp)
    consequence, net_gain = _fork_consequence(states, line, exercise.seed)
    allows_fork = consequence == "proved" and net_gain is not None and net_gain >= 200
    return MoveGrade(
        "incorrect",
        "Move allows the tracked checking knight fork" if allows_fork
        else "Move loses value for another reason",
        move_uci, loss_cp, allows_fork,
    )
