"""Issue 11 regressions for compatible analysis and concrete consequences."""

from app.services.threat_detection import (
    find_defensive_knight_forks, propose_exercise_anchors,
)
from app.services.threat_models import GameSnapshot, SourceLine, ThreatPolicy
from app.services.threat_validation import (
    AnalysisLine, AnalysisReport, EngineScore, make_validation_plan,
    validate_threat_anchor,
)
import chess


BASE_FEN = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
POLICY = ThreatPolicy()


def evidence(fen=BASE_FEN, historical_pv=("a2a3", "b4c2", "e1d2", "c2a1", "d2c1")):
    game = GameSnapshot("game", 1, fen, ("a2a3",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, historical_pv[1:])
    )[0]
    anchor = propose_exercise_anchors(game, seed, POLICY)[0]
    plan = make_validation_plan(anchor, engine_version="sf19", network_version="nnue", policy=POLICY)
    best = AnalysisReport(plan.best_request, (
        AnalysisLine("e1f2", ("e1f2", "e8e7"), EngineScore(cp=0), 14),
    ), True)
    historical = AnalysisReport(plan.historical_request, (
        AnalysisLine("a2a3", historical_pv, EngineScore(cp=-180), 14),
    ), True)
    return anchor, seed, plan, best, historical


def test_issue11_historical_move_allows_concrete_checking_knight_fork():
    anchor, seed, plan, best, historical = evidence()
    result = validate_threat_anchor(anchor, seed, plan, best, historical, POLICY)
    assert result.state == "engine_supported"
    assert result.loss_cp == 180
    assert result.net_material_cp == 500


def test_issue11_capturable_knight_geometry_is_not_approved():
    fen = "4k3/8/8/8/1n2B3/8/P7/R3K3 w Q - 0 1"
    anchor, seed, plan, best, historical = evidence(fen)
    assert validate_threat_anchor(anchor, seed, plan, best, historical, POLICY).state == "rejected"


def test_issue11_rook_capture_and_knight_recapture_counts_net_exchange():
    fen = "4k3/8/8/8/1n6/8/PB6/R3K3 w Q - 0 1"
    pv = ("a2a3", "b4c2", "e1d2", "c2a1", "b2a1")
    anchor, seed, plan, best, historical = evidence(fen, pv)
    result = validate_threat_anchor(anchor, seed, plan, best, historical, POLICY)
    assert result.state == "engine_supported"
    assert result.net_material_cp == 200


def test_issue11_sound_earlier_move_is_lesson_only():
    anchor, seed, plan, best, historical = evidence()
    historical = AnalysisReport(plan.historical_request, (
        AnalysisLine("a2a3", historical.lines[0].pv_uci, EngineScore(cp=-20), 14),
    ), True)
    assert validate_threat_anchor(anchor, seed, plan, best, historical, POLICY).state == "lesson_only"


def test_issue11_unrelated_refutation_does_not_claim_target_fork():
    anchor, seed, plan, best, _ = evidence()
    unrelated = AnalysisReport(plan.historical_request, (
        AnalysisLine("a2a3", ("a2a3", "e8e7"), EngineScore(cp=-180), 14),
    ), True)
    assert validate_threat_anchor(anchor, seed, plan, best, unrelated, POLICY).state == "lesson_only"


def test_issue11_missing_partial_and_incompatible_reports_cannot_approve():
    anchor, seed, plan, best, historical = evidence()
    assert validate_threat_anchor(anchor, seed, plan, best, None, POLICY).state == "needs_analysis"
    partial = AnalysisReport(plan.historical_request, historical.lines, False)
    assert validate_threat_anchor(anchor, seed, plan, best, partial, POLICY).state == "inconclusive"
    different = AnalysisReport(plan.best_request, historical.lines, True)
    assert validate_threat_anchor(anchor, seed, plan, best, different, POLICY).state == "needs_analysis"


def test_issue11_illegal_pv_and_truncated_consequence_cannot_approve():
    anchor, seed, plan, best, historical = evidence()
    illegal = AnalysisReport(plan.historical_request, (
        AnalysisLine("a2a3", ("a2a3", "b4b5"), EngineScore(cp=-180), 14),
    ), True)
    assert validate_threat_anchor(anchor, seed, plan, best, illegal, POLICY).state == "rejected"
    truncated = AnalysisReport(plan.historical_request, (
        AnalysisLine("a2a3", historical.lines[0].pv_uci[:-1], EngineScore(cp=-180), 14),
    ), True)
    assert validate_threat_anchor(anchor, seed, plan, best, truncated, POLICY).state == "inconclusive"


def test_issue11_compatible_validation_replay_is_deterministic():
    anchor, seed, plan, best, historical = evidence()
    first = validate_threat_anchor(anchor, seed, plan, best, historical, POLICY)
    assert first == validate_threat_anchor(anchor, seed, plan, best, historical, POLICY)
    assert first.report_ids == (best.report_id, historical.report_id)


def test_issue11_black_learner_scores_are_normalized_from_white_perspective():
    mirrored_fen = chess.Board(BASE_FEN).mirror().fen()
    game = GameSnapshot("black-game", 1, mirrored_fen, ("a7a6",), "black")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("b5c7", "e8d7", "c7a8", "d7c8"))
    )[0]
    anchor = propose_exercise_anchors(game, seed, POLICY)[0]
    plan = make_validation_plan(anchor, engine_version="sf19", network_version="nnue", policy=POLICY)
    best = AnalysisReport(plan.best_request, (
        AnalysisLine("e8f7", ("e8f7", "e1e2"), EngineScore(cp=0), 14),
    ), True)
    historical = AnalysisReport(plan.historical_request, (
        AnalysisLine("a7a6", ("a7a6", "b5c7", "e8d7", "c7a8", "d7c8"),
                     EngineScore(cp=180), 14),
    ), True)
    result = validate_threat_anchor(anchor, seed, plan, best, historical, POLICY)
    assert result.state == "engine_supported"
    assert result.loss_cp == 180


def test_issue11_request_identity_includes_history_engine_limits_and_root_move():
    anchor, _, plan, _, _ = evidence()
    other = make_validation_plan(anchor, engine_version="sf20", network_version="nnue", policy=POLICY)
    assert plan.best_request.request_id != plan.historical_request.request_id
    assert plan.best_request.request_id != other.best_request.request_id
    assert plan.best_request.request_id != type(plan.best_request)(
        **{**plan.best_request.__dict__, "depth": 16}
    ).request_id
