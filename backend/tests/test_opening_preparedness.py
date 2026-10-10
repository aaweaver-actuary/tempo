"""Probability-priority contract regressions: pure fixtures, no service writes."""
from dataclasses import FrozenInstanceError, asdict
from itertools import permutations, product
import json
from pathlib import Path
import subprocess
import sys

import chess
import pytest

from app.services.opening_preparedness import (
    CardLearningEffect, DecisionReadiness, OpponentReply, OpponentReplyDistribution,
    ProbabilityEvidence, RouteReach, decision_reach, marginal_card_value,
    preparedness, rank_card_values, route_probability,
)
from app.services.opening_segmentation import opening_position_key, presentation_occurrences


def exact(value):
    return ProbabilityEvidence.exact(value)


def route(probability, prefix=(), context="rapid", root="root", *, policy_id="fixture-policy"):
    return RouteReach(context, root, prefix, probability, policy_id=policy_id)


def decision(identifier, reach=0.6, recall=0.2, *, policy_id="fixture-policy"):
    return DecisionReadiness(identifier, (route(exact(reach), (identifier,), policy_id=policy_id),), exact(recall), policy_id=policy_id)


def effect(card_id, identifier, projected=0.8, *, policy_id="fixture-policy"):
    return CardLearningEffect(card_id, ((identifier, exact(projected)),), policy_id=policy_id)


def presentation(moves, color="white", start=chess.STARTING_FEN):
    return {"id": "fixture", "revision": 1, "trained_color": color,
            "start_fen": start, "moves_json": json.dumps(moves)}


def test_probability_priority_hand_calculated_route_preparedness_and_marginal_gain():
    reach = route_probability(exact(0.5), (exact(0.6), exact(0.4)))
    assert reach.lower == pytest.approx(0.12)
    readiness = (DecisionReadiness("child", (route(reach, ("e2e4", "c7c5")),), exact(0.25), policy_id="fixture-policy"),)
    before = preparedness(readiness, policy_id="fixture-policy")
    gain = marginal_card_value(readiness, effect("card", "child", 0.75), policy_id="fixture-policy")
    assert before.value == pytest.approx(0.03)
    assert gain.value == pytest.approx(0.06)
    after = preparedness((DecisionReadiness("child", readiness[0].routes, exact(0.75), policy_id="fixture-policy"),), policy_id="fixture-policy")
    assert gain.value == pytest.approx(after.value - before.value)
    assert gain.contributions[0].projected_recall == exact(0.75)


def test_probability_priority_frequent_deeper_decision_outranks_rare_shallow_decision():
    readiness = (decision("deep", 0.6), decision("shallow", 0.05), decision("ready", 0.6, 0.75))
    ranking = rank_card_values(readiness, (effect("rare", "shallow"), effect("frequent", "deep"), effect("ready", "ready")), policy_id="fixture-policy")
    assert ranking.ranked[0].card_id == "frequent"
    assert {result.card_id for result in ranking.ranked[1:]} == {"rare", "ready"}
    assert [result.value for result in ranking.ranked] == pytest.approx([0.36, 0.03, 0.03])
    assert not ranking.incomplete


@pytest.mark.parametrize("lower_reach,higher_reach,current,projected", tuple(product((0, 0.2), (0.6, 1), (0, 0.4), (0.7, 1))))
def test_probability_priority_increasing_reach_cannot_reduce_marginal_value(lower_reach, higher_reach, current, projected):
    lower_value = marginal_card_value((decision("d", lower_reach, current),), effect("c", "d", projected), policy_id="fixture-policy")
    higher_value = marginal_card_value((decision("d", higher_reach, current),), effect("c", "d", projected), policy_id="fixture-policy")
    assert 0 <= lower_value.value <= higher_value.value <= higher_reach * (1 - current)


@pytest.mark.parametrize("reach,lower_current,higher_current,target", tuple(product((0, 0.3, 1), (0, 0.1), (0.5, 0.7), (0.8, 1))))
def test_probability_priority_increasing_readiness_cannot_increase_remaining_benefit(reach, lower_current, higher_current, target):
    lower_value = marginal_card_value((decision("d", reach, lower_current),), effect("c", "d", target), policy_id="fixture-policy")
    higher_value = marginal_card_value((decision("d", reach, higher_current),), effect("c", "d", target), policy_id="fixture-policy")
    assert 0 <= higher_value.value <= lower_value.value <= reach * (1 - lower_current)


def test_probability_priority_zero_reach_and_unchanged_readiness_have_zero_gain():
    assert marginal_card_value((decision("d", 0),), effect("c", "d"), policy_id="fixture-policy").value == 0
    assert marginal_card_value((decision("d", 1, 0.8),), effect("c", "d"), policy_id="fixture-policy").value == 0
    assert preparedness((), policy_id="fixture-policy").value == 0
    assert marginal_card_value((), CardLearningEffect("empty", (), policy_id="fixture-policy"), policy_id="fixture-policy").value == 0


@pytest.mark.parametrize("invalid", (-0.1, 1.1, float("nan"), float("inf"), -float("inf"), True, "0.5"))
def test_probability_priority_rejects_invalid_probability_values(invalid):
    with pytest.raises(ValueError):
        exact(invalid)


@pytest.mark.parametrize("arguments", ((0.8, 0.2, "bounded", "bad"), (0.1, 0.2, "exact", ""),
                                       (0, 0.5, "unknown", "missing"), (0, 1, "bounded", ""),
                                       (0, 1, "unexpected", "missing")))
def test_probability_priority_rejects_invalid_probability_evidence(arguments):
    with pytest.raises(ValueError):
        ProbabilityEvidence(*arguments)


def test_probability_priority_retains_authored_outside_and_unassigned_mass_without_normalization():
    distribution = OpponentReplyDistribution((
        OpponentReply("e7e5", exact(0.6), True), OpponentReply("c7c5", exact(0.2), True),
        OpponentReply("e7e6", exact(0.1), False),
    ), exact(0.1))
    assert distribution.authored_mass.lower == pytest.approx(0.8)
    assert distribution.outside_mass.lower == 0.1
    assert distribution.unassigned_mass.lower == 0.1
    common_reply = next(reply for reply in distribution.replies if reply.move_uci == "e7e5")
    assert route_probability(exact(1), (common_reply.probability,)).lower == 0.6
    # This tests supplied absolute mass, not probabilities renormalized to .75/.25.
    assert sorted(reply.probability.lower for reply in distribution.replies if reply.in_repertoire) == [0.2, 0.6]
    unknown = OpponentReplyDistribution((), exact(1))
    assert unknown.authored_mass.lower == unknown.outside_mass.lower == 0
    assert unknown.unassigned_mass.lower == 1


@pytest.mark.parametrize("reply_mass,unassigned", ((0.9, 0.2), (0.7, 0.1)))
def test_probability_priority_rejects_impossible_distribution_mass(reply_mass, unassigned):
    with pytest.raises(ValueError, match="total mass one"):
        OpponentReplyDistribution((OpponentReply("e7e5", exact(reply_mass), True),), exact(unassigned))


def test_probability_priority_unknown_reply_evidence_is_not_a_genuine_zero():
    unknown = ProbabilityEvidence.unknown("No opponent source evidence")
    distribution = OpponentReplyDistribution((OpponentReply("e7e5", unknown, True),), exact(0))
    assert distribution.authored_mass.status == "bounded"
    assert (distribution.authored_mass.lower, distribution.authored_mass.upper) == (0, 1)
    reach = route_probability(exact(0.6), (unknown,))
    assert (reach.lower, reach.upper) == (0, 0.6)
    assert reach.status == "bounded"
    assert "No opponent source" in reach.reason


def test_probability_priority_duplicate_routes_and_shared_trunks_are_counted_once():
    trunk = DecisionReadiness("trunk", (route(exact(1)), route(exact(1))), exact(0.2), policy_id="fixture-policy")
    child = decision("child", 0.6, 0.4)
    readiness = (trunk, child, trunk)
    card = CardLearningEffect("prefix", (("trunk", exact(0.8)), ("child", exact(0.9)), ("trunk", exact(0.8))), policy_id="fixture-policy")
    assert preparedness(readiness, policy_id="fixture-policy").value == pytest.approx(0.44)
    assert marginal_card_value(readiness, card, policy_id="fixture-policy").value == pytest.approx(0.9)
    assert len(marginal_card_value(readiness, card, policy_id="fixture-policy").contributions) == 2
    assert len(rank_card_values(readiness, (card, card), policy_id="fixture-policy").ranked) == 1


def test_probability_priority_distinct_transposed_opponent_paths_share_one_decision():
    moves_a = ["e2e4", "g8f6", "g1f3", "d7d6", "d2d4"]
    moves_b = ["e2e4", "d7d6", "g1f3", "g8f6", "d2d4"]
    occurrence_a = presentation_occurrences("rep", presentation(moves_a))[-1]
    occurrence_b = presentation_occurrences("rep", presentation(moves_b))[-1]
    assert occurrence_a["decision_id"] == occurrence_b["decision_id"]
    incoming_a = route(exact(0.2), tuple(moves_a[:occurrence_a["move_offset"]]))
    incoming_b = route(exact(0.3), tuple(moves_b[:occurrence_b["move_offset"]]))
    shared = DecisionReadiness(occurrence_a["decision_id"], (incoming_a, incoming_b, incoming_a), exact(0.2), policy_id="fixture-policy")
    assert decision_reach(shared.routes, policy_id="fixture-policy").lower == 0.5
    assert len(preparedness((shared,), policy_id="fixture-policy").contributions) == 1
    assert marginal_card_value((shared,), effect("card", shared.decision_id), policy_id="fixture-policy").value == pytest.approx(0.3)


def test_probability_priority_overlapping_revisits_do_not_double_count_decision_reach():
    moves = ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3"]
    occurrences = presentation_occurrences("rep", presentation(moves))
    assert occurrences[0]["decision_id"] == occurrences[-1]["decision_id"]
    earlier, revisit = route(exact(0.4)), route(exact(0.2), tuple(moves[:4]))
    assert decision_reach((revisit, earlier), policy_id="fixture-policy").lower == 0.4
    with pytest.raises(ValueError, match="ancestor"):
        decision_reach((earlier, route(exact(0.5), tuple(moves[:4]))), policy_id="fixture-policy")


def test_probability_priority_disjoint_context_weights_and_union_bounds_remain_bounded():
    assert decision_reach((route(exact(0.3)), route(exact(0.5), context="blitz")), policy_id="fixture-policy").lower == 0.8
    unknown = ProbabilityEvidence.unknown("Unknown context-weighted reach")
    result = decision_reach((route(unknown), route(unknown, context="blitz")), policy_id="fixture-policy")
    assert (result.lower, result.upper) == (0, 1)
    assert result.status == "bounded"
    with pytest.raises(ValueError, match="exceeds one"):
        decision_reach((route(exact(0.6)), route(exact(0.5), context="blitz")), policy_id="fixture-policy")


def test_probability_priority_conflicting_duplicates_and_invalid_references_fail():
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        decision_reach((route(exact(0.5)), route(exact(0.6))), policy_id="fixture-policy")
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        preparedness((decision("d"), decision("d", recall=0.7)), policy_id="fixture-policy")
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        CardLearningEffect("c", (("d", exact(0.5)), ("d", exact(0.6))), policy_id="fixture-policy")
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        OpponentReplyDistribution((OpponentReply("e7e5", exact(0.5), True), OpponentReply("e7e5", exact(0.6), True)), exact(0.5))
    with pytest.raises(ValueError, match="absent"):
        marginal_card_value((decision("d"),), effect("c", "missing"), policy_id="fixture-policy")
    with pytest.raises(ValueError, match="decrease"):
        marginal_card_value((decision("d", recall=0.9),), effect("c", "d", 0.8), policy_id="fixture-policy")
    with pytest.raises(ValueError, match="multiple learner responses"):
        preparedness((DecisionReadiness("d", (route(exact(0.5)),), exact(0.3), policy_id="fixture-policy"),
                      DecisionReadiness("alternate", (route(exact(0.5)),), exact(0.3), policy_id="fixture-policy")), policy_id="fixture-policy")
    with pytest.raises(ValueError, match="reach evidence"):
        DecisionReadiness("d", (), exact(0.2), policy_id="fixture-policy")


def test_probability_priority_interval_bounds_contain_all_feasible_non_regressive_changes():
    reach = ProbabilityEvidence.bounded(0.2, 0.4, "Coarse source cohort")
    current = ProbabilityEvidence.bounded(0.2, 0.5, "Sparse reviews")
    projected = ProbabilityEvidence.bounded(0.3, 0.9, "Uncertain study effect")
    readiness = (DecisionReadiness("d", (route(reach),), current, policy_id="fixture-policy"),)
    result = marginal_card_value(readiness, CardLearningEffect("c", (("d", projected),), policy_id="fixture-policy"), policy_id="fixture-policy")
    assert result.value is None
    assert (result.lower, result.upper) == pytest.approx((0, 0.28))
    for actual_reach, actual_current, actual_projected in product((0.2, 0.3, 0.4), (0.2, 0.3, 0.5), (0.3, 0.5, 0.9)):
        if actual_projected >= actual_current:
            actual_gain = actual_reach * (actual_projected - actual_current)
            assert result.lower <= actual_gain <= result.upper
    impossible = ProbabilityEvidence.bounded(0, 0.1, "Low projected recall")
    with pytest.raises(ValueError, match="decrease"):
        marginal_card_value(readiness, CardLearningEffect("c", (("d", impossible),), policy_id="fixture-policy"), policy_id="fixture-policy")


def test_probability_priority_unknown_readiness_has_no_fabricated_score_even_at_zero_reach():
    unknown = ProbabilityEvidence.unknown("Not enough review evidence")
    readiness = (DecisionReadiness("d", (route(exact(0)),), unknown, policy_id="fixture-policy"),)
    result = marginal_card_value(readiness, CardLearningEffect("c", (("d", unknown),), policy_id="fixture-policy"), policy_id="fixture-policy")
    assert (result.lower, result.upper) == (0, 0)
    assert result.value is None
    assert len(result.diagnostics) == 2
    ranking = rank_card_values(readiness, (CardLearningEffect("c", (("d", unknown),), policy_id="fixture-policy"),), policy_id="fixture-policy")
    assert not ranking.ranked and ranking.incomplete == (result,)
    nonzero = DecisionReadiness("d", (route(exact(0.6)),), unknown, policy_id="fixture-policy")
    assert marginal_card_value((nonzero,), effect("c", "d"), policy_id="fixture-policy").upper == pytest.approx(0.48)
    assert preparedness((nonzero,), policy_id="fixture-policy").upper == 0.6


def test_probability_priority_totals_can_exceed_one_but_each_probability_is_bounded():
    readiness = (DecisionReadiness("a", (route(exact(1)),), exact(1), policy_id="fixture-policy"),
                 DecisionReadiness("b", (route(exact(1), ("e2e4", "e7e5")),), exact(1), policy_id="fixture-policy"))
    assert preparedness(readiness, policy_id="fixture-policy").value == 2
    assert all(0 <= item.reach.lower <= item.reach.upper <= 1 for item in preparedness(readiness, policy_id="fixture-policy").contributions)


def test_probability_priority_input_permutations_and_ties_are_deterministic():
    readiness = (decision("a", 1, 0), decision("b", 1, 0),
                 DecisionReadiness("unknown", (route(exact(1), ("unknown",)),), ProbabilityEvidence.unknown("missing"), policy_id="fixture-policy"))
    cards = (effect("card-b", "a", 0.5), effect("card-a", "b", 0.5), effect("z-missing", "unknown"), effect("a-missing", "unknown"))
    expected = rank_card_values(readiness, cards, policy_id="fixture-policy")
    for decision_order in permutations(readiness):
        for card_order in permutations(cards):
            assert rank_card_values(decision_order, card_order, policy_id="fixture-policy") == expected
    assert [value.card_id for value in expected.ranked] == ["card-a", "card-b"]
    assert [value.card_id for value in expected.incomplete] == ["a-missing", "z-missing"]
    routes = (route(exact(0.1), ("a",)), route(exact(0.2), ("b",)), route(exact(0.3), ("c",)))
    for route_order in permutations(routes):
        assert decision_reach(route_order, policy_id="fixture-policy") == decision_reach(routes, policy_id="fixture-policy")


@pytest.mark.parametrize("color,moves", (("white", ["e2e4"]), ("black", ["e2e4", "e7e5"])))
def test_probability_priority_existing_identity_maps_short_prefixes_and_trained_colors(color, moves):
    occurrence = presentation_occurrences("rep", presentation(moves, color))[-1]
    root = opening_position_key(chess.STARTING_FEN)
    readiness = DecisionReadiness(occurrence["decision_id"], (route(exact(0.5), tuple(moves[:occurrence["move_offset"]]), root=root),), exact(0.2), policy_id="fixture-policy")
    assert marginal_card_value((readiness,), effect("short", occurrence["decision_id"]), policy_id="fixture-policy").value == pytest.approx(0.3)
    interior = chess.Board(); interior.push_uci("e2e4")
    custom = presentation_occurrences("rep", presentation(["e7e5", "g1f3"], start=interior.fen()))[-1]
    assert custom["move_offset"] == 1
    alternate = presentation_occurrences("rep", presentation(["d2d4"]))[-1]
    assert alternate["decision_id"] != occurrence["decision_id"]


def test_probability_priority_inputs_and_results_are_immutable():
    prefixes, incoming = ["e2e4"], []
    incoming.append(route(exact(0.6), prefixes))
    readiness = DecisionReadiness("d", incoming, exact(0.2), policy_id="fixture-policy")
    projections = [["d", exact(0.8)]]
    card = CardLearningEffect("c", projections, policy_id="fixture-policy")
    before = asdict(readiness), asdict(card)
    prefixes.append("e7e5")
    incoming.clear()
    projections.clear()
    result = marginal_card_value((readiness,), card, policy_id="fixture-policy")
    assert (asdict(readiness), asdict(card)) == before
    assert result.value == pytest.approx(0.36)
    with pytest.raises(FrozenInstanceError):
        result.lower = 0


def test_probability_priority_fresh_import_and_scoring_need_no_database_network_chess_or_fsrs():
    backend = str(Path(__file__).resolve().parents[1])
    script = """
import importlib.abc, sys
class DenyRuntimeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.database', 'app.main', 'app.postgres_store'} or fullname.split('.')[0] in {'chess', 'fsrs', 'httpx', 'psycopg', 'redis', 'celery'}:
            raise AssertionError('Unexpected runtime import: ' + fullname)
sys.meta_path.insert(0, DenyRuntimeImports())
sys.path.insert(0, sys.argv[1])
from app.services.opening_preparedness import *
state = DecisionReadiness('d', (RouteReach('context', 'root', (), ProbabilityEvidence.exact(.5), policy_id='fixture-policy'),), ProbabilityEvidence.exact(.2), policy_id='fixture-policy')
result = marginal_card_value((state,), CardLearningEffect('c', (('d', ProbabilityEvidence.exact(.8)),), policy_id='fixture-policy'), policy_id='fixture-policy')
assert abs(result.value - .3) < 1e-12
"""
    completed = subprocess.run([sys.executable, "-I", "-c", script, backend], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("policy_id", ("", " ", None, 17))
@pytest.mark.parametrize("input_type", ("route", "readiness", "effect"))
def test_probability_priority_policy_identity_is_required_and_nonempty(input_type, policy_id):
    constructors = {
        "route": lambda **identity: RouteReach("rapid", "root", (), exact(0.5), **identity),
        "readiness": lambda **identity: DecisionReadiness("d", (route(exact(0.5)),), exact(0.2), **identity),
        "effect": lambda **identity: CardLearningEffect("c", (), **identity),
    }
    with pytest.raises(TypeError, match="policy_id"):
        constructors[input_type]()
    with pytest.raises(ValueError, match="nonempty"):
        constructors[input_type](policy_id=policy_id)


@pytest.mark.parametrize("operation", ("reach", "preparedness", "marginal", "ranking"))
def test_probability_priority_evaluation_requires_explicit_selected_policy(operation):
    selected = decision("d")
    card = effect("c", "d")
    operations = {
        "reach": lambda **identity: decision_reach(selected.routes, **identity),
        "preparedness": lambda **identity: preparedness((selected,), **identity),
        "marginal": lambda **identity: marginal_card_value((selected,), card, **identity),
        "ranking": lambda **identity: rank_card_values((selected,), (card,), **identity),
    }
    with pytest.raises(TypeError, match="policy_id"):
        operations[operation]()
    with pytest.raises(ValueError, match="nonempty"):
        operations[operation](policy_id=" ")


@pytest.mark.parametrize("operation", ("reach", "preparedness", "marginal", "ranking"))
def test_probability_priority_one_policy_generators_preserve_scores_and_provenance(operation):
    selected = decision("d", policy_id="hypothetical-policy")
    card = effect("c", "d", policy_id=selected.policy_id)
    operations = {
        "reach": lambda: decision_reach(iter(selected.routes), policy_id=selected.policy_id),
        "preparedness": lambda: preparedness(iter((selected,)), policy_id=selected.policy_id),
        "marginal": lambda: marginal_card_value(iter((selected,)), card, policy_id=selected.policy_id),
        "ranking": lambda: rank_card_values(iter((selected,)), iter((card,)), policy_id=selected.policy_id),
    }
    result = operations[operation]()
    if operation == "reach":
        assert result == exact(0.6)
    else:
        assert result.policy_id == selected.policy_id
        if operation == "preparedness":
            assert result.value == 0.6 * 0.2
        elif operation == "marginal":
            assert result.value == 0.6 * (0.8 - 0.2)
        else:
            assert result.ranked[0].policy_id == selected.policy_id
            assert result.ranked[0].value == 0.6 * (0.8 - 0.2)


@pytest.mark.parametrize("operation", ("preparedness", "marginal", "ranking"))
@pytest.mark.parametrize("same_decision_id", (False, True))
def test_probability_priority_mixed_policy_readiness_is_rejected(operation, same_decision_id):
    shared_prefix = ("e2e4", "e7e5", "g1f3", "b8c6")
    italian = DecisionReadiness("shared" if same_decision_id else "italian",
                               (route(exact(0.42), shared_prefix + ("f1c4",), policy_id="italian"),),
                               exact(0.5), policy_id="italian")
    ruy = DecisionReadiness("shared" if same_decision_id else "ruy",
                           (route(exact(0.42), shared_prefix + ("f1b5",), policy_id="ruy"),),
                           exact(0.5), policy_id="ruy")
    mixed = iter((italian, ruy))
    card = effect("c", italian.decision_id, policy_id="italian")
    operations = {
        "preparedness": lambda: preparedness(mixed, policy_id="italian"),
        "marginal": lambda: marginal_card_value(mixed, card, policy_id="italian"),
        "ranking": lambda: rank_card_values(mixed, (), policy_id="italian"),
    }
    with pytest.raises(ValueError, match="Policy mismatch"):
        operations[operation]()


@pytest.mark.parametrize("same_prefix", (False, True))
def test_probability_priority_mixed_policy_routes_are_rejected_before_deduplication(same_prefix):
    italian = route(exact(0.3), ("e2e4",), policy_id="italian")
    ruy = route(exact(0.4), ("e2e4",) if same_prefix else ("d2d4",), policy_id="ruy")
    assert italian.identity[:3] == ("italian", "rapid", "root")
    assert italian.identity != ruy.identity
    with pytest.raises(ValueError, match="Policy mismatch"):
        decision_reach(iter((italian, ruy)), policy_id="italian")
    with pytest.raises(ValueError, match="Policy mismatch"):
        DecisionReadiness("d", iter((italian, ruy)), exact(0.2), policy_id="italian")


@pytest.mark.parametrize("operation", ("marginal", "ranking"))
@pytest.mark.parametrize("empty_projection", (False, True))
def test_probability_priority_foreign_policy_effects_are_rejected_before_projection_lookup(operation, empty_projection):
    selected = decision("d", policy_id="italian")
    foreign = CardLearningEffect("c", () if empty_projection else (("missing", exact(0.8)),), policy_id="ruy")
    operations = {
        "marginal": lambda: marginal_card_value((selected,), foreign, policy_id="italian"),
        "ranking": lambda: rank_card_values((selected,), iter((effect("c", "d", policy_id="italian"), foreign)), policy_id="italian"),
    }
    with pytest.raises(ValueError, match="Policy mismatch"):
        operations[operation]()


def test_probability_priority_empty_evaluations_still_require_and_retain_one_policy():
    empty_effect = CardLearningEffect("c", (), policy_id="italian")
    assert decision_reach((), policy_id="italian") == exact(0)
    assert preparedness((), policy_id="italian").value == 0
    assert preparedness((), policy_id="italian").policy_id == "italian"
    assert marginal_card_value((), empty_effect, policy_id="italian").value == 0
    assert rank_card_values((), (), policy_id="italian").policy_id == "italian"
    with pytest.raises(ValueError, match="Policy mismatch"):
        marginal_card_value((), empty_effect, policy_id="ruy")
    with pytest.raises(ValueError, match="Policy mismatch"):
        rank_card_values((), (empty_effect,), policy_id="ruy")
    with pytest.raises(ValueError, match="nonempty"):
        decision_reach((), policy_id="")
    with pytest.raises(ValueError, match="nonempty"):
        preparedness((), policy_id="")
    with pytest.raises(ValueError, match="nonempty"):
        rank_card_values((), (), policy_id="")


def policy_fixture(policy_id, defining_move):
    """Two policies share real canonical positions and stable knowledge IDs."""
    shared_prefix = ("e2e4", "e7e5", "g1f3", "b8c6")
    root = opening_position_key(chess.STARTING_FEN)
    prefixes = ((), shared_prefix[:2], shared_prefix, shared_prefix + (defining_move, "a7a6"))
    reaches = (exact(1), route_probability(exact(1), (exact(0.42),)),
               route_probability(exact(1), (exact(0.42), exact(0.5))),
               route_probability(exact(1), (exact(0.42), exact(0.5), exact(0.25))))
    readiness = []
    cards = []
    for index, (prefix, reach) in enumerate(zip(prefixes, reaches)):
        position = chess.Board()
        for move in prefix:
            position.push_uci(move)
        expected_move = ("e2e4", "g1f3", defining_move, "d2d3")[index]
        decision_id = opening_position_key(position.fen()) + ":" + expected_move
        readiness.append(DecisionReadiness(decision_id,
                         (route(reach, prefix, root=root, policy_id=policy_id),),
                         exact(0.2), policy_id=policy_id))
        cards.append(effect("card-" + str(index), decision_id, policy_id=policy_id))
    return tuple(readiness), tuple(cards)


def policy_evaluation(catalog, policy_id):
    readiness, cards = catalog[policy_id]
    return (
        tuple(incoming.probability for selected in readiness for incoming in selected.routes),
        tuple(decision_reach(selected.routes, policy_id=policy_id) for selected in readiness),
        preparedness(readiness, policy_id=policy_id),
        tuple(marginal_card_value(readiness, card, policy_id=policy_id) for card in cards),
        rank_card_values(readiness, cards, policy_id=policy_id),
    )


def assert_other_policy_cannot_change_selected_policy(selected_policy, selected_move, other_policy, other_move):
    catalog = {selected_policy: policy_fixture(selected_policy, selected_move)}
    baseline = policy_evaluation(catalog, selected_policy)
    catalog[other_policy] = policy_fixture(other_policy, other_move)
    assert policy_evaluation(catalog, selected_policy) == baseline
    other_readiness, other_cards = catalog[other_policy]
    catalog[other_policy] = other_readiness * 3, other_cards * 3
    assert policy_evaluation(catalog, selected_policy) == baseline
    del catalog[other_policy]
    assert policy_evaluation(catalog, selected_policy) == baseline
    assert baseline[1][2] == exact(0.42 * 0.5)
    assert baseline[4].ranked[0].card_id == "card-0"


def test_probability_priority_italian_is_unchanged_when_ruy_is_added_expanded_or_removed():
    assert_other_policy_cannot_change_selected_policy("italian", "f1c4", "ruy", "f1b5")


def test_probability_priority_ruy_is_unchanged_when_italian_is_added_expanded_or_removed():
    assert_other_policy_cannot_change_selected_policy("ruy", "f1b5", "italian", "f1c4")


def test_probability_priority_policy_conditioning_does_not_force_opponent_cooperation():
    readiness, cards = policy_fixture("italian", "f1c4")
    assert decision_reach(readiness[1].routes, policy_id="italian") == exact(0.42)
    assert decision_reach(readiness[2].routes, policy_id="italian") == exact(0.42 * 0.5)
    assert decision_reach(readiness[3].routes, policy_id="italian") == exact(0.42 * 0.5 * 0.25)
    gain = marginal_card_value(readiness, cards[3], policy_id="italian")
    assert gain.value == (0.42 * 0.5 * 0.25) * (0.8 - 0.2)


def test_probability_priority_learner_policy_moves_have_no_inferred_branch_probability():
    italian, _ = policy_fixture("italian", "f1c4")
    ruy, _ = policy_fixture("ruy", "f1b5")
    assert italian[2].routes[0].move_prefix == ruy[2].routes[0].move_prefix
    assert italian[2].decision_id != ruy[2].decision_id
    assert italian[3].routes[0].move_prefix[4] == "f1c4"
    assert ruy[3].routes[0].move_prefix[4] == "f1b5"
    assert italian[3].routes[0].probability == ruy[3].routes[0].probability == exact(0.42 * 0.5 * 0.25)


def test_probability_priority_identical_transposed_positions_have_independent_policy_mass():
    moves_a = ("e2e4", "g8f6", "g1f3", "d7d6")
    moves_b = ("e2e4", "d7d6", "g1f3", "g8f6")
    boards = [chess.Board(), chess.Board()]
    for board, moves in zip(boards, (moves_a, moves_b)):
        for move in moves:
            board.push_uci(move)
    assert opening_position_key(boards[0].fen()) == opening_position_key(boards[1].fen())
    shared_id = opening_position_key(boards[0].fen()) + ":d2d4"
    states = []
    for policy_id, first_mass, second_mass in (("italian", 0.2, 0.3), ("ruy", 0.1, 0.15)):
        first = route(exact(first_mass), moves_a, policy_id=policy_id)
        second = route(exact(second_mass), moves_b, policy_id=policy_id)
        revisit = route(exact(first_mass / 2), moves_a + ("f3g1", "f6g8", "g1f3", "g8f6"), policy_id=policy_id)
        states.append(DecisionReadiness(shared_id, (first, second, first, revisit), exact(0.2), policy_id=policy_id))
    italian, ruy = states
    assert decision_reach(italian.routes, policy_id="italian") == exact(0.5)
    assert decision_reach(ruy.routes, policy_id="ruy") == exact(0.25)
    for selected, expected_mass in ((italian, 0.5), (ruy, 0.25)):
        result = preparedness((selected, selected), policy_id=selected.policy_id)
        assert len(result.contributions) == 1
        assert result.value == expected_mass * 0.2
        card = effect("shared-card", shared_id, policy_id=selected.policy_id)
        assert marginal_card_value((selected,), card, policy_id=selected.policy_id).value == expected_mass * (0.8 - 0.2)
    with pytest.raises(ValueError, match="Policy mismatch"):
        preparedness(states, policy_id="italian")


def test_probability_priority_same_context_different_roots_are_disjoint_within_policy():
    first = route(exact(0.2), (), root="first", policy_id="italian")
    second = route(exact(0.3), ("e2e4",), root="second", policy_id="italian")
    assert decision_reach((first, second), policy_id="italian") == exact(0.5)


@pytest.mark.parametrize("policy_id", ("italian", "ruy"))
def test_probability_priority_policy_selection_preserves_outside_unassigned_and_unknown_mass(policy_id):
    distribution = OpponentReplyDistribution((OpponentReply("e7e5", exact(0.6), True),
                                             OpponentReply("c7c5", exact(0.2), True),
                                             OpponentReply("e7e6", exact(0.1), False)), exact(0.1))
    before = asdict(distribution)
    incoming = route(route_probability(exact(1), (next(reply.probability for reply in distribution.replies if reply.move_uci == "e7e5"),)),
                     ("e2e4", "e7e5"), policy_id=policy_id)
    assert decision_reach((incoming,), policy_id=policy_id) == exact(0.6)
    assert distribution.authored_mass == exact(0.8)
    assert distribution.outside_mass == distribution.unassigned_mass == exact(0.1)
    uncertain = route(route_probability(exact(0.6), (ProbabilityEvidence.unknown("missing opponent evidence"),)),
                      ("e2e4", "e7e5", "g1f3"), policy_id=policy_id)
    result = decision_reach((uncertain,), policy_id=policy_id)
    assert (result.lower, result.upper, result.status) == (0, 0.6, "bounded")
    assert result.reason == "missing opponent evidence"
    assert asdict(distribution) == before
