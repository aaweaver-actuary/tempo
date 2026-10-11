"""Direct end-to-end regular regressions for #174; no shadow harness dependency."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import chess
import pytest

from app.services import opening_card_valuation as valuation
from app.services.decision_readiness import (
    DecisionObservationEvidence, PresentationIdentity, ReadinessSnapshot,
    StudyReviewEvidence, decision_readiness,
)
from app.services.opening_policy_reach import PolicyReachRoute, SelectedOpponentEvidence, propagate_policy_reach
from app.services.opening_preparedness import CardLearningEffect, ProbabilityEvidence as P
from app.services.opening_ranking_evaluation import OpeningRankingCandidate, canonical_json
from app.services.opening_segmentation import decision_identity, opening_position_key, presentation_occurrences
from app.services.scheduler import schedule_review
from test_opening_policy_reach import CONTEXT, ITALIAN, RUY, evidence_for, policy_for, selected_evidence

AS_OF = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def fixture(policy_id="italian", moves=ITALIAN, policy_version=1, repertoire_id=None):
    repertoire_id = repertoire_id or policy_id
    policy = replace(policy_for(policy_id, (moves,)), policy_version=policy_version, repertoire_id=repertoire_id)
    routes = (PolicyReachRoute(policy_id, "line", moves, "generation-7"),)
    evidence = evidence_for((moves,))
    reach = propagate_policy_reach(policy, routes, evidence, context=CONTEXT)
    bindings = []
    board = chess.Board()
    for move in moves:
        if board.turn == chess.WHITE:
            presentation = PresentationIdentity("memory-" + str(len(bindings)), 1, repertoire_id, "white")
            snapshot = ReadinessSnapshot(presentation, board.fen(), (move,),
                reviews=(StudyReviewEvidence(len(bindings), presentation.card_id, AS_OF, "again", card_revision=1),))
            identity = decision_identity(repertoire_id, board.fen(), "white", move, policy_version)
            bindings.append(valuation.DecisionReadinessBinding(policy_id, policy_version, identity, snapshot, 0))
        board.push_uci(move)
    early = next(decision.identity for decision in reach.decisions
                 if any(path.route.move_prefix == moves[:4] for path in decision.incoming_paths))
    deep = next(decision.identity for decision in reach.decisions
                if any(path.route.move_prefix == moves[:6] for path in decision.incoming_paths))
    candidates = (
        valuation.ValuationCandidate(OpeningRankingCandidate("multi", 1, canonical_json({"state": "new",
            "introduced_at": None, "archived": 0, "pending_validation": 0}), policy_id=policy_id),
            policy_version, 1, (early, deep), (("generation-7", "line"),),
            CardLearningEffect("multi", ((early, P.exact(.6)), (deep, P.exact(.8))), policy_id=policy_id),
            "explicit-hand-fixture-v1"),
        valuation.ValuationCandidate(OpeningRankingCandidate("single", 2, canonical_json({"state": "new",
            "introduced_at": None, "archived": 0, "pending_validation": 0}), policy_id=policy_id),
            policy_version, 1, (early,), (("generation-7", "line"),),
            CardLearningEffect("single", ((early, P.exact(.9)),), policy_id=policy_id),
            "explicit-hand-fixture-v1"),
    )
    return valuation.OpeningCardValuationSnapshot(policy, CONTEXT, routes, evidence, tuple(bindings),
                                                candidates, AS_OF, date(2026, 10, 11))


def rows(result):
    return {row["card_id"]: row for row in result["evaluations"][0]["rows"]}


@pytest.mark.parametrize("policy_id,moves,other_id,other_moves", [
    ("italian", ITALIAN, "ruy", RUY), ("ruy", RUY, "italian", ITALIAN),
])
def test_valuation_italian_and_ruy_add_remove_duplicate_isolation(policy_id, moves, other_id, other_moves):
    catalog = {policy_id: fixture(policy_id, moves)}
    baseline = valuation.evaluate_opening_card_valuation(catalog[policy_id])
    for copies in (1, 2, 4):
        catalog[other_id] = [fixture(other_id, other_moves)] * copies
        assert valuation.evaluate_opening_card_valuation(catalog[policy_id]) == baseline
    del catalog[other_id]
    assert valuation.evaluate_opening_card_valuation(catalog[policy_id]) == baseline


def test_valuation_real_reach_readiness_kernel_multi_decision_hand_calculation():
    current = fixture()
    result = valuation.evaluate_opening_card_valuation(current)
    scored = rows(result)
    assert scored["multi"]["score"] == pytest.approx(.21 * .6 + .0525 * .8)
    assert scored["single"]["score"] == pytest.approx(.21 * .9)
    assert (scored["single"]["alternative_rank"], scored["multi"]["alternative_rank"]) == (1, 2)
    contributions = scored["multi"]["metadata"]["valuation"]["marginal"]["contributions"]
    assert len(contributions) == 2
    assert sorted(item["reach"]["lower"] for item in contributions) == pytest.approx([.0525, .21])
    for item in scored["multi"]["metadata"]["valuation"]["readiness"].values():
        assert item["estimate"]["basis"] == "conservative_failure"
        assert item["recall"]["lower"] == 0
    configuration = result["scorer_configuration"]
    assert configuration["preparedness"]["lower"] == 0
    assert configuration["complete_preparedness"]
    assert result["evaluations"][0]["spearman"] == -1
    assert result["evaluations"][0]["top_k"]["1"]["overlap"] == 0


def test_valuation_opponent_stochastic_factors_and_outside_mass_survive():
    result = valuation.evaluate_opening_card_valuation(fixture())
    metadata = rows(result)["multi"]["metadata"]["valuation"]
    deep = next(decision for decision in metadata["reach"] if decision["probability"]["lower"] == .0525)
    factors = deep["incoming_paths"][0]["opponent_factors"]
    assert [factor[3]["lower"] for factor in factors] == [.42, .50, .25]
    assert [diagnostic["known_outside_mass"] for diagnostic in result["scorer_configuration"]["reach"]["opponent_diagnostics"]]
    changed = fixture()
    after_e4 = chess.Board()
    after_e4.push_uci("e2e4")
    alternative = selected_evidence(after_e4, {"e7e5": .21, "c7c5": .79})
    changed = replace(changed, opponent_evidence=tuple(alternative if evidence.evidence_id == alternative.evidence_id
                                                     else evidence for evidence in changed.opponent_evidence))
    assert rows(valuation.evaluate_opening_card_valuation(changed))["multi"]["score"] == pytest.approx(.084)


def test_valuation_real_fsrs_decision_readiness_used_at_fixed_timestamp():
    current = fixture()
    target = next(binding for binding in current.readiness if binding.decision_id in current.candidates[1].affected_decision_ids)
    schedule = schedule_review("correct", reviewed_at=AS_OF)
    memory = replace(target.snapshot, fsrs_card_json=schedule.fsrs_card_json,
        reviews=(StudyReviewEvidence(7, target.snapshot.presentation.card_id, AS_OF, "correct", card_revision=1),))
    target = replace(target, snapshot=memory)
    current = replace(current, readiness=tuple(target if binding.decision_id == target.decision_id else binding
                                              for binding in current.readiness))
    # Supply an explicit non-regressive hypothetical effect, not an adapter-generated gain.
    candidates = tuple(replace(candidate, effect=replace(candidate.effect,
        projected_readiness=tuple((identity, P.exact(1) if identity == target.decision_id else projection)
                                  for identity, projection in candidate.effect.projected_readiness)))
        for candidate in current.candidates)
    current = replace(current, candidates=candidates)
    expected = decision_readiness(memory, AS_OF, decision_index=0)
    assert expected.readiness_score == 1
    result = valuation.evaluate_opening_card_valuation(current)
    estimate = rows(result)["single"]["metadata"]["valuation"]["readiness"][target.decision_id]["estimate"]
    assert estimate["readiness_score"] == expected.readiness_score
    assert estimate["basis"] == "fsrs"
    assert rows(result)["single"]["score"] == 0


def test_valuation_cumulative_prefix_card_readiness_not_copied_to_decisions():
    current = fixture()
    schedule = schedule_review("correct", reviewed_at=AS_OF)
    presentation = PresentationIdentity("prefix", 1, "italian", "white")
    prefix = ReadinessSnapshot(presentation, chess.STARTING_FEN, ITALIAN,
        fsrs_card_json=schedule.fsrs_card_json,
        reviews=(StudyReviewEvidence(1, "prefix", AS_OF, "correct", card_revision=1),))
    occurrences = presentation_occurrences("italian", {"id": "prefix", "revision": 1,
        "trained_color": "white", "start_fen": prefix.start_fen, "moves_json": json.dumps(prefix.moves)})
    bindings = tuple(valuation.DecisionReadinessBinding("italian", 1, occurrence["decision_id"], prefix, index)
                     for index, occurrence in enumerate(occurrences))
    result = valuation.evaluate_opening_card_valuation(replace(current, readiness=bindings))
    assert not result["scorer_configuration"]["complete_preparedness"]
    for row in rows(result).values():
        assert row["score"] is None and row["alternative_rank"] is None
        for provenance in row["metadata"]["valuation"]["readiness"].values():
            assert provenance["estimate"]["card_estimate"]["readiness_score"] == 1
            assert provenance["recall"]["status"] == "unknown"
            assert "no_independent_decision_memory" in provenance["estimate"]["reasons"]


def test_valuation_shared_decisions_and_duplicate_routes_not_double_counted():
    current = fixture()
    baseline = valuation.evaluate_opening_card_valuation(current)
    # Identical repeated inputs collapse; distinct line representations retain provenance only.
    duplicate_route = replace(current.routes[0], line_id="copy")
    expanded = replace(current, routes=current.routes * 2 + (duplicate_route,),
                       readiness=current.readiness * 2, candidates=current.candidates * 2)
    result = valuation.evaluate_opening_card_valuation(expanded)
    assert {identity: row["score"] for identity, row in rows(result).items()} == {
        identity: row["score"] for identity, row in rows(baseline).items()}
    assert len(result["scorer_configuration"]["preparedness"]["contributions"]) == 4
    assert len(rows(result)["multi"]["metadata"]["valuation"]["marginal"]["contributions"]) == 2


@pytest.mark.parametrize("missing", ["reach", "readiness", "effect", "empty_effect", "partial_effect", "bounded_effect"])
def test_valuation_unknown_and_bounded_evidence_remain_explicit(missing):
    current = fixture()
    if missing == "reach":
        current = replace(current, opponent_evidence=())
    elif missing == "readiness":
        current = replace(current, readiness=())
    else:
        multi = current.candidates[0]
        effect = None if missing == "effect" else replace(multi.effect,
            projected_readiness=() if missing == "empty_effect" else multi.effect.projected_readiness[:1] if missing == "partial_effect" else
            tuple((identity, P.bounded(.4, .8, "explicit_sensitivity")) for identity in multi.affected_decision_ids))
        current = replace(current, candidates=(replace(multi, effect=effect), current.candidates[1]))
    result = valuation.evaluate_opening_card_valuation(current)
    row = rows(result)["multi"]
    assert row["score"] is None and row["alternative_rank"] is None and row["reason"]
    assert row["metadata"]["valuation"]["marginal"]["upper"] > 0
    if missing == "effect":
        assert "missing_learning_effect" in row["reason"]


def test_valuation_partial_opponent_assignment_not_normalized():
    current = fixture()
    board = chess.Board()
    board.push_uci("e2e4")
    partial = selected_evidence(board, {"c7c5": .6}, unknown_mass=.4)
    current = replace(current, opponent_evidence=tuple(partial if evidence.evidence_id == partial.evidence_id else evidence
                                                     for evidence in current.opponent_evidence))
    result = valuation.evaluate_opening_card_valuation(current)
    assert rows(result)["multi"]["score"] is None
    diagnostic = next(item for item in result["scorer_configuration"]["reach"]["opponent_diagnostics"]
                      if item["evidence_id"] == partial.evidence_id)
    assert diagnostic["known_outside_mass"] == .6
    assert diagnostic["unassigned_mass"]["lower"] == .4
    assert next(probability for move, probability in diagnostic["replies"] if move == "e7e5")["upper"] == .4


def test_valuation_unresolved_policy_actions_and_missing_reach_are_unscorable():
    current = fixture()
    current = replace(current, policy=replace(current.policy, actions=tuple(action for action in current.policy.actions
        if action.position_key != opening_position_key(chess.STARTING_FEN))))
    result = valuation.evaluate_opening_card_valuation(current)
    assert all(row["score"] is None for row in rows(result).values())
    assert any("unresolved_policy_action" in row["reason"] for row in rows(result).values())
    assert not result["scorer_configuration"]["complete_preparedness"]
    shortened = replace(fixture(), routes=(replace(fixture().routes[0], moves=ITALIAN[:3]),))
    assert "missing_decision_reach" in rows(valuation.evaluate_opening_card_valuation(shortened))["multi"]["reason"]


@pytest.mark.parametrize("foreign", ["route", "candidate", "readiness", "effect", "context", "version", "presentation", "game", "observation"])
def test_valuation_foreign_identity_fails_before_scoring(monkeypatch, foreign):
    current = fixture()
    if foreign == "route":
        current = replace(current, routes=(replace(current.routes[0], policy_id="ruy"),))
    elif foreign == "candidate":
        entry = current.candidates[0]
        current = replace(current, candidates=(replace(entry, candidate=replace(entry.candidate, policy_id="ruy")),))
    elif foreign == "effect":
        current = replace(current, candidates=(replace(current.candidates[0],
            effect=replace(current.candidates[0].effect, policy_id="ruy")), current.candidates[1]))
    elif foreign == "context":
        evidence = current.opponent_evidence[0]
        current = replace(current, opponent_evidence=(SelectedOpponentEvidence(
            "other", evidence.evidence_id, evidence.snapshot()),))
    else:
        binding = current.readiness[0]
        if foreign == "readiness":
            binding = replace(binding, policy_id="ruy")
        elif foreign == "version":
            binding = replace(binding, policy_version=2)
        elif foreign == "presentation":
            binding = replace(binding, snapshot=replace(binding.snapshot,
                presentation=replace(binding.snapshot.presentation, repertoire_id="ruy")))
        elif foreign == "observation":
            observation = DecisionObservationEvidence("foreign", binding.snapshot.presentation, 0,
                "foreign-decision", binding.snapshot.moves[0], AS_OF, AS_OF, binding.snapshot.moves[0])
            binding = replace(binding, snapshot=replace(binding.snapshot, observations=(observation,)))
        else:
            from app.services.decision_readiness import GameDecisionEvidence
            event = GameDecisionEvidence("foreign", binding.snapshot.presentation.card_id, "ruy", "white",
                binding.snapshot.start_fen, binding.snapshot.moves[0], "a2a3", "miss", AS_OF, True)
            binding = replace(binding, snapshot=replace(binding.snapshot, game_events=(event,)))
        current = replace(current, readiness=(binding, *current.readiness[1:]))
    def forbidden(*args, **kwargs):
        pytest.fail("Identity error reached computation")
    for name in ("propagate_policy_reach", "decision_readiness", "preparedness", "marginal_card_value", "rank"):
        monkeypatch.setattr(valuation, name, forbidden)
    with pytest.raises(ValueError):
        valuation.evaluate_opening_card_valuation(current)


def test_valuation_policy_version_bridge_retains_both_verified_decision_ids():
    current = fixture(policy_version=3)
    result = valuation.evaluate_opening_card_valuation(current)
    assert rows(result)["multi"]["score"] == pytest.approx(.168)
    for identity, provenance in rows(result)["multi"]["metadata"]["valuation"]["readiness"].items():
        assert provenance["estimate"]["decision_id"] != identity
        assert provenance["estimate"]["presentation"]["repertoire_id"] == "italian"
    assert result["source_versions"]["policy_version"] == 3


def test_valuation_input_permutation_is_byte_identical():
    current = fixture()
    permuted = replace(current, routes=tuple(reversed(current.routes)), readiness=tuple(reversed(current.readiness)),
        candidates=tuple(reversed(current.candidates)), opponent_evidence=tuple(reversed(current.opponent_evidence)))
    assert canonical_json(valuation.evaluate_opening_card_valuation(current)) == canonical_json(
        valuation.evaluate_opening_card_valuation(permuted))


def test_valuation_offline_replay_is_identical_across_hash_seeds(tmp_path):
    result = valuation.evaluate_opening_card_valuation(fixture())
    document = result.pop("snapshot")
    path = tmp_path / "snapshot.json"
    path.write_text(canonical_json(document))
    assert valuation.replay_opening_card_valuation(document) == result
    source = """import json,sys
from app.services.opening_card_valuation import replay_opening_card_valuation
from app.services.opening_ranking_evaluation import canonical_json
print(canonical_json(replay_opening_card_valuation(json.load(open(sys.argv[1])))))
"""
    for seed in ("1", "77"):
        output = subprocess.check_output([sys.executable, "-c", source, str(path)],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, text=True)
        assert output.strip() == canonical_json(result)
    document["candidates"][0]["metadata"]["valuation"]["value"] = 999
    with pytest.raises(ValueError, match="digest"):
        valuation.replay_opening_card_valuation(document)


def test_valuation_no_production_queue_admission_or_network_writes(monkeypatch):
    current = fixture()
    before = canonical_json(valuation._plain(current))
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only valuation accessed storage, network, or scheduling mutation")
    from app import database
    import socket
    from app.services import scheduler
    from app import postgres_store
    monkeypatch.setattr(database, "connection", forbidden)
    monkeypatch.setattr(postgres_store, "connection", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(scheduler, "schedule_review", forbidden)
    assert rows(valuation.evaluate_opening_card_valuation(current))["multi"]["score"] == pytest.approx(.168)
    assert canonical_json(valuation._plain(current)) == before
    with pytest.raises(FrozenInstanceError):
        current.version = "changed"


def test_valuation_empty_and_all_unscorable_candidates_are_explicit():
    empty = valuation.evaluate_opening_card_valuation(replace(fixture(), candidates=()))
    assert empty["evaluations"][0]["coverage_undefined_reason"] == "empty_candidates"
    current = fixture()
    current = replace(current, candidates=tuple(replace(entry, effect=None) for entry in current.candidates))
    result = valuation.evaluate_opening_card_valuation(current)
    assert result["evaluations"][0]["scored_count"] == 0
    assert result["evaluations"][0]["status"] == "partial"


@pytest.mark.parametrize("invalid", ["binding", "publication", "effect_decision", "effect_provenance", "order", "revision", "input_version"])
def test_valuation_invalid_bindings_effects_and_baseline_are_rejected(invalid):
    current = fixture()
    with pytest.raises(ValueError):
        if invalid == "binding":
            current = replace(current, readiness=(replace(current.readiness[0], decision_id="wrong"),))
        elif invalid == "input_version":
            current = replace(current, version="future")
        else:
            entry = current.candidates[0]
            if invalid == "publication":
                entry = replace(entry, route_publications=(("foreign", "line"),))
            elif invalid == "effect_decision":
                entry = replace(entry, effect=replace(entry.effect, projected_readiness=(("foreign", P.exact(.8)),)))
            elif invalid == "effect_provenance":
                entry = replace(entry, effect_reference=None)
            elif invalid == "order":
                entry = replace(entry, candidate=replace(entry.candidate, production_rank=2))
            else:
                entry = replace(entry, card_revision=0)
            current = replace(current, candidates=(entry, current.candidates[1]))
        valuation.evaluate_opening_card_valuation(current)


def test_valuation_conflicting_duplicate_evidence_is_rejected():
    current = fixture()
    with pytest.raises(ValueError, match="Conflicting"):
        replace(current, readiness=(*current.readiness, replace(current.readiness[0], decision_index=1)))


def test_valuation_snapshot_detaches_mutable_readiness_collections():
    current = fixture()
    original = current.readiness[0]
    mutable_reviews = list(original.snapshot.reviews)
    binding = replace(original, snapshot=replace(original.snapshot, reviews=mutable_reviews))
    snapshot = replace(current, readiness=(binding, *current.readiness[1:]))
    baseline = valuation.evaluate_opening_card_valuation(snapshot)
    mutable_reviews.clear()
    assert valuation.evaluate_opening_card_valuation(snapshot) == baseline


def test_valuation_policy_identity_can_differ_from_repertoire_identity():
    current = fixture(policy_id="selected-experiment", repertoire_id="italian")
    result = valuation.evaluate_opening_card_valuation(current)
    assert result["policy_id"] == "selected-experiment"
    assert rows(result)["multi"]["score"] == pytest.approx(.168)
    assert all(provenance["estimate"]["presentation"]["repertoire_id"] == "italian"
               for provenance in rows(result)["multi"]["metadata"]["valuation"]["readiness"].values())


def test_valuation_exports_complete_precomputed_bundles_for_independent_harness():
    result = valuation.evaluate_opening_card_valuation(fixture())
    configuration = result["snapshot"]["scorer_configuration"]
    assert configuration["baseline_bundle"] == "production-order@1"
    for bundle in configuration["precomputed_score_bundles"]:
        assert set(bundle["scores"]) == {"multi", "single"}
        assert bundle["assumption"] and bundle["provenance"] and bundle["cohort_id"]
    assert configuration["precomputed_score_bundles"][0]["scores"]["multi"]["score"] == -1
    assert configuration["precomputed_score_bundles"][1]["scores"]["multi"]["score"] == pytest.approx(.168)


@pytest.mark.parametrize("invalid", ["naive_time", "day", "boolean_policy_version"])
def test_valuation_rejects_ambiguous_dates_and_boolean_versions(invalid):
    current = fixture()
    with pytest.raises(ValueError):
        if invalid == "naive_time":
            current = replace(current, as_of=AS_OF.replace(tzinfo=None))
        elif invalid == "day":
            current = replace(current, study_day=AS_OF)
        else:
            current = replace(current, readiness=tuple(replace(binding, policy_version=True) for binding in current.readiness))
        valuation.evaluate_opening_card_valuation(current)


def test_valuation_black_policy_retains_white_opponent_uncertainty():
    moves = ("e2e4", "e7e5", "g1f3", "b8c6")
    policy = policy_for("black-policy", (moves,), trained_color="black")
    evidence = evidence_for((moves,), trained_color="black")
    routes = (PolicyReachRoute(policy.policy_id, "line", moves, "generation-1"),)
    reach = propagate_policy_reach(policy, routes, evidence, context=CONTEXT)
    bindings = []
    board = chess.Board()
    for move in moves:
        if board.turn == chess.BLACK:
            presentation = PresentationIdentity("black-memory-" + str(len(bindings)), 1, policy.repertoire_id, "black")
            memory = ReadinessSnapshot(presentation, board.fen(), (move,),
                reviews=(StudyReviewEvidence(len(bindings), presentation.card_id, AS_OF, "again", card_revision=1),))
            identity = decision_identity(policy.repertoire_id, board.fen(), "black", move)
            bindings.append(valuation.DecisionReadinessBinding(policy.policy_id, 1, identity, memory, 0))
        board.push_uci(move)
    affected = tuple(decision.identity for decision in reach.decisions)
    candidate = valuation.ValuationCandidate(OpeningRankingCandidate("black-card", 1, policy_id=policy.policy_id),
        1, 1, affected, (("generation-1", "line"),),
        CardLearningEffect("black-card", tuple((identity, P.exact(.8)) for identity in affected),
                           policy_id=policy.policy_id), "explicit-black-fixture-v1")
    snapshot = valuation.OpeningCardValuationSnapshot(policy, CONTEXT, routes, evidence, tuple(bindings),
        (candidate,), AS_OF, AS_OF.date())
    result = valuation.evaluate_opening_card_valuation(snapshot)
    assert rows(result)["black-card"]["score"] == pytest.approx(.1 * .8 + .1 * .1 * .8)


def test_valuation_matching_bound_decision_survives_divergent_prefix_actions():
    current = fixture()
    # The observed Nf3 decision agrees with Italian. A later Bb5 presentation
    # occurrence is unrelated to that binding and must not discard its failure.
    prefix = ReadinessSnapshot(PresentationIdentity("divergent-prefix", 1, "italian", "white"),
                               chess.STARTING_FEN, RUY)
    occurrences = presentation_occurrences("italian", {"id": "divergent-prefix", "revision": 1,
        "trained_color": "white", "start_fen": prefix.start_fen, "moves_json": json.dumps(prefix.moves)})
    target = occurrences[1]
    observation = DecisionObservationEvidence("target-failure", prefix.presentation, 1,
        target["decision_id"], target["expected_uci"], AS_OF, AS_OF, "f1c4")
    prefix = replace(prefix, observations=(observation,))
    expected = decision_readiness(prefix, AS_OF, decision_index=1)
    assert expected.readiness_score == 0 and expected.basis == "conservative_failure"
    binding = valuation.DecisionReadinessBinding("italian", 1, target["decision_id"], prefix, 1)
    entry = current.candidates[1]
    candidate = replace(entry, candidate=replace(entry.candidate, production_rank=1),
        affected_decision_ids=(target["decision_id"],),
        effect=replace(entry.effect, projected_readiness=((target["decision_id"], P.exact(.8)),)))
    current = replace(current, readiness=(binding,), candidates=(candidate,))
    result = valuation.evaluate_opening_card_valuation(current)
    assert rows(result)["single"]["score"] == pytest.approx(.42 * .8)
    estimate = rows(result)["single"]["metadata"]["valuation"]["readiness"][target["decision_id"]]["estimate"]
    assert estimate["decision_id"] == expected.decision_id
    assert estimate["readiness_score"] == expected.readiness_score
    # Binding the actual foreign action still fails before any scoring.
    foreign = replace(binding, decision_index=2, decision_id=occurrences[2]["decision_id"])
    with pytest.raises(ValueError, match="contradicts selected policy"):
        valuation.evaluate_opening_card_valuation(replace(current, readiness=(foreign,)))
