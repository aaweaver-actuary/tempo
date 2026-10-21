"""Opening shadow comparisons are pure, deterministic and explicit about missing evidence."""
from dataclasses import FrozenInstanceError, replace
import json
import os
from contextlib import contextmanager, nullcontext
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
    from tempo_legacy_database import connections as database
    from tempo_legacy_database.adapters import postgres_store
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
    from tempo_legacy_database.adapters import postgres_store
    from app.study.openings import capacity_frontier as opening_capacity_frontier
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
                from zoneinfo import ZoneInfo
                instant = datetime.now(timezone.utc)
                return Cursor([{"as_of": instant, "study_day": instant.astimezone(ZoneInfo(parameters[0])).date().isoformat()}])
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
    monkeypatch.setattr(snapshot, "shared_admission_configured", lambda: True)
    monkeypatch.setattr(postgres_store, "connection", read_section)
    original_loads = json.loads
    def outside_transaction_loads(*args, **kwargs):
        assert not state["open"], "Evidence decoded inside database transaction"
        return original_loads(*args, **kwargs)
    monkeypatch.setattr(json, "loads", outside_transaction_loads)
    original_planner = opening_capacity_frontier.plan_prioritized_opening_admissions
    def outside_transaction_plan(*args, **kwargs):
        assert not state["open"], "Ranking retained database connection"
        return original_planner(*args, **kwargs)
    monkeypatch.setattr(opening_capacity_frontier, "plan_prioritized_opening_admissions", outside_transaction_plan)
    document = snapshot.capture_snapshot()
    assert len(document["candidates"]) == 1
    assert len(document["candidates"][0]["metadata"]["memberships"]) == 2
    assert document["actual_admission_plan"] == []
    assert document["production_order_basis"] == snapshot.PRODUCTION_ORDER_BASIS
    assert document["candidates"][0]["metadata"]["routes"][0]["parent_card_id"] == "parent"
    assert evaluation.load_snapshot(document)[0][0].card_id == "shared"


def test_shadow_capture_defers_foreground_work_without_database_access(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(snapshot, "shared_admission_configured", lambda: True)
    @contextmanager
    def no_connection(**options):
        raise AssertionError("Capture opened SQL despite foreground work")
        yield
    monkeypatch.setattr(snapshot.postgres_store, "connection", no_connection)
    with snapshot.activity_gate.foreground():
        with pytest.raises((evaluation.OpeningRankingError, snapshot.BackgroundAdmissionDeferred)):
            snapshot.capture_snapshot()


@pytest.mark.parametrize("study_day", [
    "", "not-a-date", "2026-02-30", "2026-10-9", "20261009", "2026-W41-5",
    "2026-10-09T00:00:00", " 2026-10-09 ", 20261009, True, date(2026, 10, 9),
])
def test_shadow_capture_rejects_malformed_study_day_before_sql(monkeypatch, study_day):
    from app.services import opening_ranking_snapshot as snapshot
    def prohibited(**options):
        raise AssertionError("Malformed study day reached PostgreSQL")
    monkeypatch.setattr(snapshot.postgres_store, "connection", prohibited)
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        snapshot.capture_snapshot(study_day=study_day, production_timezone="America/New_York")
    assert raised.value.code == "invalid_study_day"


@pytest.fixture
def authoritative_capture_boundary(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    captured = raw_capture_fixture()
    state = {"open": False, "opened": 0, "boundary_reads": 0, "raw_reads": 0,
             "as_of": datetime(2026, 10, 10, 3, tzinfo=timezone.utc), "study_day": "2026-10-09"}
    for candidate in captured["candidates"]:
        candidate["due_date"] = state["study_day"]
    class Database:
        def execute_native(self, query, parameters=()):
            assert state["open"]
            assert query == ("SELECT transaction_timestamp() AS as_of, "
                             "(transaction_timestamp() AT TIME ZONE %s)::date::text AS study_day")
            assert parameters == ("America/New_York",)
            state["boundary_reads"] += 1
            return self
        def fetchone(self):
            return {"as_of": state["as_of"], "study_day": state["study_day"]}
    @contextmanager
    def connection(**options):
        assert options == {"read_only": True, "authoritative": True, "background": True,
                           "repeatable_read": True, "pool_timeout_seconds": 0.1}
        state["open"] = True
        state["opened"] += 1
        try:
            yield Database()
        finally:
            state["open"] = False
    def raw_rows(database, query, parameters, **options):
        assert state["open"]
        state["raw_reads"] += 1
        if "SELECT step.*" in query: group = "routes"
        elif "SELECT repertoire.id" in query: group = "repertoires"
        elif "AS count" in query:
            group = "introductions"
            assert parameters == (state["study_day"],) * 2
        elif "SELECT version FROM priority_source_epoch" in query: group = "global_source"
        else:
            group = "candidates"
            assert parameters[1:] == (state["study_day"],) * 4
        rows = tuple(evaluation.canonical_json(row) for row in captured[group])
        return rows, sum(len(row.encode()) for row in rows)
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(snapshot, "shared_admission_configured", lambda: True)
    monkeypatch.setattr(snapshot.postgres_store, "connection", connection)
    monkeypatch.setattr(snapshot, "_raw_rows", raw_rows)
    return state


@pytest.mark.parametrize("application_instant", [
    datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
])
def test_shadow_capture_accepts_database_day_when_application_clock_disagrees(
        monkeypatch, authoritative_capture_boundary, application_instant):
    from app.services import opening_ranking_snapshot as snapshot
    application_clock_reads = []
    class ApplicationClock(datetime):
        @classmethod
        def now(cls, zone=None):
            application_clock_reads.append(zone)
            return application_instant.astimezone(zone) if zone else application_instant
    monkeypatch.setattr(snapshot, "datetime", ApplicationClock, raising=False)
    state = authoritative_capture_boundary
    reservations_before = snapshot.activity_gate.active_background_sections
    document = snapshot.capture_snapshot(study_day=state["study_day"], production_timezone="America/New_York")
    assert document["as_of"] == state["as_of"].isoformat()
    assert document["study_day"] == state["study_day"]
    assert document["source_versions"]["study_day_basis"] == "transaction_timestamp_in_declared_deployment_timezone"
    assert state["opened"] == state["boundary_reads"] == 1 and state["raw_reads"] == 5
    assert not state["open"] and not application_clock_reads
    assert snapshot.activity_gate.active_background_sections == reservations_before


@pytest.mark.parametrize("study_day", ["2026-10-08", "2026-10-10"])
def test_shadow_capture_rejects_noncurrent_database_day_after_boundary_read(
        monkeypatch, authoritative_capture_boundary, study_day):
    from app.services import opening_ranking_snapshot as snapshot
    class ApplicationClock(datetime):
        @classmethod
        def now(cls, zone=None):
            instant = datetime.fromisoformat(f"{study_day}T12:00:00+00:00")
            return instant.astimezone(zone) if zone else instant
    monkeypatch.setattr(snapshot, "datetime", ApplicationClock, raising=False)
    state = authoritative_capture_boundary
    reservations_before = snapshot.activity_gate.active_background_sections
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        snapshot.capture_snapshot(study_day=study_day, production_timezone="America/New_York")
    assert raised.value.code == "historical_capture_unavailable"
    assert state["opened"] == state["boundary_reads"] == 1 and state["raw_reads"] == 0
    assert not state["open"]
    assert snapshot.activity_gate.active_background_sections == reservations_before


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
    from tempo_legacy_database.adapters import postgres_store
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
    monkeypatch.setattr(snapshot, "shared_admission_configured", lambda: True)
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


def test_shadow_capture_as_of_boundary_uses_deployment_timezone_not_cli_date(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    captured = raw_capture_fixture()
    # At this instant a UTC CLI says Oct 10, while deployed New York Tempo says Oct 9.
    class UtcCliDate(date):
        @classmethod
        def today(cls): return date(2026, 10, 10)
    monkeypatch.setattr(snapshot, "date", UtcCliDate, raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(snapshot, "shared_admission_configured", lambda: True)
    class Database:
        def execute_native(self, query, parameters=()):
            assert query.startswith("SELECT transaction_timestamp")
            return self
        def fetchone(self):
            return {"as_of": datetime(2026, 10, 10, 3, tzinfo=timezone.utc), "study_day": "2026-10-09"}
    @contextmanager
    def connection(**options): yield Database()
    monkeypatch.setattr(snapshot.postgres_store, "connection", connection)
    def raw_rows(database, query, parameters, **options):
        if "SELECT step.*" in query: group = "routes"
        elif "SELECT repertoire.id" in query: group = "repertoires"
        elif "AS count" in query: group = "introductions"
        elif "SELECT version FROM priority_source_epoch" in query: group = "global_source"
        else:
            group = "candidates"
            assert parameters[1:] == ("2026-10-09",) * 4
        rows = tuple(evaluation.canonical_json(row) for row in captured[group])
        return rows, sum(len(row.encode()) for row in rows)
    monkeypatch.setattr(snapshot, "_raw_rows", raw_rows)
    document = snapshot.capture_snapshot()
    assert document["study_day"] == "2026-10-09"
    assert document["source_versions"]["production_timezone"] == "America/New_York"


def test_shadow_external_unscorable_result_without_reason_is_rejected():
    ranked = evaluation.rank(candidates(), context(), FixtureScorer())
    missing = replace(ranked.rows[0], score=None, alternative_rank=None, reason=None)
    with pytest.raises(evaluation.OpeningRankingError, match="unscorable reason"):
        evaluation.compare(candidates(), [replace(ranked, rows=(missing, *ranked.rows[1:]))])


def test_shadow_invalid_deployment_timezone_is_rejected_before_sql(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    def prohibited(**options): raise AssertionError("Timezone validation opened SQL")
    monkeypatch.setattr(snapshot.postgres_store, "connection", prohibited)
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        snapshot.capture_snapshot(production_timezone="not/a-timezone")
    assert raised.value.code == "invalid_production_timezone"


def test_shadow_capture_requires_shared_foreground_admission_before_sql(monkeypatch):
    from app.services import opening_ranking_snapshot as snapshot
    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    monkeypatch.setattr(snapshot.postgres_store, "configured", lambda: True)
    def prohibited(**options):
        raise AssertionError("Capture opened PostgreSQL without shared foreground admission")
    monkeypatch.setattr(snapshot.postgres_store, "connection", prohibited)
    with pytest.raises(evaluation.OpeningRankingError) as raised:
        snapshot.capture_snapshot()
    assert raised.value.code == "admission_unavailable"


@pytest.mark.parametrize("original_url", [None, "redis://owned-example:6379/2"])
@pytest.mark.parametrize("failure", [False, True])
def test_priority_recovery_shadow_proof_scopes_real_redis_configuration(monkeypatch, original_url, failure):
    from database.tests.check_postgres_priority_recovery import shadow_proof_admission_configuration
    if original_url is None:
        monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("TEMPO_REDIS_URL", original_url)
    expected_error = pytest.raises(RuntimeError, match="proof failure") if failure else nullcontext()
    with expected_error:
        with shadow_proof_admission_configuration():
            assert os.getenv("TEMPO_REDIS_URL") == (original_url or "redis://redis:6379/0")
            if failure:
                raise RuntimeError("proof failure")
    assert os.getenv("TEMPO_REDIS_URL") == original_url


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


@pytest.mark.parametrize("policy_id,defining_move,other_policy,other_move", [
    ("italian", "f1c4", "ruy", "f1b5"), ("ruy", "f1b5", "italian", "f1c4")])
def test_shadow_selected_policy_ranking_is_unchanged_when_other_repertoire_is_added(
    policy_id, defining_move, other_policy, other_move,
):
    from test_opening_preparedness import policy_fixture, policy_evaluation
    from app.study.openings.preparedness import marginal_card_value
    catalog = {policy_id: policy_fixture(policy_id, defining_move)}
    selected_context = replace(context(), evaluation_scope="selected_policy", policy_id=policy_id)
    def evaluate():
        readiness, effects = catalog[selected_context.policy_id]
        source = tuple(evaluation.OpeningRankingCandidate(effect.card_id, index, policy_id=policy_id)
                       for index, effect in enumerate(effects, 1))
        inputs = []
        class ConditionedScorer:
            name, version = "conditioned-marginal-fixture", "1"
            def score(self, candidate, captured_context):
                effect = next(effect for effect in effects if effect.card_id == candidate.card_id)
                value = marginal_card_value(readiness, effect, policy_id=captured_context.policy_id)
                inputs.append((candidate, captured_context, readiness, effect, value))
                return evaluation.ScoreResult(value.value)
        ranking = evaluation.rank(source, selected_context, ConditionedScorer())
        return (policy_evaluation(catalog, policy_id), inputs, ranking,
                evaluation.compare(source, [ranking], (2,)))
    baseline = evaluate()
    catalog[other_policy] = policy_fixture(other_policy, other_move)
    assert evaluate() == baseline
    # Identical physical card IDs and canonical shared prefixes remain independent.
    assert catalog[other_policy][1][0].card_id == catalog[policy_id][1][0].card_id
    catalog[other_policy] = tuple(tuple(items) * 3 for items in catalog[other_policy])
    assert evaluate() == baseline
    del catalog[other_policy]
    assert evaluate() == baseline


def test_shadow_selected_policy_preserves_opponent_deviation_probability():
    from test_opening_preparedness import policy_fixture
    from app.study.openings.preparedness import decision_reach, marginal_card_value
    readiness, effects = policy_fixture("italian", "f1c4")
    selected_context = replace(context(), evaluation_scope="selected_policy", policy_id="italian")
    source = (evaluation.OpeningRankingCandidate("card-3", 1, policy_id="italian"),)
    class DeviationScorer:
        name, version = "opponent-deviation", "1"
        def score(self, candidate, captured_context):
            return evaluation.ScoreResult(marginal_card_value(
                readiness, effects[3], policy_id=captured_context.policy_id).value)
    ranking = evaluation.rank(source, selected_context, DeviationScorer())
    assert decision_reach(readiness[1].routes, policy_id="italian").lower == 0.42
    assert decision_reach(readiness[2].routes, policy_id="italian").lower == 0.42 * 0.5
    assert ranking.rows[0].score == (0.42 * 0.5 * 0.25) * (0.8 - 0.2)
    assert ranking.rows[0].score < 0.8 - 0.2


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


def test_shadow_selected_capture_filters_shared_card_memberships_before_planning():
    from copy import deepcopy
    from app.services import opening_ranking_snapshot as snapshot
    combined = raw_capture_fixture()
    selected = deepcopy(combined)
    for group, key in (("candidates", "repertoire_id"), ("routes", "repertoire_id"), ("repertoires", "id")):
        selected[group] = [row for row in selected[group] if row[key] == "white"]
    instant = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    baseline = snapshot._project_snapshot(selected, instant, "2026-10-10", ("white",), policy_id="white")
    assert snapshot._project_snapshot(combined, instant, "2026-10-10", ("white",), policy_id="white") == baseline
    source, replay_context = evaluation.load_snapshot(baseline)
    assert source[0].policy_id == replay_context.policy_id == "white"
    assert [row["repertoire_id"] for row in source[0].metadata()["memberships"]] == ["white"]
    assert all(row["repertoire_id"] == "white" for row in source[0].metadata()["routes"])


def test_shadow_selected_capture_rejects_conflicting_selection_before_sql(monkeypatch):
    from tempo_legacy_database.adapters import postgres_store
    from app.services import opening_ranking_snapshot as snapshot
    def forbidden(**options):
        pytest.fail("Conflicting selection opened SQL")
    monkeypatch.setattr(postgres_store, "connection", forbidden)
    with pytest.raises(evaluation.OpeningRankingError, match="only its repertoire"):
        snapshot.capture_snapshot(policy_id="italian", repertoire_ids=("italian", "ruy"))


def test_shadow_selected_capture_binds_policy_to_sql_and_offline_cli(monkeypatch, tmp_path):
    from tempo_legacy_database.adapters import postgres_store
    from app.services import opening_ranking_snapshot as snapshot
    fixture = raw_capture_fixture()
    calls = []
    class Cursor:
        def fetchone(self):
            return {"as_of": datetime(2026, 10, 10, 12, tzinfo=timezone.utc), "study_day": "2026-10-10"}
    class Database:
        def execute_native(self, query, parameters):
            assert query.startswith("SELECT transaction_timestamp")
            return Cursor()
    @contextmanager
    def connection(**options):
        yield Database()
    def raw_rows(database, query, parameters, **options):
        calls.append((query, parameters))
        if query.startswith("SELECT step.*"): group = "routes"
        elif query.startswith("SELECT repertoire.id"): group = "repertoires"
        elif "AS count" in query: group = "introductions"
        elif query.startswith("SELECT version"): group = "global_source"
        else: group = "candidates"
        if group in ("candidates", "routes", "repertoires"):
            assert "linked.id=ANY(%s::text[])" in query
            assert ["white"] in parameters
        return tuple(evaluation.canonical_json(row) for row in fixture[group]), 0
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(snapshot, "shared_admission_configured", lambda: True)
    monkeypatch.setattr(postgres_store, "connection", connection)
    monkeypatch.setattr(snapshot, "_raw_rows", raw_rows)
    document = snapshot.capture_snapshot(policy_id="white")
    assert len(calls) == 5
    path = tmp_path / "policy.json"
    path.write_text(evaluation.canonical_json(document))
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, str(root / "scripts/evaluate_opening_rankings.py"),
        "compare", "--snapshot", str(path)], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["evaluation_scope"] == "selected_policy" and result["policy_id"] == "white"
