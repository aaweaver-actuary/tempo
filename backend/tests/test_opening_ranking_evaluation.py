"""Opening shadow comparisons are pure, deterministic and explicit about missing evidence."""
from dataclasses import FrozenInstanceError, replace
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from app.services import opening_ranking_evaluation as evaluation


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
    from app import database, postgres_store
    def prohibited(*args, **kwargs):
        raise AssertionError("Pure evaluation attempted storage access")
    monkeypatch.setattr(database, "connection", prohibited)
    monkeypatch.setattr(postgres_store, "connection", prohibited)
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


def raw_capture_fixture():
    rows = []
    for repertoire_id in ("black", "white"):
        rows.append({"id": "shared", "repertoire_id": repertoire_id, "moves_json": '["e2e4"]',
                     "due_date": date.today().isoformat(), "revision": 2, "start_fen": "fixture", "state": "new",
                     "introduced_at": None, "archived": 0, "pending_validation": 0, "repertoire_name": repertoire_id,
                     "priority_generation": 4, "scoring_version": 2, "priority_updated_at": "2026-10-10T00:00:00Z",
                     "priority_evidence_json": "{}", "completion_mass": 0.7, "frontier_reach": 0.3,
                     "gameplay_priority_reason": None, "priority_date": None, "priority_score": 0.5,
                     "completed_line_ids_json": '[]', "frontier_decisions_json": '[]'})
    return {"candidates": rows, "routes": [{"card_id": "shared", "repertoire_id": "white", "generation": 1,
            "line_id": "transposed", "decision_index": 2, "parent_card_id": "parent"}],
            "repertoires": [{"id": repertoire_id, "daily_limit": 0} for repertoire_id in ("black", "white")],
            "introductions": [], "global_source": [{"version": 8}]}


def test_shadow_capture_is_readonly_and_decodes_and_plans_after_connection_closure(monkeypatch):
    from app import main, postgres_store
    from app.services import opening_ranking_snapshot as snapshot
    state = {"open": False}
    captured = raw_capture_fixture()
    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
        def fetchone(self): return self.rows[0]
    class ReadOnlyDatabase:
        def execute_native(self, query, parameters=()):
            assert state["open"] and query.startswith("SELECT")
            if query.startswith("SELECT transaction_timestamp"):
                return Cursor([{"as_of": datetime.now(timezone.utc)}])
            if "SELECT step.*" in query: group = "routes"
            elif "SELECT repertoire.id" in query: group = "repertoires"
            elif "AS count" in query: group = "introductions"
            elif "SELECT version FROM priority_source_epoch" in query: group = "global_source"
            else:
                group = "candidates"
                assert "ORDER BY linked.id,c.id" in query
            serialized = [evaluation.canonical_json(row) for row in captured[group]]
            total = sum(len(raw.encode()) for raw in serialized)
            return Cursor([{"row_json": raw, "total_bytes": total} for raw in serialized])
    @contextmanager
    def read_section(**options):
        assert options == {"read_only": True, "authoritative": True, "background": True,
                           "repeatable_read": True, "pool_timeout_seconds": 0.1}
        state["open"] = True
        try: yield ReadOnlyDatabase()
        finally: state["open"] = False
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(postgres_store, "connection", read_section)
    original_loads = json.loads
    def outside_transaction_loads(*args, **kwargs):
        assert not state["open"], "Evidence decoded inside database transaction"
        return original_loads(*args, **kwargs)
    monkeypatch.setattr(json, "loads", outside_transaction_loads)
    original_planner = main._plan_prioritized_opening_admissions
    def outside_transaction_plan(*args):
        assert not state["open"], "Ranking retained database connection"
        return original_planner(*args)
    monkeypatch.setattr(main, "_plan_prioritized_opening_admissions", outside_transaction_plan)
    document = snapshot.capture_snapshot()
    assert len(document["candidates"]) == 1
    assert len(document["candidates"][0]["metadata"]["memberships"]) == 2
    assert document["actual_admission_plan"] == []
    assert document["production_order_basis"] == snapshot.PRODUCTION_ORDER_BASIS
    assert document["candidates"][0]["metadata"]["routes"][0]["parent_card_id"] == "parent"
    assert evaluation.load_snapshot(document)[0][0].card_id == "shared"


def test_shadow_capture_rejects_backdating_and_foreground_work_without_database_access(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    @contextmanager
    def no_connection(**options):
        raise AssertionError("Capture opened SQL despite foreground work")
        yield
    monkeypatch.setattr(snapshot.postgres_store, "connection", no_connection)
    with pytest.raises(evaluation.OpeningRankingError, match="current production study day"):
        snapshot.capture_snapshot(study_day="1900-01-01")
    with snapshot.activity_gate.foreground():
        with pytest.raises((evaluation.OpeningRankingError, snapshot.BackgroundAdmissionDeferred)):
            snapshot.capture_snapshot()


@pytest.mark.parametrize("rows", [
    [{"row_json": None, "total_bytes": 101}],
    [{"row_json": "{}", "total_bytes": 2}, {"row_json": "{}", "total_bytes": 2}],
])
def test_shadow_capture_exceeds_budget_without_decoding_or_partial_export(rows):
    from app.services import opening_ranking_snapshot as snapshot
    class ReadOnlyDatabase:
        def execute_native(self, query, parameters): return self
        def fetchall(self): return rows
    with pytest.raises(evaluation.OpeningRankingError, match="budget"):
        snapshot._raw_rows(ReadOnlyDatabase(), "SELECT 1", (), limit=1, remaining_bytes=100)


def test_shadow_cli_replay_is_offline_deterministic_across_hash_seeds_and_protects_evidence(tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(evaluation.canonical_json(evaluation.snapshot_document(candidates(), context())))
    script = Path(__file__).resolve().parents[2] / "scripts/evaluate_opening_rankings.py"
    command = [sys.executable, str(script), "compare", "--snapshot", str(snapshot_path),
               "--scorer", "production-order", "--scorer", "shorter-card-test", "--top-k", "2"]
    outputs = []
    for hash_seed in ("1", "123"):
        environment = {**os.environ, "PYTHONHASHSEED": hash_seed,
                       "TEMPO_DATABASE_WRITE_URL": "postgresql://invalid:1/unreachable",
                       "TEMPO_REDIS_URL": "redis://invalid:1/0"}
        completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    result = json.loads(outputs[0])
    assert result["comparison_basis"] == "rankings_only_not_queue_admission"
    assert result["snapshot_id"]
    assert result["evaluations"][0]["top_k"]["2"]["overlap"] == 1
    protected = subprocess.run([*command, "--output", str(snapshot_path)], capture_output=True, text=True, timeout=10)
    assert protected.returncode == 2
    assert json.loads(protected.stderr)["code"] == "invalid_input_or_output"
    evaluation.load_snapshot(json.loads(snapshot_path.read_text()))


def test_shadow_documented_example_matches_cli_and_hand_calculated_metrics():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, str(root / "scripts/evaluate_opening_rankings.py"), "compare",
                               "--snapshot", str(root / "docs/examples/opening-ranking-snapshot.json"), "--top-k", "2"],
                              capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    expected = json.loads((root / "docs/examples/opening-ranking-comparison.json").read_text())
    assert json.loads(completed.stdout) == expected
    baseline, alternative = expected["evaluations"]
    assert baseline["spearman"] == 1
    assert alternative["spearman"] == -0.5
    assert alternative["top_k"]["2"]["weighted_overlap"] == pytest.approx(0.2)


def test_shadow_missing_priority_and_route_evidence_remain_unavailable():
    from app.services import opening_ranking_snapshot as snapshot
    captured = raw_capture_fixture()
    captured["routes"] = []
    for row in captured["candidates"]:
        for field in ("priority_score", "frontier_decisions_json", "completed_line_ids_json", "priority_evidence_json"):
            row[field] = None
    document = snapshot._project_snapshot(captured, datetime.now(timezone.utc), date.today().isoformat(), ())
    metadata = document["candidates"][0]["metadata"]
    assert metadata["route_evidence_status"] == "unavailable_current_published_routes"
    assert metadata["memberships"][0]["priority_score"] is None
    assert metadata["memberships"][0]["frontier_ply"] is None
    assert "priority_evidence:missing" in metadata["memberships"][0]["unavailable_evidence"]


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


def test_shadow_pool_timeout_is_opt_in_and_preserves_existing_connection_defaults(monkeypatch):
    from app import postgres_store
    acquired_options = []
    statements = []
    class RawConnection:
        def execute(self, query, parameters=()): statements.append(query)
    class Pool:
        @contextmanager
        def connection(self, **options):
            acquired_options.append(options)
            yield RawConnection()
    monkeypatch.setattr(postgres_store, "_pool", lambda _read_only: Pool())
    with postgres_store.connection():
        pass
    with postgres_store.connection(read_only=True, authoritative=True, repeatable_read=True, pool_timeout_seconds=0.1):
        pass
    assert acquired_options == [{}, {"timeout": 0.1}]
    assert statements == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", "SET TRANSACTION READ ONLY"]


def test_shadow_capture_pool_failure_is_actionable_and_releases_background_reservation(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    @contextmanager
    def failed_acquisition(**options):
        assert options["pool_timeout_seconds"] == 0.1
        raise snapshot.PoolTimeout("secret database URL")
        yield
    monkeypatch.setattr(snapshot.postgres_store, "connection", failed_acquisition)
    before = snapshot.activity_gate.active_background_sections
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        snapshot.capture_snapshot()
    assert raised.value.code == "capture_unavailable"
    assert "secret" not in str(raised.value)
    assert snapshot.activity_gate.active_background_sections == before
