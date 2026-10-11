"""Independent corpus/CLI proofs; no valuation implementation is exercised."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import chess
import pytest

from app.opening_move_probability_contracts import MoveProbabilityDistribution, move_universe
from app.services import opening_ranking_evaluation as shadow
from app.services import opening_ranking_harness as harness
from app.services.opening_segmentation import opening_position_key

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests/fixtures/opening-ranking-evaluation"
MANIFEST = CORPUS / "manifest.json"
CLI = ROOT / "scripts/evaluate_opening_rankings.py"


def read(path):
    return json.loads(path.read_text())


def resign(document):
    document["snapshot_id"] = sha256(shadow.canonical_json(
        {key: value for key, value in document.items() if key != "snapshot_id"}).encode()).hexdigest()
    return document


@pytest.fixture
def copied_corpus(tmp_path):
    for source in CORPUS.glob("*.json"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    return tmp_path / "manifest.json"


def change_snapshot(manifest_path, change, scenario="italian-common"):
    manifest = read(manifest_path)
    entry = next(entry for entry in manifest["scenarios"] if entry["id"] == scenario)
    snapshot_path = manifest_path.parent / entry["snapshot"]
    document = read(snapshot_path)
    change(document)
    resign(document)
    snapshot_path.write_text(shadow.canonical_json(document))
    entry["snapshot_id"] = document["snapshot_id"]
    manifest_path.write_text(shadow.canonical_json(manifest))


def evaluation(result, name="precomputed-base@1"):
    return next(item for item in result["comparison"]["evaluations"] if item["alternative"] == name)


def test_harness_all_thirteen_scenarios_have_substantive_legal_evidence():
    manifest = read(MANIFEST)
    assert {tag for entry in manifest["scenarios"] for tag in entry["coverage"]} == set(harness.SCENARIOS)
    for entry in manifest["scenarios"]:
        document = read(CORPUS / entry["snapshot"])
        candidates, context, bundles, _ = harness.evaluate_snapshot(document)
        assert len(candidates) == 6
        source = json.loads(context.source_versions_json)
        assert source["scenario_id"] == entry["id"]
        for candidate in candidates:
            metadata = candidate.metadata()
            board = chess.Board(metadata["start_fen"])
            assert metadata["state"] == "new"
            for move in metadata["moves"]:
                assert chess.Move.from_uci(move) in board.legal_moves
                board.push_uci(move)
            for decision in metadata["decisions"]:
                board = chess.Board()
                for move in decision["prefix"]:
                    board.push_uci(move)
                assert board.turn == chess.WHITE
                assert opening_position_key(board.fen()) == decision["position_key"]
                assert chess.Move.from_uci(decision["move_uci"]) in board.legal_moves
        for evidence in source["opponent_evidence"]:
            distribution = MoveProbabilityDistribution.model_validate({
                "position": move_universe(evidence["fen"]).model_dump(),
                "moves": evidence["moves"], "unknown_mass": evidence["unknown_mass"]})
            assert distribution.position.fen_key == evidence["position_key"]
            assert evidence["outside_reply"] not in evidence["authored_replies"]
            if "unknown_unassigned_mass" in entry["coverage"]:
                assert distribution.unknown_mass == .1
                assert any(move.probability is None for move in distribution.moves)
                assert evidence["bounds"][0]["lower"] < evidence["bounds"][0]["upper"]
        if "common_italian" in entry["coverage"]:
            assert any(candidate.metadata()["moves"][4] == "f1c4" for candidate in candidates)
        if "common_ruy_lopez" in entry["coverage"]:
            assert any(candidate.metadata()["moves"][4] == "f1b5" for candidate in candidates)
        if "shared_prefixes" in entry["coverage"]:
            assert any(len(candidate.metadata()["routes"]) > 1 for candidate in candidates)
        if "rare_sidelines" in entry["coverage"]:
            assert any(candidate.metadata()["moves"][:2] == ["e2e4", "b7b6"] for candidate in candidates)
        if "opponent_deviations" in entry["coverage"]:
            assert any(candidate.metadata()["moves"][:2] == ["e2e4", "c7c5"] for candidate in candidates)
        if "multiple_cohorts" in entry["coverage"]:
            assert {bundle["cohort_id"] for bundle in bundles} == set(source["cohorts"])
            assert source["cohorts"]["rapid-1200-1400"] != source["cohorts"]["classical-1800-2000"]
            assert {evidence["cohort_id"] for evidence in source["opponent_evidence"]} == set(source["cohorts"])
            e5_values = {next(move["probability"] for move in evidence["moves"] if move["move_uci"] == "e7e5")
                         for evidence in source["opponent_evidence"] if evidence["authored_replies"] == ["b7b6", "c7c5", "e7e5"]}
            assert e5_values == {.6, .7}
        if "cumulative_prefix_cards" in entry["coverage"]:
            assert any(len(candidate.metadata()["decisions"]) >= 5 for candidate in candidates)
            supporting = source["supporting_cards"]
            assert {card["state"] for card in supporting} == {"learning", "mature"}
            assert all(card["card_id"] not in {candidate.card_id for candidate in candidates} for card in supporting)
            assert any(card["introduced_at"].startswith(context.study_day) for card in supporting)
            failed = next(card for card in supporting if "observation" in card)
            assert failed["observation"]["outcome"] == "incorrect"
            assert failed["observation"]["decision_id"] == failed["decisions"][0]["decision_id"]
            # Card-level assumptions are not copied onto each constituent decision.
            assert all("recall_assumption" not in decision for candidate in candidates for decision in candidate.metadata()["decisions"])


def test_harness_checked_snapshots_rebuild_with_real_production_planner():
    spec = importlib.util.spec_from_file_location("corpus_builder", CORPUS / "build_corpus.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for filename, rebuilt in builder.build_documents().items():
        assert rebuilt == read(CORPUS / filename)
    from app.main import _plan_prioritized_opening_admissions
    for entry in read(MANIFEST)["scenarios"]:
        document = read(CORPUS / entry["snapshot"])
        inputs = document["source_versions"]["production_planner_inputs"]
        order = _plan_prioritized_opening_admissions(inputs, {}, document["study_day"], len(inputs))
        assert [row["id"] for _, row in order] == [row["card_id"] for row in document["candidates"]]
        assert document["production_order_basis"] == "production_planner_capacity_neutral_sorted_repertoire_then_card"


def test_harness_hand_calculated_metrics_and_stable_ties():
    result = harness.run_corpus(MANIFEST, scenario_ids=["italian-common"], top_k=(2,))["results"][0]
    supplied = evaluation(result)
    assert [row["card_id"] for row in sorted(supplied["rows"], key=lambda row: row["alternative_rank"])] == [
        "italian-b", "italian-c", "italian-a", "shared-sicilian", "italian-e", "italian-f"]
    # Production breadth postpones b behind the other four distinct lines.
    assert [row["card_id"] for row in supplied["rows"]] == [
        "italian-a", "italian-c", "shared-sicilian", "italian-e", "italian-f", "italian-b"]
    assert supplied["spearman"] == pytest.approx(3 / 35)
    assert supplied["mean_absolute_displacement"] == pytest.approx(5 / 3)
    assert supplied["maximum_absolute_displacement"] == 5
    assert supplied["top_k"]["2"]["overlap"] == .5
    assert supplied["top_k"]["2"]["weighted_overlap"] == pytest.approx(.2)
    assert result["summaries"]["precomputed-base@1"]["promoted"] == ["italian-b"]
    tied = evaluation(result, "precomputed-ties@1")
    assert [row["rank_delta"] for row in tied["rows"]] == [0] * 6
    assert evaluation(result, "production-order@1")["spearman"] == 1


def test_harness_partial_bounds_unknown_mass_and_sensitivity_reversals():
    results = {result["scenario_id"]: result for result in harness.run_corpus(MANIFEST)["results"]}
    uncertainty = results["cohorts-uncertainty"]
    baseline = evaluation(uncertainty)
    assert baseline["status"] == "partial"
    assert baseline["candidate_coverage"] == pytest.approx(4 / 6)
    assert {row["card_id"] for row in baseline["rows"] if row["score"] is None} == {"italian-b", "italian-f"}
    assert all(row["alternative_rank"] is None and row["reason"] for row in baseline["rows"] if row["score"] is None)
    assert evaluation(uncertainty, "precomputed-bound-low@1")["status"] == "partial"
    assert evaluation(uncertainty, "precomputed-bound-high@1")["status"] == "partial"
    assert all(next(row for row in item["rows"] if row["card_id"] == "italian-f")["score"] is None
               for item in uncertainty["comparison"]["evaluations"] if item["alternative"] != "production-order@1")
    for family in ("cohorts-uncertainty", "cumulative-history", "rare-deviations"):
        assert all(item["changed_candidate_count"] > 0 for item in results[family]["sensitivity"])
    assert {item["axis"] for item in results["cumulative-history"]["sensitivity"]} >= {"decision_readiness", "learning_effect"}
    assert uncertainty["comparison"]["comparison_basis"] == "rankings_only_not_queue_admission"


def test_harness_other_policy_selection_does_not_change_italian_or_ruy_results():
    together = harness.run_corpus(MANIFEST, scenario_ids=["italian-common", "ruy-common"])
    for scenario in ("italian-common", "ruy-common"):
        alone = harness.run_corpus(MANIFEST, scenario_ids=[scenario])["results"][0]
        assert alone == next(result for result in together["results"] if result["scenario_id"] == scenario)
    italian = read(CORPUS / "italian-common.json")
    ruy = read(CORPUS / "ruy-common.json")
    assert italian["source_versions"]["supporting_cards"][0]["moves"] == ruy["source_versions"]["supporting_cards"][0]["moves"]
    assert italian["source_versions"]["supporting_cards"][0]["card_id"] == ruy["source_versions"]["supporting_cards"][0]["card_id"]
    shared_italian = next(candidate for candidate in italian["candidates"] if candidate["card_id"] == "shared-sicilian")
    shared_ruy = next(candidate for candidate in ruy["candidates"] if candidate["card_id"] == "shared-sicilian")
    assert shared_italian["metadata"]["moves"] == shared_ruy["metadata"]["moves"]
    assert shared_italian["policy_id"] != shared_ruy["policy_id"]
    shared = harness.run_corpus(MANIFEST, scenario_ids=["shared-prefixes"])["results"][0]
    assert evaluation(shared)["candidate_count"] == 6


def test_harness_permuted_manifest_and_selection_replay_identically(copied_corpus):
    before = harness.run_corpus(copied_corpus)
    manifest = read(copied_corpus)
    manifest["scenarios"].reverse()
    for entry in manifest["scenarios"]:
        entry["coverage"].reverse()
    copied_corpus.write_text(shadow.canonical_json(manifest))
    assert shadow.canonical_json(harness.run_corpus(copied_corpus)) == shadow.canonical_json(before)
    assert harness.run_corpus(copied_corpus, top_k=(10, 5, 1)) == before


def test_harness_permuted_snapshot_keeps_metrics_but_changes_content_identity():
    document = read(CORPUS / "italian-common.json")
    candidates, context, bundles, _ = harness.evaluate_snapshot(document)
    original = shadow.compare(candidates, [shadow.rank(candidates, context, harness.PrecomputedScoreScorer(bundle)) for bundle in bundles])
    document["candidates"].reverse()
    document["scorer_configuration"]["precomputed_score_bundles"].reverse()
    old_digest = document["snapshot_id"]
    resign(document)
    candidates, context, bundles, _ = harness.evaluate_snapshot(document)
    assert document["snapshot_id"] != old_digest  # #155 binds array order, not just semantic sets.
    assert shadow.compare(candidates, [shadow.rank(candidates, context, harness.PrecomputedScoreScorer(bundle)) for bundle in bundles]) == original


@pytest.mark.parametrize("corruption", ["foreign_policy", "missing_score", "extra_score", "unknown_zero_reason", "bool_score", "string_score", "oversized_integer_score", "duplicate_bundle", "missing_baseline", "missing_provenance", "introduced_candidate", "mature_candidate", "global_scope"])
def test_harness_rejects_invalid_snapshot_and_score_contract_before_ranking(copied_corpus, monkeypatch, corruption):
    def corrupt(document):
        bundle = document["scorer_configuration"]["precomputed_score_bundles"][0]
        card_id = document["candidates"][0]["card_id"]
        if corruption == "foreign_policy": document["candidates"][0]["policy_id"] = "foreign"
        elif corruption == "missing_score": del bundle["scores"][card_id]
        elif corruption == "extra_score": bundle["scores"]["foreign-card"] = {"score": 1}
        elif corruption == "unknown_zero_reason": bundle["scores"][card_id] = {"score": None, "reason": ""}
        elif corruption == "bool_score": bundle["scores"][card_id] = {"score": True}
        elif corruption == "string_score": bundle["scores"][card_id] = {"score": "0.1"}
        elif corruption == "oversized_integer_score": bundle["scores"][card_id] = {"score": 10 ** 400}
        elif corruption == "duplicate_bundle": document["scorer_configuration"]["precomputed_score_bundles"].append(deepcopy(bundle))
        elif corruption == "missing_baseline": document["scorer_configuration"]["baseline_bundle"] = "missing@1"
        elif corruption == "missing_provenance": bundle.pop("provenance")
        elif corruption == "introduced_candidate": document["candidates"][0]["metadata"]["introduced_at"] = "2026-10-10T01:00:00Z"
        elif corruption == "mature_candidate": document["candidates"][0]["metadata"]["state"] = "mature"
        elif corruption == "global_scope":
            document["evaluation_scope"], document["policy_id"] = "global_diagnostic", None
            for candidate in document["candidates"]: candidate["policy_id"] = None
    change_snapshot(copied_corpus, corrupt)
    def never_rank(*args, **kwargs): raise AssertionError("Invalid corpus reached ranking")
    monkeypatch.setattr(harness, "rank", never_rank)
    with pytest.raises(shadow.OpeningRankingError):
        harness.run_corpus(copied_corpus)


@pytest.mark.parametrize("corruption", ["incomplete_coverage", "unknown_tag", "duplicate_id", "foreign_path", "wrong_snapshot_id", "unsupported_version", "nonobject_entry"])
def test_harness_rejects_invalid_manifest_without_partial_report(copied_corpus, corruption):
    manifest = read(copied_corpus)
    if corruption == "incomplete_coverage": manifest["scenarios"].pop()
    elif corruption == "unknown_tag": manifest["scenarios"][0]["coverage"].append("not-a-scenario")
    elif corruption == "duplicate_id": manifest["scenarios"].append(manifest["scenarios"][0])
    elif corruption == "foreign_path": manifest["scenarios"][0]["snapshot"] = "../outside.json"
    elif corruption == "wrong_snapshot_id": manifest["scenarios"][0]["snapshot_id"] = "wrong"
    elif corruption == "unsupported_version": manifest["corpus_version"] = True
    elif corruption == "nonobject_entry": manifest["scenarios"][0] = []
    copied_corpus.write_text(shadow.canonical_json(manifest))
    with pytest.raises(shadow.OpeningRankingError): harness.run_corpus(copied_corpus)


@pytest.mark.parametrize("selection", [[], ["not-a-scenario"], ["italian-common", "italian-common"]])
def test_harness_rejects_empty_unknown_or_duplicate_selection(selection):
    with pytest.raises(shadow.OpeningRankingError): harness.run_corpus(MANIFEST, scenario_ids=selection)


@pytest.mark.parametrize("top_k", [(0,), (1, 1), (True,)])
def test_harness_invalid_top_k_is_not_a_success(top_k):
    with pytest.raises(shadow.OpeningRankingError): harness.run_corpus(MANIFEST, top_k=top_k)


def test_harness_invalid_digest_and_size_budgets(copied_corpus):
    manifest = read(copied_corpus)
    path = copied_corpus.parent / manifest["scenarios"][0]["snapshot"]
    document = read(path)
    document["candidates"][0]["metadata"]["revision"] += 1
    path.write_text(shadow.canonical_json(document))
    with pytest.raises(shadow.OpeningRankingError, match="differs"):
        harness.run_corpus(copied_corpus)
    path.write_bytes(b" " * (harness.MAX_FILE_BYTES + 1))
    with pytest.raises(shadow.OpeningRankingError, match="4 MiB"):
        harness.run_corpus(copied_corpus)


def test_harness_cli_offline_hash_seed_and_clock_independent_replay_preserves_inputs(tmp_path):
    import os
    inputs = {path: path.read_bytes() for path in CORPUS.glob("*.json")}
    bootstrap = '''
import builtins, runpy, socket, sys
original_import = builtins.__import__
blocked = {"app.main", "app.database", "app.postgres_store", "app.services.opening_ranking_snapshot", "app.services.opening_card_valuation", "psycopg", "redis", "sqlite3", "chess", "httpx", "celery"}
def guarded_import(name, *args, **kwargs):
    if name in blocked: raise AssertionError("Offline runner imported service dependency: " + name)
    return original_import(name, *args, **kwargs)
def no_network(*args, **kwargs): raise AssertionError("Offline runner attempted network")
builtins.__import__ = guarded_import
socket.socket.connect = no_network
socket.create_connection = no_network
sys.argv = [sys.argv[1], "corpus"]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    outputs = []
    for seed, tz in (("1", "UTC"), ("22", "Pacific/Honolulu"), ("random", "Asia/Tokyo")):
        environment = dict(os.environ, PYTHONHASHSEED=seed, TZ=tz,
            TEMPO_DATABASE_WRITE_URL="postgresql://invalid/never", TEMPO_REDIS_URL="redis://invalid")
        completed = subprocess.run([sys.executable, "-S", "-c", bootstrap, str(CLI)],
            env=environment, capture_output=True, text=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert all(path.read_bytes() == before for path, before in inputs.items())
    existing = tmp_path / "existing.json"
    existing.write_text("preserved")
    failed = subprocess.run([sys.executable, str(CLI), "corpus", "--output", str(existing)], capture_output=True, text=True, timeout=20)
    assert failed.returncode == 2 and failed.stdout == ""
    assert existing.read_text() == "preserved"
    assert json.loads(failed.stderr)["status"] == "error"
    bad_output = tmp_path / "should-not-exist.json"
    failed = subprocess.run([sys.executable, str(CLI), "corpus", "--scenario", "missing", "--output", str(bad_output)], capture_output=True, text=True, timeout=20)
    assert failed.returncode == 2 and not bad_output.exists()
    assert json.loads(failed.stderr)["code"] == "invalid_corpus"


def test_harness_precomputed_lookup_detaches_supplied_results():
    document = read(CORPUS / "italian-common.json")
    candidates, context, bundles, _ = harness.evaluate_snapshot(document)
    scorer = harness.PrecomputedScoreScorer(bundles[0])
    before = scorer.score(candidates[0], context)
    bundles[0]["scores"][candidates[0].card_id]["score"] = 999
    assert scorer.score(candidates[0], context) == before
