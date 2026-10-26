"""Hand-computable synthetic policy facts; no live infrastructure."""
import sys
import json
from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
from pathlib import Path

import chess
import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in ("packages/game-review-contracts/src", "contracts/game-evidence/src",
             "packages/chess-core/src", "packages/evidence-math/src", "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / path))

from app.contracts.product_v02.analysis import PopulationSelection, ProbabilityEvidence, RankingCandidate, RankingInput
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.analysis.coverage.ranking import rank_candidates, rank_coverage
from app.analysis.coverage.ports import AnalysisLimits
from app.analysis.coverage.personal_evidence import (
    PersonalEncounter, PersonalGame, deduplicate_games, decision_importance, importance_from_encounters,
)
from app.contracts.product_v02.learning import GameEncounter
from app.repertoire import build_index
from tempo_evidence_math import (
    LearnerPolicy, LearnerPolicyAction, MoveProbability, MoveProbabilityDistribution,
    CountEvidence, MoveCount, move_universe, opening_position_key,
)

POPULATION = PopulationSelection(rating_min=1600, rating_max=1800, time_controls=("synthetic-rapid",))
LIMITS = AnalysisLimits(max_routes=100, max_plies=20, max_positions=1000)


def fen(*moves):
    board = chess.Board()
    for move in moves:
        board.push_uci(move)
    return board.fen()


def key(*moves):
    return opening_position_key(fen(*moves))


def route(identity, moves, repertoire="rep", **changes):
    return dict(repertoire_id=repertoire, route_id=identity, policy_id="policy:" + repertoire,
                learner_color="black", starting_fen=chess.STARTING_FEN, moves=list(moves), reentries=[]) | changes


def snapshot(*routes, mapped=(), **changes):
    mappings = []
    by_route = {item["route_id"]: item for item in routes}
    for route_id, ply, decision_id, introduction in mapped:
        raw = by_route[route_id]
        mappings.append(dict(decision_id=decision_id, repertoire_id=raw["repertoire_id"],
            position_key=key(*raw["moves"][:ply]), selected_response_uci=raw["moves"][ply],
            presentation_id="physical:" + decision_id, presentation_revision=1, repertoire_revision=1,
            introduction=introduction, introduced_at=None, available=True,
            introduction_matches_current_content=False))
    payload = dict(membership=dict(workspace_id="synthetic", snapshot_id="snapshot", revision=1,
        digest="a"*64, source_revision="source:1", captured_at="2026-10-26T12:00:00Z",
        basis="current_repertoire", scope_id="synthetic", scope_repertoire_ids=sorted({r["repertoire_id"] for r in routes}),
        complete=True, available=True, unavailable_reason=None, routes=routes),
        variant="standard", learner_color="black", boundaries=[dict(route_id=r["route_id"],
            terminal_ply=len(r["moves"]), deliberate=True) for r in routes], decision_presentations=mappings)
    payload.update(changes)
    # Shared strict contract JSON boundary, without local copies of its DTOs.
    import json
    return RepertoireSnapshot.model_validate_json(json.dumps(payload))


def policy(snapshot, repertoire="rep", overrides=None):
    actions = {}
    for route in snapshot.membership.routes:
        if route.repertoire_id != repertoire:
            continue
        board = chess.Board(route.starting_fen)
        for move in route.moves:
            if board.turn == chess.BLACK:
                actions.setdefault(opening_position_key(board.fen()), move)
            board.push_uci(move)
    actions.update(overrides or {})
    identity = "policy:" + repertoire
    return LearnerPolicy(identity, repertoire, "black", tuple(
        LearnerPolicyAction(identity, position, move) for position, move in sorted(actions.items())))


def probability(moves=(), values=None, *, unknown=0., identity=None, counts=None,
                provider="synthetic-explorer", basis="population", population=POPULATION):
    universe = move_universe(fen(*moves))
    values = values or {}
    distribution = MoveProbabilityDistribution(position=universe,
        moves=tuple(MoveProbability(move_uci=move, probability=values.get(move, None if unknown else 0.))
                    for move in universe.legal_moves), unknown_mass=unknown)
    return ProbabilityEvidence(evidence_id=identity or "evidence:" + ":".join(moves), position=universe,
        provider=provider, model=None, basis=basis, population=population if basis == "population" else None,
        personal_account="synthetic-player" if basis == "personal" else None,
        observed_at="2026-10-26T12:00:00Z", distribution=distribution,
        counts=CountEvidence(kind="counts", total_count=counts,
            moves=tuple(MoveCount(move_uci=move, count=int(value * counts)) for move, value in values.items()))
            if counts is not None else None,
        unavailable_reason=None)


def compute(source, evidence=(), *, approved=None, selected_policy=None, **changes):
    selected_policy = selected_policy or policy(source)
    request = dict(snapshot=source, index=build_index(source), policy=selected_policy,
        root_fen=chess.STARTING_FEN, approved_route_ids=tuple(approved) if approved is not None else tuple(
            r.route_id for r in source.membership.routes if r.repertoire_id == selected_policy.repertoire_id),
        population=POPULATION, provider="synthetic-explorer", model=None,
        evidence=tuple(evidence), limits=LIMITS)
    request.update(changes)
    return rank_coverage(**request)


def detail(result, kind, position, move):
    return next(item for item in result.details
                if (item.kind, item.position_key, item.move_uci) == (kind, position, move))


def personal(game_id, position, move, *, revision=1, repertoire="rep"):
    return PersonalGame("synthetic-chesscom", "synthetic-player", game_id, revision,
                        (PersonalEncounter(repertoire, position, move),))


def facts(*candidates):
    return RankingInput(repertoire_revisions=(("rep", 1),), candidates=tuple(candidates), evidence_ids=())


def candidate(identity, reach, personal=0):
    return RankingCandidate(decision_id=identity, repertoire_id="rep", reach_probability=reach,
                            evidence_support=None, uncertainty=None, personal_encounters=personal)


def test_twenty_percent_precedes_two_percent():
    result = rank_candidates(facts(candidate("two", .02), candidate("twenty", .2)))
    assert [item.decision_id for item in result.ordered] == ["twenty", "two"]
    source = snapshot(route("twenty", ("e2e4", "e7e5")), route("two", ("d2d4", "d7d5")),
        mapped=(("twenty", 1, "twenty", "unintroduced"), ("two", 1, "two", "unintroduced")))
    result = compute(source, (probability(values={"e2e4": .2, "d2d4": .02}, unknown=.78),))
    assert [item.decision_id for item in result.introductions.ordered] == ["twenty", "two"]
    assert [item.reach_probability for item in result.introductions.based_on.candidates] == [.02, .2]


def test_personal_tie_break_keeps_population_estimates_separate():
    supplied = facts(candidate("a", .2), candidate("b", .2, personal=1))
    result = rank_candidates(supplied)
    assert [item.decision_id for item in result.ordered] == ["b", "a"]
    assert result.based_on == supplied


def test_unknown_reach_is_unavailable_not_zero():
    result = rank_candidates(facts(candidate("unknown", None)))
    assert result.unavailable_reason == "population_reach_unknown"
    assert result.based_on.candidates[0].reach_probability is None


def test_reach_times_reply_not_reply_alone():
    source = snapshot(route("a", ("e2e4", "e7e5", "f1c4")),
                      route("b", ("d2d4", "d7d5", "g1f3")))
    result = compute(source, (probability(values={"e2e4": .8, "d2d4": .2}),
        probability(("e2e4", "e7e5"), {"g1f3": .2, "f1c4": .8}),
        probability(("d2d4", "d7d5"), {"c2c4": .5, "g1f3": .5})))
    first = detail(result, "opponent_reply", key("e2e4", "e7e5"), "g1f3")
    second = detail(result, "opponent_reply", key("d2d4", "d7d5"), "c2c4")
    assert first.position_reach.lower == .8
    assert first.supports[0].conditional_reply.lower == .2
    assert first.encounter_reach.lower == pytest.approx(.16)
    assert second.encounter_reach.lower == .1
    ids = [item.decision_id for item in result.gaps.ordered]
    assert ids.index(first.candidate_id) < ids.index(second.candidate_id)


def test_selected_own_policy_excludes_other_repertoire_branch():
    source = snapshot(route("chosen", ("e2e4", "e7e5", "g1f3")),
        route("excluded", ("d2d4", "d7d5")), route("other", ("d2d4", "g8f6"), repertoire="other"),
        mapped=(("chosen", 1, "chosen", "unintroduced"), ("excluded", 1, "excluded", "unintroduced"),
                ("other", 1, "other", "unintroduced")))
    selected = policy(source, overrides={key("d2d4"): "g8f6"})
    result = compute(source, (probability(values={"e2e4": .2, "d2d4": .8}),), selected_policy=selected)
    assert [item.decision_id for item in result.introductions.ordered] == ["chosen"]
    assert all(item.repertoire_id == "rep" for item in result.details)
    assert result.introductions.based_on.candidates[0].reach_probability == .2
    assert any(item.code == "outside_selected_own_policy" for item in result.diagnostics)


def test_transposed_decision_has_one_ranking_entry():
    first = ("g1f3", "g8f6", "g2g3", "g7g6", "f1g2", "f8g7")
    second = ("g2g3", "g7g6", "g1f3", "g8f6", "f1g2", "f8g7")
    source = snapshot(route("a", first), route("b", second), route("copy", first), mapped=(
        ("a", 5, "shared", "unintroduced"), ("b", 5, "shared", "unintroduced")))
    result = compute(source, (probability(values={"g1f3": .2, "g2g3": .3}, unknown=.5, counts=100),
        probability(first[:2], {"g2g3": .5}, unknown=.5, counts=50),
        probability(second[:2], {"g1f3": .4}, unknown=.6, counts=25),
        probability(first[:4], {"f1g2": .8}, unknown=.2, counts=250)))
    assert [item.decision_id for item in result.introductions.ordered] == ["shared"]
    shared = detail(result, "introduction", key(*first[:5]), "f8g7")
    assert shared.encounter_reach.lower == pytest.approx(.176)  # .2*.5*.8 + .3*.4*.8
    assert {item.route_id for item in shared.supports} == {"a", "b", "copy"}
    assert {item.presentation_id for item in shared.presentations} == {"physical:shared"}
    assert shared.encounter_reach.status == "exact"
    assert result.introductions.based_on.candidates[0].evidence_support == 25


def test_duplicate_personal_game_has_one_observation():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "unintroduced"),))
    game = personal("game", key("e2e4"), "e7e5")
    result = compute(source, (probability(values={"e2e4": 1.}),), personal_games=(game, game))
    assert result.introductions.based_on.candidates[0].personal_encounters == 1
    assert detail(result, "introduction", key("e2e4"), "e7e5").personal_sources == (
        ("synthetic-chesscom", "synthetic-player", "game", 1),)
    assert deduplicate_games((game, replace(game, source_revision=2, encounters=()))) == (
        replace(game, source_revision=2, encounters=()),)
    with pytest.raises(ValueError, match="conflicting_personal_game_revision"):
        deduplicate_games((game, replace(game, encounters=())))


def test_new_personal_evidence_changes_order_with_provenance():
    source = snapshot(route("a", ("e2e4", "e7e5")), route("b", ("d2d4", "d7d5")),
                      mapped=(("a", 1, "a", "unintroduced"), ("b", 1, "b", "unintroduced")))
    evidence = (probability(values={"e2e4": .5, "d2d4": .5}, counts=100),)
    before = compute(source, evidence)
    after = compute(source, evidence, personal_games=(personal("new", key("d2d4"), "d7d5"),))
    assert [item.decision_id for item in before.introductions.ordered] == ["a", "b"]
    assert [item.decision_id for item in after.introductions.ordered] == ["b", "a"]
    assert before.introductions.based_on.candidates[0].reach_probability == .5
    before_a = detail(before, "introduction", key("e2e4"), "e7e5")
    after_a = detail(after, "introduction", key("e2e4"), "e7e5")
    before_b = detail(before, "introduction", key("d2d4"), "d7d5")
    after_b = detail(after, "introduction", key("d2d4"), "d7d5")
    assert before_a.dependency_digest == after_a.dependency_digest
    assert before_b.dependency_digest != after_b.dependency_digest
    assert after_b.personal_sources[0][-1] == 1
    assert after_b.population_evidence == evidence
    assert after.introductions.ordered[0].reason == after.policy_version


def test_partial_mass_and_unknown_branch_not_full_coverage():
    source = snapshot(route("a", ("e2e4", "e7e5")), route("b", ("d2d4", "d7d5")),
        mapped=(("a", 1, "a", "unintroduced"), ("b", 1, "b", "unintroduced")))
    partial = probability(values={"e2e4": .6}, unknown=.4)
    result = compute(source, (partial,))
    unknown = detail(result, "introduction", key("d2d4"), "d7d5")
    assert (unknown.encounter_reach.lower, unknown.encounter_reach.upper) == (0, .4)
    fact = next(item for item in result.introductions.based_on.candidates if item.decision_id == "b")
    assert fact.reach_probability is None and fact.uncertainty == .4
    assert any(item.code == "unassigned_reply_mass" and item.unknown_mass == .4 for item in result.diagnostics)
    unavailable = compute(source)
    assert unavailable.introductions.unavailable_reason == "population_reach_unknown"
    assert detail(unavailable, "introduction", key("e2e4"), "e7e5").encounter_reach.upper == 1


def test_cycles_and_horizon_are_bounded():
    moves = ("g1f3", "g8f6", "f3g1", "f6g8") * 3
    source = snapshot(route("cycle", moves), mapped=(("cycle", 1, "knight", "unintroduced"),))
    evidence = (probability(values={"g1f3": .2}, unknown=.8),
                probability(moves[:2], {"f3g1": .5}, unknown=.5))
    result = compute(source, evidence, limits=AnalysisLimits(1, 6, 20))
    shared = detail(result, "introduction", key("g1f3"), "g8f6")
    assert len(shared.supports) == 2
    assert shared.encounter_reach.lower == .2  # Later revisit is contained in first event.
    assert any(item.code == "horizon_limit" and item.ply == 6 for item in result.diagnostics)
    root_reply = detail(result, "opponent_reply", key(), "g1f3")
    assert root_reply.encounter_reach.lower == .2
    limited = compute(source, evidence, limits=AnalysisLimits(1, 20, 2))
    assert any(item.code == "position_limit" and item.ply == 2 for item in limited.diagnostics)


def test_conflicting_or_unapproved_content_not_introduction_candidate():
    source = snapshot(route("a", ("e2e4", "e7e5")), route("b", ("e2e4", "c7c5")),
        mapped=(("a", 1, "a", "unintroduced"), ("b", 1, "b", "unintroduced")))
    result = compute(source, (probability(values={"e2e4": 1.}),))
    assert result.introductions.ordered == ()
    assert any(item.code == "own_response_conflict" for item in result.diagnostics)
    clean = snapshot(route("a", ("e2e4", "e7e5")), route("b", ("d2d4", "d7d5")),
        mapped=(("a", 1, "a", "unintroduced"), ("b", 1, "b", "unintroduced")))
    assert [item.decision_id for item in compute(clean, approved=("a",)).introductions.ordered] == ["a"]
    assert compute(clean, approved=()).introductions.ordered == ()


def test_population_settings_required():
    source = snapshot(route("a", ("e2e4", "e7e5")))
    with pytest.raises(ValueError, match="explicit_provider_population_settings_required"):
        compute(source, population=None)
    with pytest.raises(ValueError, match="probability_population_settings_mismatch"):
        compute(source, (probability(values={"e2e4": 1.}),), population=PopulationSelection(
            rating_min=2200, rating_max=2400, time_controls=("synthetic-blitz",)))
    # Same numeric rating band on a different provider is never silently reused.
    other = probability(values={"e2e4": 1.}, provider="synthetic-chesscom")
    result = compute(source, (other,))
    assert any(item.code == "probability_unavailable" for item in result.diagnostics)


def test_deterministic_ties_and_no_input_mutation():
    source = snapshot(route("b", ("d2d4", "d7d5")), route("a", ("e2e4", "e7e5")),
        mapped=(("a", 1, "a", "unintroduced"), ("b", 1, "b", "unintroduced")))
    evidence = (probability(values={"e2e4": .5, "d2d4": .5}),)
    original = deepcopy(source.model_dump(mode="json")), evidence[0].model_dump(mode="json")
    first = compute(source, evidence, approved=("b", "a"))
    second = compute(source, evidence, approved=("a", "b"))
    assert first == second
    assert [item.decision_id for item in first.introductions.ordered] == ["a", "b"]
    assert original == (source.model_dump(mode="json"), evidence[0].model_dump(mode="json"))
    with pytest.raises(FrozenInstanceError):
        first.details[0].covered = False


def test_common_covered_reply_remains_available_for_repair_owner():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "introduced"),))
    result = compute(source, (probability(values={"e2e4": .8, "d2d4": .2}),))
    covered = detail(result, "opponent_reply", key(), "e2e4")
    assert covered.covered and covered.encounter_reach.lower == .8
    assert covered.target_fen == fen("e2e4")
    assert covered.candidate_id not in {item.decision_id for item in result.gaps.ordered}
    assert result.introductions.ordered == ()


def test_stale_index_and_ambiguous_evidence_fail_explicitly():
    source = snapshot(route("a", ("e2e4", "e7e5")))
    with pytest.raises(ValueError, match="stale_repertoire_index"):
        compute(source, index=replace(build_index(source), revision=2))
    first = probability(values={"e2e4": 1.})
    second = first.model_copy(update={"evidence_id": "other"})
    with pytest.raises(ValueError, match="select_one_probability_snapshot_per_position"):
        compute(source, (first, second))


def test_personal_distribution_is_retained_without_population_fusion():
    source = snapshot(route("a", ("e2e4", "e7e5")))
    population = probability(values={"e2e4": .2}, unknown=.8)
    personal_estimate = probability(values={"e2e4": .9}, unknown=.1,
                                    identity="personal", basis="personal")
    result = compute(source, (population, personal_estimate))
    edge = detail(result, "opponent_reply", key(), "e2e4")
    assert edge.encounter_reach.lower == .2
    assert edge.personal_evidence == (personal_estimate,)


def test_budget_truncation_does_not_manufacture_a_missing_edge():
    source = snapshot(route("a", ("e2e4", "e7e5")), route("b", ("d2d4", "d7d5")))
    result = compute(source, limits=AnalysisLimits(1, 20, 20))
    assert detail(result, "opponent_reply", key(), "d2d4").covered
    assert any(item.code == "route_limit" and item.route_id == "b" for item in result.diagnostics)


def test_coverage_boundary_uses_t01_scoped_dependency_and_cycle_guard():
    from test_product_contracts import boundary_errors, has_import_cycle, import_targets
    directory = ROOT / "backend/app/analysis/coverage"
    modules = {"app.analysis.coverage." + path.stem: path.read_text() for path in directory.glob("*.py")}
    assert not boundary_errors([("analysis", source, module) for module, source in modules.items()])
    assert not has_import_cycle(modules)
    forbidden = ("app.services", "app.main", "app.tasks", "app.product_composition", "app.games",
                 "app.repertoire", "app.study", "httpx", "requests", "psycopg", "sqlite3", "subprocess")
    assert not [(module, target) for module, source in modules.items()
                for target in import_targets(source, module) if target.startswith(forbidden)]


def test_wrong_own_response_still_counts_as_a_personal_decision_encounter():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "unintroduced"),))
    result = compute(source, (probability(values={"e2e4": 1.}),), personal_games=(
        personal("miss", key("e2e4"), "c7c5"), personal("other-reply", key(), "d2d4")))
    own = detail(result, "introduction", key("e2e4"), "e7e5")
    assert len(own.personal_sources) == 1 and own.personal_sources[0][2] == "miss"
    assert detail(result, "opponent_reply", key(), "e2e4").personal_sources == ()
    assert len(detail(result, "opponent_reply", key(), "d2d4").personal_sources) == 1


def test_source_refresh_invalidates_only_its_dependent_branch():
    first, second = ("e2e4", "e7e5", "g1f3", "b8c6"), ("d2d4", "d7d5", "c2c4", "e7e6")
    source = snapshot(route("a", first), route("b", second),
        mapped=(("a", 3, "a", "unintroduced"), ("b", 3, "b", "unintroduced")))
    root = probability(values={"e2e4": .5, "d2d4": .5})
    evidence_a = probability(first[:2], {"g1f3": .5}, unknown=.5)
    evidence_b = probability(second[:2], {"c2c4": .5}, unknown=.5)
    before = compute(source, (root, evidence_a, evidence_b))
    changed = evidence_b.model_copy(update={"observed_at": "2026-10-26T13:00:00Z"})
    after = compute(source, (root, evidence_a, changed))
    assert detail(before, "introduction", key(*first[:3]), "b8c6").dependency_digest == detail(
        after, "introduction", key(*first[:3]), "b8c6").dependency_digest
    assert detail(before, "introduction", key(*second[:3]), "e7e6").dependency_digest != detail(
        after, "introduction", key(*second[:3]), "e7e6").dependency_digest
    unrelated = probability(("a2a3", "a7a6"), {"b2b3": 1.}, identity="unrelated")
    assert compute(source, (root, evidence_a, evidence_b, unrelated)) == before


def test_input_rejects_incomplete_source_multiple_roots_and_nonfinite_bounds():
    source = snapshot(route("a", ("e2e4", "e7e5")))
    with pytest.raises(ValueError, match="complete_available_snapshot_required"):
        compute(source.model_copy(update={"membership": source.membership.model_copy(update={"complete": False})}))
    with pytest.raises(ValueError, match="routes_require_one_common_root_and_learner"):
        compute(source, root_fen=fen("e2e4"))
    with pytest.raises(ValueError, match="analysis_limits_require_positive_finite_bounds"):
        AnalysisLimits(1, float("inf"), 100)


def test_non_deliberate_boundary_and_unknown_introduction_are_not_silently_eligible():
    from app.contracts.product_v02.repertoire import RouteBoundary
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "unknown"),))
    source = source.model_copy(update={"boundaries": (RouteBoundary(route_id="a", terminal_ply=2, deliberate=False),)})
    result = compute(source, (probability(values={"e2e4": 1.}),))
    assert result.introductions.ordered == ()
    assert any(item.code == "non_deliberate_boundary" for item in result.diagnostics)


def test_importance_covers_introduced_and_unintroduced_current_approved_decisions():
    source = snapshot(route("a", ("e2e4", "e7e5")), route("b", ("d2d4", "d7d5")),
        mapped=(("a", 1, "a", "introduced"), ("b", 1, "b", "unintroduced")))
    games = (personal("old-a", key("e2e4"), "c7c5"), personal("old-b", key("d2d4"), "g8f6"))
    result = decision_importance(current_approved_decisions=source.decision_presentations, games=games)
    assert [item.decision.introduction for item in result] == ["introduced", "unintroduced"]
    assert [item.facts.personal_encounters for item in result] == [1, 1]
    assert [len(item.differing_response_sources) for item in result] == [1, 1]
    assert all(item.facts.reach_probability is None for item in result)


def test_importance_duplicate_recomparison_and_transposed_mapping_count_once():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "introduced"),))
    decision = source.decision_presentations[0]
    game = personal("stable-game", key("e2e4"), "c7c5")
    original = decision_importance(current_approved_decisions=(decision,), games=(game,))
    repeated = decision_importance(current_approved_decisions=(decision, decision), games=(game, game))
    assert repeated == original
    assert repeated[0].encounter_sources == (("synthetic-chesscom", "synthetic-player", "stable-game", 1),)
    # A newer comparison of the same game replaces observations, without another contribution.
    recomparison = replace(game, source_revision=2)
    refreshed = decision_importance(current_approved_decisions=(decision,), games=(game, recomparison))
    assert refreshed[0].facts.personal_encounters == 1
    assert len(refreshed[0].differing_response_sources) == 1
    assert refreshed[0].encounter_sources[0][-1] == 2


def test_historical_importance_survives_later_introduction_and_learning_without_lapse():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "unintroduced"),))
    decision = source.decision_presentations[0]
    old_miss = personal("before-introduction", key("e2e4"), "c7c5")
    before = decision_importance(current_approved_decisions=(decision,), games=(old_miss,))[0]
    # Current owner facts change after admission/reviews; game evidence does not.
    learned = decision.model_copy(update={"introduction": "introduced", "introduced_at": "2026-10-26T18:00:00Z",
                                          "presentation_revision": 2})
    after = decision_importance(current_approved_decisions=(learned,), games=(old_miss,))[0]
    assert after.encounter_sources == before.encounter_sources
    assert after.differing_response_sources == before.differing_response_sources
    assert after.facts.personal_encounters == before.facts.personal_encounters == 1
    assert after.dependency_digest != before.dependency_digest  # Fence changed current mapping/state.
    assert after.decision == learned
    assert not hasattr(after, "lapse_eligible") and not hasattr(after, "schedule")


def test_correct_personal_encounter_is_frequency_not_a_fabricated_miss():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "unintroduced"),))
    decision = source.decision_presentations[0]
    correct = personal("correct", key("e2e4"), "e7e5")
    result = decision_importance(current_approved_decisions=(decision,), games=(correct,))[0]
    assert result.facts.personal_encounters == 1
    assert result.differing_response_sources == ()
    # Repeated visits of the same decision in a game still give one encounter,
    # with one differing-game support if any visit differed from the current response.
    also_different = replace(correct, encounters=correct.encounters + (
        PersonalEncounter("rep", key("e2e4"), "c7c5"),))
    mixed = decision_importance(current_approved_decisions=(decision,), games=(also_different,))[0]
    assert mixed.facts.personal_encounters == len(mixed.differing_response_sources) == 1


def test_importance_new_source_revision_retracts_obsolete_facts_and_fences_current_mapping():
    source = snapshot(route("a", ("e2e4", "e7e5")), mapped=(("a", 1, "a", "introduced"),))
    decision = source.decision_presentations[0]
    old = personal("corrected", key("e2e4"), "c7c5")
    removed = replace(old, source_revision=2, encounters=())
    result = decision_importance(current_approved_decisions=(decision,), games=(old, removed))[0]
    assert result.facts.personal_encounters == 0 and result.differing_response_sources == ()
    unavailable = decision.model_copy(update={"available": False})
    assert decision_importance(current_approved_decisions=(unavailable,), games=(old,)) == ()
    conflict = decision.model_copy(update={"selected_response_uci": "c7c5", "decision_id": "other"})
    with pytest.raises(ValueError, match="conflicting_current_approved_responses"):
        decision_importance(current_approved_decisions=(decision, conflict), games=(old,))


def encounter_fixture(name):
    payload = json.loads((ROOT / f"contracts/product-v02/fixtures/encounters/{name}.json").read_text())["payload"]
    return GameEncounter.model_validate_json(json.dumps(payload))


def from_encounters(decision, *encounters):
    return importance_from_encounters(workspace_id="fixture", account="synthetic-player",
        current_approved_decisions=(decision,), encounters=tuple(encounters))[0]


@pytest.mark.parametrize("name,state,misses", [
    ("unintroduced-correct", "unintroduced", 0),
    ("unintroduced-missed", "unintroduced", 1),
    ("historical-missed", "introduced", 1),
])
def test_frozen_encounter_importance_preserves_both_states_and_observation(name, state, misses):
    event = encounter_fixture(name)
    result = from_encounters(event.decision, event)
    assert result.decision.introduction == state
    assert result.facts.personal_encounters == 1
    assert len(result.differing_response_sources) == misses
    assert result.source_encounters == (event,)
    assert result.source_encounters[0].key.logical_id() == event.key.logical_id()
    assert result.source_encounters[0].source.source_digest == event.source.source_digest


def test_frozen_encounter_duplicate_recomparison_retains_original_detection_after_learning():
    event = encounter_fixture("unintroduced-missed")
    learned = event.decision.model_copy(update={"introduction": "introduced", "introduced_at": "2026-10-27T12:00:00Z",
                                              "presentation_revision": 2})
    repeated = event.model_copy(update={"decision": learned, "detected_at": "2026-10-28T12:00:00Z"})
    result = from_encounters(learned, event, repeated, event)
    assert result.facts.personal_encounters == len(result.differing_response_sources) == 1
    assert result.source_encounters == (event,)
    assert from_encounters(learned, repeated, event) == result


def test_frozen_historical_miss_and_correct_play_are_not_relabelled_by_current_response():
    missed = encounter_fixture("historical-missed")
    now_matches = missed.decision.model_copy(update={"selected_response_uci": missed.played_uci,
                                                    "repertoire_revision": 2, "presentation_revision": 2})
    retained = from_encounters(now_matches, missed)
    assert len(retained.differing_response_sources) == 1
    correct = encounter_fixture("unintroduced-correct")
    now_differs = correct.decision.model_copy(update={"selected_response_uci": "d2d4", "repertoire_revision": 2})
    result = from_encounters(now_differs, correct)
    assert result.differing_response_sources == ()
    assert result.facts.personal_encounters == 1
    assert result.source_encounters[0].observation == "matched"


def test_frozen_encounter_source_correction_and_authority_fences_are_explicit():
    event = encounter_fixture("unintroduced-missed")
    source = event.source.model_copy(update={"source_revision": 2, "source_digest": "f"*64})
    revised_mapping = event.decision.model_copy(update={"introduction_game": source})
    corrected = event.model_copy(update={"source": source, "decision": revised_mapping,
                                        "played_uci": event.expected_uci, "observation": "matched"})
    result = from_encounters(revised_mapping, event, corrected)
    assert result.facts.personal_encounters == 1 and result.differing_response_sources == ()
    assert result.source_encounters == (corrected,)
    with pytest.raises(ValueError, match="encounter_importance_workspace_mismatch"):
        importance_from_encounters(workspace_id="another", account="synthetic-player",
            current_approved_decisions=(event.decision,), encounters=(event,))
    with pytest.raises(ValueError, match="conflicting_encounter_observation"):
        from_encounters(event.decision, event, event.model_copy(update={"observation": "matched", "played_uci": event.expected_uci}))


def test_frozen_encounter_keeps_exact_decision_identity_beyond_equal_positions():
    event = encounter_fixture("unintroduced-missed")
    other = event.decision.model_copy(update={"decision_id": "different-decision", "presentation_id": "another-card"})
    result = importance_from_encounters(workspace_id="fixture", account="synthetic-player",
        current_approved_decisions=(event.decision, other), encounters=(event,))
    by_id = {item.decision.decision_id: item for item in result}
    assert by_id[event.key.decision_id].facts.personal_encounters == 1
    assert by_id["different-decision"].facts.personal_encounters == 0
    assert by_id["different-decision"].source_encounters == ()
