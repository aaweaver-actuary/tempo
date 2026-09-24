"""Issue 13 move-rubric regressions; scheduling is tested at the API boundary."""

from app.services.threat_detection import find_defensive_knight_forks, propose_exercise_anchors
from app.services.threat_grading import DefenseExercise, grade_defense_move
from app.services.threat_models import GameSnapshot, SourceLine, ThreatPolicy
from app.services.threat_validation import (
    AnalysisLine, AnalysisReport, EngineScore, make_validation_plan,
)


FEN = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
POLICY = ThreatPolicy()


def exercise():
    game = GameSnapshot("game", 1, FEN, ("a2a3",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("b4c2", "e1d2", "c2a1", "d2c1"))
    )[0]
    anchor = propose_exercise_anchors(game, seed, POLICY)[0]
    plan = make_validation_plan(anchor, engine_version="sf19", network_version="nnue", policy=POLICY)
    best = AnalysisReport(plan.best_request, (
        AnalysisLine("e1f2", ("e1f2", "e8e7"), EngineScore(cp=0), 14),
        AnalysisLine("e1d2", ("e1d2", "e8e7"), EngineScore(cp=-20), 14),
    ), True)
    historical = AnalysisReport(plan.historical_request, (
        AnalysisLine("a2a3", ("a2a3", "b4c2", "e1d2", "c2a1", "d2c1"),
                     EngineScore(cp=-180), 14),
    ), True)
    return DefenseExercise("candidate", "finding", 1, anchor, seed, POLICY,
                           best, (historical,), historical.lines[0].pv_uci)


def test_issue13_best_and_different_sound_moves_are_correct():
    item = exercise()
    assert grade_defense_move(item, "e1f2").status == "correct"
    assert grade_defense_move(item, "e1d2").status == "correct"


def test_issue13_legal_move_outside_multipv_needs_root_analysis():
    grade = grade_defense_move(exercise(), "a2a4")
    assert grade.status == "needs_analysis"
    assert grade.analysis_request is not None
    assert grade.analysis_request.root_move_uci == "a2a4"


def test_issue13_losing_move_with_verified_fork_has_specific_feedback():
    grade = grade_defense_move(exercise(), "a2a3")
    assert grade.status == "incorrect"
    assert grade.allows_target_fork


def test_issue13_bad_move_for_unrelated_reason_has_no_false_fork_feedback():
    item = exercise()
    request = item.best_report.request
    unrelated = AnalysisReport(
        type(request)(**{**request.__dict__, "multipv": 1, "root_move_uci": "a2a4"}),
        (AnalysisLine("a2a4", ("a2a4", "e8e7"), EngineScore(cp=-200), 14),), True,
    )
    grade = grade_defense_move(item, "a2a4", additional_analysis=unrelated)
    assert grade.status == "incorrect"
    assert not grade.allows_target_fork


def test_issue13_illegal_and_ambiguous_moves_do_not_get_definitive_grade():
    item = exercise()
    assert grade_defense_move(item, "a2a5").status == "illegal"
    request = item.best_report.request
    uncertain = AnalysisReport(
        type(request)(**{**request.__dict__, "multipv": 1, "root_move_uci": "a2a4"}),
        (AnalysisLine("a2a4", ("a2a4", "e8e7"), EngineScore(cp=-60), 14),), True,
    )
    assert grade_defense_move(item, "a2a4", additional_analysis=uncertain).status == "ambiguous"
