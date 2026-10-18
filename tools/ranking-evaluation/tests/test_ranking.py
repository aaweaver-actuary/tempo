from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import pytest
from . import shadow as evaluation

def candidates():
    return tuple(evaluation.OpeningRankingCandidate(identifier, index, evaluation.canonical_json({
        "moves": ["e2e4"] * (4 - index),
        "memberships": [{"repertoire_id": "white", "frontier_ply": 2},
                        {"repertoire_id": "black", "frontier_ply": 4}] if identifier == "b" else
                       [{"repertoire_id": "white", "frontier_ply": 2}],
        "routes": [{"repertoire_id": "white", "generation": 1, "line_id": "shared"}],
    })) for index, identifier in enumerate("abc", 1))


def context():
    return evaluation.OpeningRankingContext("2026-10-10T12:00:00Z", "2026-10-10", "fixture")


class FixtureScorer:
    name = "fixture-scores"
    version = "1"

    def __init__(self, scores=None):
        self.scores = scores if scores is not None else {"a": 1, "b": 3, "c": 2}

    def score(self, candidate, captured_context):
        return evaluation.ScoreResult(self.scores.get(candidate.card_id), "missing_fixture_score")


def test_shadow_evaluation_performs_no_writes_and_preserves_immutable_inputs(monkeypatch):
    # Fresh-process runtime guards deny storage imports and initialization.
    source, captured_context = candidates(), context()
    before = evaluation.snapshot_document(source, captured_context)
    ranking = evaluation.rank(source, captured_context, FixtureScorer())
    assert evaluation.compare(source, [ranking])["evaluations"][0]["candidate_count"] == 3
    assert evaluation.snapshot_document(source, captured_context) == before
    with pytest.raises(FrozenInstanceError):
        source[0].card_id = "changed"
    projection = source[1].metadata()
    projection["memberships"].clear()
    assert len(source[1].metadata()["memberships"]) == 2


def test_shadow_ranking_is_deterministic_with_stable_ties_and_input_permutations():
    source = candidates()
    tied = FixtureScorer({identifier: 0 for identifier in "abc"})
    expected = evaluation.rank(source, context(), tied)
    assert evaluation.rank(tuple(reversed(source)), context(), tied) == expected
    assert [row.alternative_rank for row in expected.rows] == [1, 2, 3]


def test_shadow_compare_rejects_external_ranks_that_contradict_scores():
    source = candidates()
    external = evaluation.OpeningRanking("external", "1", context(), tuple(
        evaluation.RankedOpeningCandidate(candidate, score, supplied_rank, None)
        for candidate, score, supplied_rank in zip(source, (100, 50, 10), (3, 1, 2))
    ))
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        evaluation.compare(source, [external])
    assert raised.value.code == "invalid_order"


def test_shadow_compare_rejects_external_tied_scores_out_of_production_order():
    source = tuple(evaluation.OpeningRankingCandidate(identifier, index)
                   for index, identifier in enumerate(("z", "a", "m"), 1))
    canonical = evaluation.rank(source, context(), FixtureScorer({identifier: 50 for identifier in ("z", "a", "m")}))
    assert [row.alternative_rank for row in canonical.rows] == [1, 2, 3]
    assert evaluation.compare(source, [canonical])["evaluations"][0]["spearman"] == 1
    external = evaluation.OpeningRanking("external", "1", context(), tuple(
        evaluation.RankedOpeningCandidate(candidate, 50, supplied_rank, None)
        for candidate, supplied_rank in zip(source, (2, 1, 3))
    ))
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        evaluation.compare(source, [external])
    assert raised.value.code == "invalid_order"


def test_shadow_scored_order_uses_card_id_after_equal_score_and_production_rank():
    # Equal production ranks cannot occur in a valid candidate set. Exercise the
    # final deterministic tie break without weakening that separate validation.
    tied_rows = tuple(evaluation.RankedOpeningCandidate(
        evaluation.OpeningRankingCandidate(identifier, 1), 50, None, None,
    ) for identifier in ("z", "a"))
    ordered = sorted(tied_rows, key=evaluation._scored_candidate_order_key)
    assert [row.candidate.card_id for row in ordered] == ["a", "z"]


def test_shadow_top_k_spearman_and_rank_displacement_match_hand_calculation():
    result = evaluation.compare(candidates(), [evaluation.rank(candidates(), context(), FixtureScorer())], [2])["evaluations"][0]
    assert result["spearman"] == -0.5
    assert result["mean_absolute_displacement"] == pytest.approx(4 / 3)
    assert result["maximum_absolute_displacement"] == 2
    assert [row["rank_delta"] for row in result["rows"]] == [-2, 1, 1]
    metrics = result["top_k"]["2"]
    assert metrics["overlap"] == 0.5
    assert metrics["weighted_overlap"] == pytest.approx(0.2)
    assert metrics["production_only"] == ["a"]
    assert metrics["alternative_only"] == ["c"]


def test_shadow_production_order_baseline_yields_identity_comparison():
    result = evaluation.compare(candidates(), [evaluation.rank(candidates(), context(), evaluation.ProductionOrderScorer())])["evaluations"][0]
    assert result["spearman"] == 1
    assert result["candidate_coverage"] == 1
    assert all(row["rank_delta"] == 0 for row in result["rows"])
    assert all(metrics["overlap"] == metrics["weighted_overlap"] == 1 for metrics in result["top_k"].values())
    assert result["top_k"]["10"]["effective_k"] == 3


@pytest.mark.parametrize("missing_score", [None, float("nan"), float("inf"), True, "1"])
def test_shadow_missing_and_invalid_scores_remain_visible(missing_score):
    ranking = evaluation.rank(candidates(), context(), FixtureScorer({"a": 1, "b": missing_score, "c": 2}))
    result = evaluation.compare(candidates(), [ranking], [3])["evaluations"][0]
    assert result["status"] == "partial"
    assert result["unscorable_count"] == 1
    assert result["unscorable_percentage"] == pytest.approx(100 / 3)
    assert result["rows"][1]["alternative_rank"] is None
    assert result["rows"][1]["rank_delta"] is None
    assert result["rows"][1]["reason"]
    assert result["spearman_common_count"] == 2
    assert result["spearman"] == -1
    assert result["top_k"]["3"]["overlap"] == pytest.approx(2 / 3)


def test_shadow_shared_card_identity_and_fractional_concentration_are_preserved():
    result = evaluation.compare(candidates(), [evaluation.rank(candidates(), context(), FixtureScorer())], [2])["evaluations"][0]
    assert [row["card_id"] for row in result["rows"]] == list("abc")
    shares = result["top_k"]["2"]["alternative_concentration"]["repertoires"]
    assert shares["card_mass"] == {"black": 0.5, "white": 1.5}
    assert shares["concentration"] == 0.625
    assert sum(shares["shares"].values()) == 1


@pytest.mark.parametrize("source", [(), (evaluation.OpeningRankingCandidate("single", 1),)])
def test_shadow_empty_singleton_and_unknown_metadata_are_explicit(source):
    result = evaluation.compare(source, [evaluation.rank(source, context(), evaluation.ProductionOrderScorer())])["evaluations"][0]
    assert result["spearman"] is None
    assert result["spearman_undefined_reason"]
    if source:
        assert result["top_k"]["1"]["production_concentration"]["routes"]["shares"] == {"unknown": 1}
    else:
        assert result["candidate_coverage"] is None
        assert result["top_k"]["1"]["overlap"] is None


def test_shadow_all_unscorable_and_scorer_failures_are_not_false_success():
    class FailingScorer(FixtureScorer):
        def score(self, candidate, captured_context):
            raise RuntimeError("secret provider URL")
    result = evaluation.compare(candidates(), [evaluation.rank(candidates(), context(), FailingScorer())])["evaluations"][0]
    assert result["status"] == "partial"
    assert result["candidate_coverage"] == 0
    assert result["mean_absolute_displacement"] is None
    assert result["top_k"]["1"]["overlap"] == 0
    assert all(row["reason"] == "scorer_error:RuntimeError" for row in result["rows"])
    assert "secret" not in evaluation.canonical_json(result)


@pytest.mark.parametrize("source", [
    (evaluation.OpeningRankingCandidate("a", 1), evaluation.OpeningRankingCandidate("a", 2)),
    (evaluation.OpeningRankingCandidate("a", 2),),
    (evaluation.OpeningRankingCandidate("a", 1), evaluation.OpeningRankingCandidate("b", 1)),
])
def test_shadow_invalid_candidate_identity_or_incomplete_order_is_rejected(source):
    with pytest.raises(evaluation.OpeningRankingError):
        evaluation.rank(source, context(), FixtureScorer())


@pytest.mark.parametrize("top_k", [[0], [-1], [True], ["2"], [2, 2]])
def test_shadow_invalid_top_k_is_rejected(top_k):
    with pytest.raises(evaluation.OpeningRankingError, match="K values"):
        evaluation.compare(candidates(), [], top_k)


def test_shadow_duplicate_scorers_unknown_results_and_context_mismatch_are_rejected():
    ranked = evaluation.rank(candidates(), context(), FixtureScorer())
    with pytest.raises(evaluation.OpeningRankingError, match="identifiers"):
        evaluation.compare(candidates(), [ranked, ranked])
    wrong_rows = (replace(ranked.rows[0], candidate=evaluation.OpeningRankingCandidate("unknown", 1)), *ranked.rows[1:])
    with pytest.raises(evaluation.OpeningRankingError, match="preserve"):
        evaluation.compare(candidates(), [replace(ranked, rows=wrong_rows)])
    other = evaluation.rank(candidates(), replace(context(), study_day="2026-10-09"), evaluation.ProductionOrderScorer())
    with pytest.raises(evaluation.OpeningRankingError, match="context"):
        evaluation.compare(candidates(), [ranked, other])


def test_shadow_snapshot_roundtrip_replay_is_deterministic_and_digest_bound():
    document = evaluation.snapshot_document(candidates(), context())
    source, captured_context = evaluation.load_snapshot(json.loads(evaluation.canonical_json(document)))
    assert source == candidates()
    assert captured_context.as_of == "2026-10-10T12:00:00+00:00"
    assert not document["historical_counterfactual_supported"]
    assert document["limitations"]
    expected = evaluation.compare(source, [evaluation.rank(source, captured_context, FixtureScorer())])
    assert expected == evaluation.compare(source, [evaluation.rank(source, captured_context, FixtureScorer())])
    document["candidates"][0]["metadata"]["moves"].append("d2d4")
    with pytest.raises(evaluation.OpeningRankingError, match="digest"):
        evaluation.load_snapshot(document)


def test_shadow_shorter_card_scorer_is_explicit_example_with_missing_evidence():
    ranking = evaluation.rank(candidates(), context(), evaluation.ShorterCardTestScorer())
    assert [row.alternative_rank for row in ranking.rows] == [3, 2, 1]
    missing = evaluation.rank([evaluation.OpeningRankingCandidate("a", 1)], context(), evaluation.ShorterCardTestScorer())
    assert missing.rows[0].reason == "missing_or_invalid_moves"




def test_shadow_external_results_with_inconsistent_score_and_rank_are_rejected():
    ranked = evaluation.rank(candidates(), context(), FixtureScorer())
    wrong = replace(ranked.rows[0], score=None)
    with pytest.raises(evaluation.OpeningRankingError, match="matching scored status"):
        evaluation.compare(candidates(), [replace(ranked, rows=(wrong, *ranked.rows[1:]))])


def test_shadow_large_fixture_retains_every_physical_card_without_probability_inputs():
    source = tuple(evaluation.OpeningRankingCandidate(f"card-{index}", index) for index in range(1, 10_001))
    ranking = evaluation.rank(source, context(), evaluation.ProductionOrderScorer())
    result = evaluation.compare(source, [ranking])["evaluations"][0]
    assert result["candidate_count"] == result["scored_count"] == 10_000
    assert len(result["rows"]) == 10_000
    assert result["spearman"] == 1


def test_shadow_incomplete_membership_metadata_uses_unknown_bucket():
    source = [evaluation.OpeningRankingCandidate("a", 1, '{"memberships":[{}],"routes":[{}]}')]
    result = evaluation.compare(source, [evaluation.rank(source, context(), evaluation.ProductionOrderScorer())])["evaluations"][0]
    assert all(values["shares"] == {"unknown": 1} for values in result["top_k"]["1"]["production_concentration"].values())


def test_shadow_invalid_metadata_shape_and_naive_as_of_are_rejected():
    with pytest.raises(evaluation.OpeningRankingError, match="lists of objects"):
        evaluation.OpeningRankingCandidate("a", 1, '{"routes":{}}')
    with pytest.raises(evaluation.OpeningRankingError, match="UTC offset"):
        evaluation.OpeningRankingContext("2026-10-10T12:00:00", "2026-10-10", "fixture")


def test_shadow_external_unscorable_result_without_reason_is_rejected():
    ranked = evaluation.rank(candidates(), context(), FixtureScorer())
    missing = replace(ranked.rows[0], score=None, alternative_rank=None, reason=None)
    with pytest.raises(evaluation.OpeningRankingError, match="unscorable reason"):
        evaluation.compare(candidates(), [replace(ranked, rows=(missing, *ranked.rows[1:]))])


def test_shadow_selected_policy_rejects_foreign_candidates_before_scoring():
    selected_context = evaluation.OpeningRankingContext(
        "2026-10-10T12:00:00Z", "2026-10-10", "fixture",
        evaluation_scope="selected_policy", policy_id="italian")
    foreign_candidate = evaluation.OpeningRankingCandidate("shared", 1, policy_id="ruy")
    class NeverScorer:
        name, version = "never", "1"
        def score(self, candidate, captured_context):
            pytest.fail("Foreign candidate reached scorer")
    with pytest.raises(evaluation.OpeningRankingError, match="policy"):
        evaluation.rank((foreign_candidate,), selected_context, NeverScorer())


@pytest.mark.parametrize("scope,policy_id", [("selected_policy", None), ("selected_policy", ""),
    ("selected_policy", " "), ("selected_policy", 1), ("global_diagnostic", "italian"), ("unknown", None)])
def test_shadow_policy_context_validation_applies_to_empty_evaluations(scope, policy_id):
    with pytest.raises(evaluation.OpeningRankingError):
        replace(context(), evaluation_scope=scope, policy_id=policy_id)


@pytest.mark.parametrize("candidate_policy", [None, "ruy"])
def test_shadow_missing_or_foreign_policy_cannot_be_ranked_or_serialized(candidate_policy):
    selected_context = replace(context(), evaluation_scope="selected_policy", policy_id="italian")
    source = (evaluation.OpeningRankingCandidate("shared", 1, policy_id=candidate_policy),)
    with pytest.raises(evaluation.OpeningRankingError, match="policy"):
        evaluation.rank(source, selected_context, FixtureScorer())
    with pytest.raises(evaluation.OpeningRankingError, match="policy"):
        evaluation.snapshot_document(source, selected_context)


def test_shadow_policy_snapshot_replay_and_comparison_retain_and_fence_identity():
    selected_context = replace(context(), evaluation_scope="selected_policy", policy_id="italian")
    source = (evaluation.OpeningRankingCandidate("shared", 1, policy_id="italian"),)
    document = evaluation.snapshot_document(source, selected_context)
    replay_source, replay_context = evaluation.load_snapshot(document)
    assert replay_source == source
    assert (replay_context.evaluation_scope, replay_context.policy_id) == ("selected_policy", "italian")
    ranking = evaluation.rank(source, replay_context, FixtureScorer({"shared": 0.2}))
    comparison = evaluation.compare(source, [ranking])
    assert (comparison["evaluation_scope"], comparison["policy_id"]) == ("selected_policy", "italian")
    foreign = replace(ranking, name="foreign", context=replace(replay_context, policy_id="ruy"))
    with pytest.raises(evaluation.OpeningRankingError):
        evaluation.compare(source, [ranking, foreign])
    with pytest.raises(evaluation.OpeningRankingError):
        evaluation.rank(source, context(), FixtureScorer())
    document["policy_id"] = "ruy"
    with pytest.raises(evaluation.OpeningRankingError, match="digest"):
        evaluation.load_snapshot(document)
    empty_document = evaluation.snapshot_document((), selected_context)
    assert evaluation.load_snapshot(empty_document)[1].policy_id == "italian"


def test_shadow_legacy_snapshot_is_only_global_diagnostic():
    from hashlib import sha256
    document = evaluation.snapshot_document(candidates(), context())
    document["schema_version"] = 1
    document.pop("evaluation_scope")
    document.pop("policy_id")
    for candidate in document["candidates"]:
        candidate.pop("policy_id")
    def resign():
        payload = {key: value for key, value in document.items() if key != "snapshot_id"}
        document["snapshot_id"] = sha256(evaluation.canonical_json(payload).encode()).hexdigest()
    resign()
    source, replay_context = evaluation.load_snapshot(document)
    assert source == candidates()
    assert replay_context.evaluation_scope == "global_diagnostic" and replay_context.policy_id is None
    document["policy_id"] = "italian"
    resign()
    with pytest.raises(evaluation.OpeningRankingError, match="Legacy"):
        evaluation.load_snapshot(document)
