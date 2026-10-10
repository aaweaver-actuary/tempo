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


def route(probability, prefix=(), context="rapid", root="root"):
    return RouteReach(context, root, prefix, probability)


def decision(identifier, reach=0.6, recall=0.2):
    return DecisionReadiness(identifier, (route(exact(reach), (identifier,)),), exact(recall))


def effect(card_id, identifier, projected=0.8):
    return CardLearningEffect(card_id, ((identifier, exact(projected)),))


def presentation(moves, color="white", start=chess.STARTING_FEN):
    return {"id": "fixture", "revision": 1, "trained_color": color,
            "start_fen": start, "moves_json": json.dumps(moves)}


def test_probability_priority_hand_calculated_route_preparedness_and_marginal_gain():
    reach = route_probability(exact(0.5), (exact(0.6), exact(0.4)))
    assert reach.lower == pytest.approx(0.12)
    readiness = (DecisionReadiness("child", (route(reach, ("e2e4", "c7c5")),), exact(0.25)),)
    before = preparedness(readiness)
    gain = marginal_card_value(readiness, effect("card", "child", 0.75))
    assert before.value == pytest.approx(0.03)
    assert gain.value == pytest.approx(0.06)
    after = preparedness((DecisionReadiness("child", readiness[0].routes, exact(0.75)),))
    assert gain.value == pytest.approx(after.value - before.value)
    assert gain.contributions[0].projected_recall == exact(0.75)


def test_probability_priority_frequent_deeper_decision_outranks_rare_shallow_decision():
    readiness = (decision("deep", 0.6), decision("shallow", 0.05), decision("ready", 0.6, 0.75))
    ranking = rank_card_values(readiness, (effect("rare", "shallow"), effect("frequent", "deep"), effect("ready", "ready")))
    assert ranking.ranked[0].card_id == "frequent"
    assert {result.card_id for result in ranking.ranked[1:]} == {"rare", "ready"}
    assert [result.value for result in ranking.ranked] == pytest.approx([0.36, 0.03, 0.03])
    assert not ranking.incomplete


@pytest.mark.parametrize("lower_reach,higher_reach,current,projected", tuple(product((0, 0.2), (0.6, 1), (0, 0.4), (0.7, 1))))
def test_probability_priority_increasing_reach_cannot_reduce_marginal_value(lower_reach, higher_reach, current, projected):
    lower_value = marginal_card_value((decision("d", lower_reach, current),), effect("c", "d", projected))
    higher_value = marginal_card_value((decision("d", higher_reach, current),), effect("c", "d", projected))
    assert 0 <= lower_value.value <= higher_value.value <= higher_reach * (1 - current)


@pytest.mark.parametrize("reach,lower_current,higher_current,target", tuple(product((0, 0.3, 1), (0, 0.1), (0.5, 0.7), (0.8, 1))))
def test_probability_priority_increasing_readiness_cannot_increase_remaining_benefit(reach, lower_current, higher_current, target):
    lower_value = marginal_card_value((decision("d", reach, lower_current),), effect("c", "d", target))
    higher_value = marginal_card_value((decision("d", reach, higher_current),), effect("c", "d", target))
    assert 0 <= higher_value.value <= lower_value.value <= reach * (1 - lower_current)


def test_probability_priority_zero_reach_and_unchanged_readiness_have_zero_gain():
    assert marginal_card_value((decision("d", 0),), effect("c", "d")).value == 0
    assert marginal_card_value((decision("d", 1, 0.8),), effect("c", "d")).value == 0
    assert preparedness(()).value == 0
    assert marginal_card_value((), CardLearningEffect("empty", ())).value == 0


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
    trunk = DecisionReadiness("trunk", (route(exact(1)), route(exact(1))), exact(0.2))
    child = decision("child", 0.6, 0.4)
    readiness = (trunk, child, trunk)
    card = CardLearningEffect("prefix", (("trunk", exact(0.8)), ("child", exact(0.9)), ("trunk", exact(0.8))))
    assert preparedness(readiness).value == pytest.approx(0.44)
    assert marginal_card_value(readiness, card).value == pytest.approx(0.9)
    assert len(marginal_card_value(readiness, card).contributions) == 2
    assert len(rank_card_values(readiness, (card, card)).ranked) == 1


def test_probability_priority_distinct_transposed_opponent_paths_share_one_decision():
    moves_a = ["e2e4", "g8f6", "g1f3", "d7d6", "d2d4"]
    moves_b = ["e2e4", "d7d6", "g1f3", "g8f6", "d2d4"]
    occurrence_a = presentation_occurrences("rep", presentation(moves_a))[-1]
    occurrence_b = presentation_occurrences("rep", presentation(moves_b))[-1]
    assert occurrence_a["decision_id"] == occurrence_b["decision_id"]
    incoming_a = route(exact(0.2), tuple(moves_a[:occurrence_a["move_offset"]]))
    incoming_b = route(exact(0.3), tuple(moves_b[:occurrence_b["move_offset"]]))
    shared = DecisionReadiness(occurrence_a["decision_id"], (incoming_a, incoming_b, incoming_a), exact(0.2))
    assert decision_reach(shared.routes).lower == 0.5
    assert len(preparedness((shared,)).contributions) == 1
    assert marginal_card_value((shared,), effect("card", shared.decision_id)).value == pytest.approx(0.3)


def test_probability_priority_overlapping_revisits_do_not_double_count_decision_reach():
    moves = ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3"]
    occurrences = presentation_occurrences("rep", presentation(moves))
    assert occurrences[0]["decision_id"] == occurrences[-1]["decision_id"]
    earlier, revisit = route(exact(0.4)), route(exact(0.2), tuple(moves[:4]))
    assert decision_reach((revisit, earlier)).lower == 0.4
    with pytest.raises(ValueError, match="ancestor"):
        decision_reach((earlier, route(exact(0.5), tuple(moves[:4]))))


def test_probability_priority_disjoint_context_weights_and_union_bounds_remain_bounded():
    assert decision_reach((route(exact(0.3)), route(exact(0.5), context="blitz"))).lower == 0.8
    unknown = ProbabilityEvidence.unknown("Unknown context-weighted reach")
    result = decision_reach((route(unknown), route(unknown, context="blitz")))
    assert (result.lower, result.upper) == (0, 1)
    assert result.status == "bounded"
    with pytest.raises(ValueError, match="exceeds one"):
        decision_reach((route(exact(0.6)), route(exact(0.5), context="blitz")))


def test_probability_priority_conflicting_duplicates_and_invalid_references_fail():
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        decision_reach((route(exact(0.5)), route(exact(0.6))))
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        preparedness((decision("d"), decision("d", recall=0.7)))
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        CardLearningEffect("c", (("d", exact(0.5)), ("d", exact(0.6))))
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        OpponentReplyDistribution((OpponentReply("e7e5", exact(0.5), True), OpponentReply("e7e5", exact(0.6), True)), exact(0.5))
    with pytest.raises(ValueError, match="absent"):
        marginal_card_value((decision("d"),), effect("c", "missing"))
    with pytest.raises(ValueError, match="decrease"):
        marginal_card_value((decision("d", recall=0.9),), effect("c", "d", 0.8))
    with pytest.raises(ValueError, match="multiple learner responses"):
        preparedness((DecisionReadiness("d", (route(exact(0.5)),), exact(0.3)),
                      DecisionReadiness("alternate", (route(exact(0.5)),), exact(0.3))))
    with pytest.raises(ValueError, match="reach evidence"):
        DecisionReadiness("d", (), exact(0.2))


def test_probability_priority_interval_bounds_contain_all_feasible_non_regressive_changes():
    reach = ProbabilityEvidence.bounded(0.2, 0.4, "Coarse source cohort")
    current = ProbabilityEvidence.bounded(0.2, 0.5, "Sparse reviews")
    projected = ProbabilityEvidence.bounded(0.3, 0.9, "Uncertain study effect")
    readiness = (DecisionReadiness("d", (route(reach),), current),)
    result = marginal_card_value(readiness, CardLearningEffect("c", (("d", projected),)))
    assert result.value is None
    assert (result.lower, result.upper) == pytest.approx((0, 0.28))
    for actual_reach, actual_current, actual_projected in product((0.2, 0.3, 0.4), (0.2, 0.3, 0.5), (0.3, 0.5, 0.9)):
        if actual_projected >= actual_current:
            actual_gain = actual_reach * (actual_projected - actual_current)
            assert result.lower <= actual_gain <= result.upper
    impossible = ProbabilityEvidence.bounded(0, 0.1, "Low projected recall")
    with pytest.raises(ValueError, match="decrease"):
        marginal_card_value(readiness, CardLearningEffect("c", (("d", impossible),)))


def test_probability_priority_unknown_readiness_has_no_fabricated_score_even_at_zero_reach():
    unknown = ProbabilityEvidence.unknown("Not enough review evidence")
    readiness = (DecisionReadiness("d", (route(exact(0)),), unknown),)
    result = marginal_card_value(readiness, CardLearningEffect("c", (("d", unknown),)))
    assert (result.lower, result.upper) == (0, 0)
    assert result.value is None
    assert len(result.diagnostics) == 2
    ranking = rank_card_values(readiness, (CardLearningEffect("c", (("d", unknown),)),))
    assert not ranking.ranked and ranking.incomplete == (result,)
    nonzero = DecisionReadiness("d", (route(exact(0.6)),), unknown)
    assert marginal_card_value((nonzero,), effect("c", "d")).upper == pytest.approx(0.48)
    assert preparedness((nonzero,)).upper == 0.6


def test_probability_priority_totals_can_exceed_one_but_each_probability_is_bounded():
    readiness = (DecisionReadiness("a", (route(exact(1)),), exact(1)),
                 DecisionReadiness("b", (route(exact(1), ("e2e4", "e7e5")),), exact(1)))
    assert preparedness(readiness).value == 2
    assert all(0 <= item.reach.lower <= item.reach.upper <= 1 for item in preparedness(readiness).contributions)


def test_probability_priority_input_permutations_and_ties_are_deterministic():
    readiness = (decision("a", 1, 0), decision("b", 1, 0),
                 DecisionReadiness("unknown", (route(exact(1), ("unknown",)),), ProbabilityEvidence.unknown("missing")))
    cards = (effect("card-b", "a", 0.5), effect("card-a", "b", 0.5), effect("z-missing", "unknown"), effect("a-missing", "unknown"))
    expected = rank_card_values(readiness, cards)
    for decision_order in permutations(readiness):
        for card_order in permutations(cards):
            assert rank_card_values(decision_order, card_order) == expected
    assert [value.card_id for value in expected.ranked] == ["card-a", "card-b"]
    assert [value.card_id for value in expected.incomplete] == ["a-missing", "z-missing"]
    routes = (route(exact(0.1), ("a",)), route(exact(0.2), ("b",)), route(exact(0.3), ("c",)))
    for route_order in permutations(routes):
        assert decision_reach(route_order) == decision_reach(routes)


@pytest.mark.parametrize("color,moves", (("white", ["e2e4"]), ("black", ["e2e4", "e7e5"])))
def test_probability_priority_existing_identity_maps_short_prefixes_and_trained_colors(color, moves):
    occurrence = presentation_occurrences("rep", presentation(moves, color))[-1]
    root = opening_position_key(chess.STARTING_FEN)
    readiness = DecisionReadiness(occurrence["decision_id"], (route(exact(0.5), tuple(moves[:occurrence["move_offset"]]), root=root),), exact(0.2))
    assert marginal_card_value((readiness,), effect("short", occurrence["decision_id"])).value == pytest.approx(0.3)
    interior = chess.Board(); interior.push_uci("e2e4")
    custom = presentation_occurrences("rep", presentation(["e7e5", "g1f3"], start=interior.fen()))[-1]
    assert custom["move_offset"] == 1
    alternate = presentation_occurrences("rep", presentation(["d2d4"]))[-1]
    assert alternate["decision_id"] != occurrence["decision_id"]


def test_probability_priority_inputs_and_results_are_immutable():
    prefixes, incoming = ["e2e4"], []
    incoming.append(route(exact(0.6), prefixes))
    readiness = DecisionReadiness("d", incoming, exact(0.2))
    projections = [["d", exact(0.8)]]
    card = CardLearningEffect("c", projections)
    before = asdict(readiness), asdict(card)
    prefixes.append("e7e5")
    incoming.clear()
    projections.clear()
    result = marginal_card_value((readiness,), card)
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
state = DecisionReadiness('d', (RouteReach('context', 'root', (), ProbabilityEvidence.exact(.5)),), ProbabilityEvidence.exact(.2))
result = marginal_card_value((state,), CardLearningEffect('c', (('d', ProbabilityEvidence.exact(.8)),)))
assert abs(result.value - .3) < 1e-12
"""
    completed = subprocess.run([sys.executable, "-I", "-c", script, backend], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr
