"""Policy isolation and event-union regressions for #169 / #156."""
from dataclasses import replace
import json
from math import fsum
from pathlib import Path

import chess
import pytest

from app.contracts.opening_move_probability import OpeningMoveEvidence, FusedMoveDistribution, move_universe
from app.study.openings.policy_reach import LearnerPolicy, LearnerPolicyAction, PolicyReachContext, PolicyReachRoute, SelectedOpponentEvidence, propagate_policy_reach
from app.study.openings.preparedness import ProbabilityEvidence as P, DecisionReadiness, CardLearningEffect, preparedness, marginal_card_value, rank_card_values
from app.chess.opening_segments import opening_position_key

EXAMPLES = json.loads((Path(__file__).resolve().parents[2] /
                      "tests/fixtures/opening-move-probability/examples.json").read_text())
ITALIAN = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "a7a6", "d2d3")
RUY = (*ITALIAN[:4], "f1b5", "a7a6", "b5a4")
CONTEXT = PolicyReachContext("opponent", chess.STARTING_FEN)


def policy_for(policy_id, routes, trained_color="white", root=chess.STARTING_FEN):
    actions = []
    for moves in routes:
        board = chess.Board(root)
        for move in moves:
            if board.turn == (trained_color == "white"):
                actions.append(LearnerPolicyAction(policy_id, opening_position_key(board.fen()), move))
            board.push_uci(move)
    return LearnerPolicy(policy_id, policy_id, trained_color, tuple(actions))


def selected_evidence(board, assigned, unknown_mass=0, *, evidence_id=None, bounds=()):
    position = move_universe(board.fen())
    evidence_id = evidence_id or opening_position_key(board.fen())
    payload = json.loads(json.dumps(EXAMPLES["maia_dense"]))
    payload["evidence_id"] = evidence_id
    payload["quality"]["uncertainty"] = {"note": "opaque metadata"}
    payload["distribution"] = {"contract_version": 1, "position": position.model_dump(),
        "moves": [{"move_uci": move, "probability": assigned.get(move, None if unknown_mass else 0)}
                  for move in position.legal_moves], "unknown_mass": unknown_mass}
    payload["raw_evidence"] = {"kind": "scores", "basis": "probability", "moves": [
        {"move_uci": move, "value": probability} for move, probability in assigned.items()]}
    return SelectedOpponentEvidence("opponent", evidence_id, OpeningMoveEvidence.model_validate(payload), bounds)


def evidence_for(routes, trained_color="white", root=chess.STARTING_FEN):
    positions = {}
    for moves in routes:
        board = chess.Board(root)
        for move in moves:
            if board.turn != (trained_color == "white"):
                key = opening_position_key(board.fen())
                positions.setdefault(key, (board.copy(), {}))[1][move] = {
                    "e7e5": .42, "b8c6": .50, "a7a6": .25,
                }.get(move, .10)
            board.push_uci(move)
    result = []
    for board, assigned in positions.values():
        # Explicitly assign remaining mass outside represented paths.
        outside = next(move.uci() for move in board.legal_moves if move.uci() not in assigned)
        assigned[outside] = 1 - fsum(assigned.values())
        result.append(selected_evidence(board, assigned))
    return tuple(result)


def evaluate(policy_id, moves, *, all_routes=None):
    # Selection is explicit at the caller boundary; passing foreign routes is an error.
    all_routes = all_routes or {policy_id: (moves,)}
    selected = all_routes[policy_id]
    policy = policy_for(policy_id, selected)
    routes = tuple(PolicyReachRoute(policy_id, str(index), route) for index, route in enumerate(selected))
    return propagate_policy_reach(policy, routes, evidence_for(selected), context=CONTEXT)


def target(result, prefix=ITALIAN[:-1]):
    return next(decision for decision in result.decisions
                if any(path.route.move_prefix == prefix for path in decision.incoming_paths))


def scores(result, decision):
    readiness = (DecisionReadiness(decision.identity, decision.kernel_routes(), P.exact(.2), policy_id=result.policy_id),)
    effect = CardLearningEffect("card", ((decision.identity, P.exact(.8)),), policy_id=result.policy_id)
    return (preparedness(readiness, policy_id=result.policy_id),
            marginal_card_value(readiness, effect, policy_id=result.policy_id),
            rank_card_values(readiness, (effect,), policy_id=result.policy_id))


@pytest.mark.parametrize("policy_id,moves,other_id,other", [("italian", ITALIAN, "ruy", RUY), ("ruy", RUY, "italian", ITALIAN)])
def test_policy_reach_italian_ruy_add_remove_duplicate_isolation(policy_id, moves, other_id, other):
    baseline = evaluate(policy_id, moves)
    for copies in (1, 2, 4):
        expanded = evaluate(policy_id, moves, all_routes={policy_id: (moves,), other_id: (other,) * copies})
        assert expanded == baseline
        assert scores(expanded, target(expanded, moves[:-1])) == scores(baseline, target(baseline, moves[:-1]))


def test_policy_reach_opponent_factors_feed_actual_preparedness_and_marginal_kernel():
    result = evaluate("italian", ITALIAN)
    decision = target(result)
    assert decision.probability.status == "exact"
    assert decision.probability.lower == pytest.approx(.42 * .50 * .25)
    assert [factor[3].lower for factor in decision.incoming_paths[0].opponent_factors] == [.42, .50, .25]
    prepared, marginal, ranking = scores(result, decision)
    assert prepared.value == pytest.approx(.0105)
    assert marginal.value == pytest.approx(.0315)
    assert ranking.ranked == (marginal,)


def test_policy_reach_learner_choices_never_dilute_selected_action():
    baseline = evaluate("italian", ITALIAN)
    expanded = evaluate("italian", ITALIAN, all_routes={"italian": (ITALIAN,), "ruy": (RUY,), "scotch": ((*ITALIAN[:4], "d2d4"),)})
    assert expanded == baseline
    bc4 = next(path for path in baseline.paths if path.route.move_prefix == ITALIAN[:4])
    after_bc4 = next(path for path in baseline.paths if path.route.move_prefix == ITALIAN[:5])
    assert bc4.prescribed_action == "f1c4"
    assert bc4.route.probability == after_bc4.route.probability


def test_policy_reach_opponent_deviation_is_not_conditioned_away():
    deviation = ("e2e4", "c7c5", "g1f3")
    result = evaluate("italian", ITALIAN, all_routes={"italian": (ITALIAN, deviation)})
    decision = target(result, deviation[:-1])
    assert decision.probability.lower == .10
    assert target(result).probability.lower == pytest.approx(.0525)


def test_policy_reach_retains_covered_outside_and_unassigned_mass():
    routes = (("e2e4", "e7e5"), ("e2e4", "c7c5"))
    board = chess.Board(); board.push_uci("e2e4")
    evidence = selected_evidence(board, {"e7e5": .6, "c7c5": .2, "e7e6": .1}, .1)
    result = propagate_policy_reach(policy_for("italian", routes),
        tuple(PolicyReachRoute("italian", str(index), moves) for index, moves in enumerate(routes)),
        (evidence,), context=CONTEXT)
    diagnostic = result.opponent_diagnostics[0]
    assert diagnostic.known_covered_mass == pytest.approx(.8)
    assert diagnostic.known_outside_mass == .1
    assert diagnostic.unassigned_mass == P.exact(.1)
    assert next(path for path in result.paths if path.route.move_prefix == routes[0]).route.probability == P.exact(.6)
    assert any(probability.status != "exact" for _, probability in diagnostic.replies)


def test_policy_reach_bounded_unknown_allocations_multiply_conservatively():
    moves = ITALIAN[:5]
    policy = policy_for("italian", (moves,))
    board = chess.Board(); selections = []
    for offset, move in enumerate(moves):
        if board.turn == chess.BLACK:
            lower, upper = (.3, .5) if offset == 1 else (.4, .6)
            selections.append(selected_evidence(board, {}, 1,
                bounds=((move, P.bounded(lower, upper, "explicit sensitivity")),)))
        board.push_uci(move)
    result = propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", moves),), selections, context=CONTEXT)
    decision = target(result, moves[:-1])
    assert decision.probability.status == "bounded"
    assert decision.probability.lower == pytest.approx(.12)
    assert decision.probability.upper == pytest.approx(.30)
    assert "unknown_reply" in decision.probability.reason
    assert scores(result, decision)[0].value is None


def test_policy_reach_missing_opponent_evidence_stays_incomplete():
    result = propagate_policy_reach(policy_for("italian", (ITALIAN,)),
        (PolicyReachRoute("italian", "line", ITALIAN),), (), context=CONTEXT)
    decision = target(result)
    assert (decision.probability.lower, decision.probability.upper) == (0, 1)
    assert scores(result, decision)[0].value is None
    assert all(diagnostic.reasons == ("missing_opponent_evidence",) for diagnostic in result.opponent_diagnostics)


def test_policy_reach_missing_learner_action_refuses_downstream_kernel_export():
    policy = policy_for("italian", (ITALIAN,))
    # Remove specifically the root action, independent of action sort order.
    missing = opening_position_key(chess.STARTING_FEN)
    policy = replace(policy, actions=tuple(action for action in policy.actions if action.position_key != missing))
    result = propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", ITALIAN),), evidence_for((ITALIAN,)), context=CONTEXT)
    assert result.paths[0].missing_current_action
    assert result.paths[0].route.probability == P.exact(1)
    decision = target(result)
    assert decision.unresolved
    with pytest.raises(ValueError, match="Unscorable"):
        decision.kernel_routes()


def test_policy_reach_transposition_and_duplicate_routes_count_unique_decision_once():
    routes = (("g1f3", "g8f6", "g2g3", "g7g6", "f1g2"),
              ("g1f3", "g7g6", "g2g3", "g8f6", "f1g2"))
    policy = policy_for("reti", routes)
    authored = tuple(PolicyReachRoute("reti", str(index), moves) for index, moves in enumerate(routes))
    evidence = evidence_for(routes)
    result = propagate_policy_reach(policy, authored, evidence, context=CONTEXT)
    decision = target(result, routes[0][:-1])
    assert len(decision.incoming_paths) == 2
    assert decision.probability.lower == pytest.approx(.02)
    duplicated = propagate_policy_reach(policy, (*authored, PolicyReachRoute("reti", "duplicate", routes[0])), evidence, context=CONTEXT)
    assert scores(result, decision) == scores(duplicated, target(duplicated, routes[0][:-1]))
    assert len(duplicated.decisions) == len(result.decisions)


def test_policy_reach_revisit_is_absorbed_at_first_position_and_decision():
    moves = ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3")
    result = evaluate("cycle", moves)
    root = next(position for position in result.positions if position.identity == opening_position_key(chess.STARTING_FEN))
    assert root.probability == P.exact(1)
    assert len(root.incoming_paths) == 1
    decision = target(result, ())
    assert decision.probability == P.exact(1)
    assert len(decision.incoming_paths) == 1
    assert len([path for path in result.paths if path.decision_id == decision.identity]) == 2


def test_policy_reach_foreign_policy_rejected_before_route_validation():
    policy = policy_for("italian", (ITALIAN,))
    with pytest.raises(ValueError, match="Foreign policy route"):
        propagate_policy_reach(policy, (PolicyReachRoute("ruy", "bad", ("not-a-move",)),), (), context=CONTEXT)
    with pytest.raises(ValueError, match="Foreign policy action"):
        replace(policy, actions=(replace(policy.actions[0], policy_id="ruy"),))


def test_policy_reach_permutation_preserves_diagnostics_kernel_scores_and_ranking():
    moves = (ITALIAN, ("e2e4", "c7c5", "g1f3"))
    policy = policy_for("italian", moves)
    routes = tuple(PolicyReachRoute("italian", str(index), route) for index, route in enumerate(moves))
    evidence = evidence_for(moves)
    baseline = propagate_policy_reach(policy, routes, evidence, context=CONTEXT)
    permuted = propagate_policy_reach(replace(policy, actions=tuple(reversed(policy.actions))),
        reversed(routes), reversed(evidence), context=CONTEXT)
    assert permuted == baseline
    assert scores(permuted, target(permuted)) == scores(baseline, target(baseline))


def test_policy_reach_detaches_source_metadata_and_retains_provenance():
    board = chess.Board(); board.push_uci("e2e4")
    selection = selected_evidence(board, {"e7e5": .42, "c7c5": .58})
    evidence = selection.snapshot()
    bound = SelectedOpponentEvidence("opponent", evidence.evidence_id, evidence)
    original = bound.snapshot_json
    evidence.cohort["mutated"] = "caller mutation"
    evidence.quality.uncertainty["mutated"] = True
    assert bound.snapshot_json == original
    assert "mutated" not in bound.snapshot().cohort
    assert bound.snapshot().provenance


@pytest.mark.parametrize("problem", ["exact_override", "exceeds_mass", "infeasible_total", "illegal_bound", "exact_unknown"])
def test_policy_reach_rejects_invalid_sensitivity_evidence(problem):
    board = chess.Board(); board.push_uci("e2e4")
    if problem == "exact_override":
        assigned, mass, bounds = {"e7e5": .9}, .1, (("e7e5", P.bounded(.2, .3, "invalid")),)
    elif problem == "exceeds_mass":
        assigned, mass, bounds = {"e7e5": .9}, .1, (("c7c5", P.bounded(.2, .3, "invalid")),)
    elif problem == "infeasible_total":
        assigned, mass = {}, 1
        bounds = tuple((move.uci(), P.bounded(0, .001, "invalid")) for move in board.legal_moves)
    elif problem == "illegal_bound":
        assigned, mass, bounds = {}, 1, (("a1a8", P.unknown("illegal")),)
    else:
        assigned, mass, bounds = {}, 1, (("e7e5", P.exact(.5)),)
    with pytest.raises(ValueError):
        selected_evidence(board, assigned, mass, bounds=bounds)


def test_policy_reach_black_policy_retains_white_opponent_probability():
    moves = ("e2e4", "e7e5", "g1f3", "b8c6")
    policy = policy_for("black", (moves,), "black")
    result = propagate_policy_reach(policy, (PolicyReachRoute("black", "line", moves),),
        evidence_for((moves,), "black"), context=CONTEXT)
    decision = target(result, moves[:-1])
    assert decision.probability.lower == pytest.approx(.01)


def test_policy_reach_rejects_conflicting_actions_routes_evidence_and_context():
    policy = policy_for("italian", (ITALIAN,))
    root_action = next(action for action in policy.actions if action.position_key == opening_position_key(chess.STARTING_FEN))
    with pytest.raises(ValueError, match="Conflicting"):
        replace(policy, actions=(*policy.actions, replace(root_action, move_uci="d2d4")))
    with pytest.raises(ValueError, match="contradicts"):
        propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", ("d2d4",)),), (), context=CONTEXT)
    with pytest.raises(ValueError, match="Illegal route"):
        propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", ("e2e4", "e7e4")),), (), context=CONTEXT)
    evidence = evidence_for((ITALIAN,))
    changed = evidence[0].snapshot().model_dump(mode="json")
    changed["source_version"] = "other-version"
    conflicting = SelectedOpponentEvidence("opponent", evidence[0].evidence_id, OpeningMoveEvidence.model_validate(changed))
    with pytest.raises(ValueError, match="Conflicting opponent evidence identity"):
        propagate_policy_reach(policy, (), (*evidence, conflicting), context=CONTEXT)
    with pytest.raises(ValueError, match="context mismatch"):
        propagate_policy_reach(policy, (), evidence, context=replace(CONTEXT, context_id="foreign"))


def test_policy_reach_ambiguous_learner_paths_never_export_a_summed_union():
    routes = (("g1f3", "d7d5", "g2g3", "g8f6", "f1g2"),
              ("g2g3", "d7d5", "g1f3", "g8f6", "f1g2"))
    board = chess.Board()
    for move in routes[0][:-1]:
        board.push_uci(move)
    policy = LearnerPolicy("reti", "reti", "white", (
        LearnerPolicyAction("reti", opening_position_key(board.fen()), "f1g2"),))
    result = propagate_policy_reach(policy,
        tuple(PolicyReachRoute("reti", str(index), moves) for index, moves in enumerate(routes)),
        evidence_for(routes), context=CONTEXT)
    decision = target(result, routes[0][:-1])
    assert decision.probability.status == "unknown"
    assert decision.unresolved
    with pytest.raises(ValueError, match="Unscorable"):
        decision.kernel_routes()


@pytest.mark.parametrize("available", [True, False])
def test_policy_reach_accepts_fused_context_and_retains_unavailable_reason(available):
    board = chess.Board(); board.push_uci("e2e4")
    source = selected_evidence(board, {"e7e5": .42, "c7c5": .58}).snapshot()
    payload = dict(contract_version=1, position=source.distribution.position.model_dump(),
        target_context={"profile_version": "selected-v1"}, policy=source.normalization.model_dump(),
        status="available" if available else "unavailable",
        distribution=source.distribution.model_dump() if available else None,
        unavailable_reason=None if available else "source_unavailable",
        contributions=[dict(evidence_id=source.evidence_id, weight=1, effective_sample_size=None)],
        freshness=source.freshness.model_dump(), quality=source.quality.model_dump(),
        provenance=[record.model_dump() for record in source.provenance])
    selection = SelectedOpponentEvidence("opponent", "fused-v1", FusedMoveDistribution.model_validate(payload))
    context = replace(CONTEXT, target_context_json=json.dumps(payload["target_context"]))
    moves = ITALIAN[:3]
    policy = policy_for("italian", (moves,))
    result = propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", moves),), (selection,), context=context)
    decision = target(result, moves[:-1])
    if available:
        assert decision.probability == P.exact(.42)
    else:
        assert decision.probability.lower == 0
        assert decision.probability.upper == 1
        assert result.opponent_diagnostics[0].reasons == ("unavailable_opponent_evidence:source_unavailable",)
    with pytest.raises(ValueError, match="target context mismatch"):
        propagate_policy_reach(policy, (), (selection,), context=CONTEXT)


def test_policy_reach_explicit_custom_root_and_bounded_root_mass():
    board = chess.Board(); board.push_uci("e2e4")
    moves = ("e7e5", "g1f3")
    context = PolicyReachContext("opponent", board.fen(), P.bounded(.4, .6, "scenario sensitivity"))
    policy = policy_for("italian", (moves,), root=board.fen())
    evidence = selected_evidence(board, {"e7e5": .5, "c7c5": .5})
    result = propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", moves),), (evidence,), context=context)
    decision = target(result, moves[:-1])
    assert decision.probability.lower == .2
    assert decision.probability.upper == .3
    assert decision.incoming_paths[0].route.move_prefix == ("e7e5",)


def test_policy_reach_unknown_reply_is_bounded_by_retained_unassigned_mass():
    moves = ("e2e4", "c7c5", "g1f3")
    board = chess.Board(); board.push_uci("e2e4")
    evidence = selected_evidence(board, {"e7e5": .8, "e7e6": .1}, .1)
    result = propagate_policy_reach(policy_for("italian", (moves,)),
        (PolicyReachRoute("italian", "line", moves),), (evidence,), context=CONTEXT)
    decision = target(result, moves[:-1])
    assert (decision.probability.lower, decision.probability.upper) == (0, .1)
    assert decision.probability.status == "bounded"
    assert "unknown_reply" in decision.probability.reason
    assert scores(result, decision)[0].value is None


def test_policy_reach_same_physical_decision_keeps_policy_identity_separate():
    policy = policy_for("italian", (ITALIAN,))
    second = replace(policy, policy_id="experiment", actions=tuple(
        replace(action, policy_id="experiment") for action in policy.actions))
    first_result = propagate_policy_reach(policy, (PolicyReachRoute("italian", "line", ITALIAN),), evidence_for((ITALIAN,)), context=CONTEXT)
    second_result = propagate_policy_reach(second, (PolicyReachRoute("experiment", "line", ITALIAN),), evidence_for((ITALIAN,)), context=CONTEXT)
    first_decision, second_decision = target(first_result), target(second_result)
    assert first_decision.identity == second_decision.identity
    assert first_decision.probability == second_decision.probability
    assert first_decision.kernel_routes()[0].policy_id == "italian"
    assert second_decision.kernel_routes()[0].policy_id == "experiment"
    with pytest.raises(ValueError, match="Policy mismatch"):
        DecisionReadiness(first_decision.identity, second_decision.kernel_routes(), P.exact(.2), policy_id="italian")


def test_policy_reach_terminal_position_does_not_report_a_required_learner_action():
    root = "7k/6Q1/5K2/8/8/8/8/8 b - - 0 1"
    assert chess.Board(root).is_checkmate()
    result = propagate_policy_reach(LearnerPolicy("terminal", "terminal", "black", ()),
        (PolicyReachRoute("terminal", "terminal-line", ()),), (),
        context=PolicyReachContext("opponent", root))
    assert result.paths[0].route.probability == P.exact(1)
    assert not result.paths[0].missing_current_action
    assert result.decisions == ()


def test_policy_reach_result_retains_explicit_selected_opponent_context():
    context = replace(CONTEXT, target_context_json='{"profile_version":"selected-v1"}')
    result = propagate_policy_reach(policy_for("italian", (ITALIAN,)),
        (PolicyReachRoute("italian", "line", ITALIAN),), evidence_for((ITALIAN,)), context=context)
    assert result.target_context_json == context.target_context_json
