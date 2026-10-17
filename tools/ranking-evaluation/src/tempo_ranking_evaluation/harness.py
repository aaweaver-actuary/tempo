"""Offline evaluation of supplied score assumptions through the existing shadow contract.

This module does not calculate probability, readiness, effects or marginal value.
Only local JSON files and the pure #155 evaluator are used.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

from tempo_evidence_math import (
    OpeningRankingError, ScoreResult,
    canonical_json, load_snapshot,
)

from .ranking import ProductionOrderScorer, compare, rank
from .content import validate_fixture_content
from .budgets import comparison_expansion

SCENARIOS = (
    "common_italian", "common_ruy_lopez", "shared_prefixes", "rare_sidelines",
    "opponent_deviations", "multiple_cohorts", "exact_probabilities",
    "bounded_probabilities", "unknown_unassigned_mass", "newly_introduced_cards",
    "mature_cards", "recently_failed_decisions", "cumulative_prefix_cards",
)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_CORPUS_BYTES = 16 * 1024 * 1024
MAX_SCORE_BUNDLES = 16
MAX_TOP_K_VALUES = 16
MAX_REPORT_BYTES = 64 * 1024 * 1024


def _invalid(message):
    raise OpeningRankingError("invalid_corpus", message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _read_document(path):
    with Path(path).open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise OpeningRankingError("limit_exceeded", "Corpus input file exceeds 4 MiB.")
    document = json.loads(raw)
    if not isinstance(document, dict):
        _invalid("Corpus inputs must be JSON objects.")
    return document, len(raw)


@dataclass(frozen=True, init=False)
class PrecomputedScoreScorer:
    """Lookup only: all scalar assumptions must already be explicit and versioned."""

    name: str
    version: str
    _scores: tuple[tuple[str, ScoreResult], ...]

    def __init__(self, bundle):
        object.__setattr__(self, "name", bundle["name"])
        object.__setattr__(self, "version", bundle["version"])
        # Detach caller-owned input, matching the immutable snapshot boundary.
        object.__setattr__(self, "_scores", tuple((card_id, ScoreResult(result["score"], result.get("reason", "")))
                        for card_id, result in sorted(bundle["scores"].items())))

    def score(self, candidate, context):
        return dict(self._scores)[candidate.card_id]


def _bundles(candidates, context):
    configuration = json.loads(context.scorer_configuration_json)
    bundles = configuration.get("precomputed_score_bundles")
    if not isinstance(bundles, list) or not bundles:
        _invalid("Every snapshot requires nonempty precomputed_score_bundles.")
    if len(bundles) > MAX_SCORE_BUNDLES:
        raise OpeningRankingError("limit_exceeded", "Snapshots permit at most 16 score bundles.")
    identifiers = set()
    candidate_ids = {candidate.card_id for candidate in candidates}
    for bundle in bundles:
        if not isinstance(bundle, dict) or any(not _text(bundle.get(field)) for field in
                ("name", "version", "assumption", "axis", "cohort_id", "provenance")):
            _invalid("Score bundles need name/version, axis, cohort, assumption and provenance.")
        identifier = f'{bundle["name"]}@{bundle["version"]}'
        if identifier in identifiers or identifier == "production-order@1":
            _invalid("Score bundle identifiers must be unique and distinct from production-order@1.")
        identifiers.add(identifier)
        scores = bundle.get("scores")
        if not isinstance(scores, dict) or set(scores) != candidate_ids:
            _invalid("Each score bundle must preserve exactly the snapshot candidate identities.")
        for supplied in scores.values():
            if not isinstance(supplied, dict) or "score" not in supplied:
                _invalid("Each supplied result requires a score or explicit null score and reason.")
            score = supplied["score"]
            if score is None:
                if not _text(supplied.get("reason")):
                    _invalid("Unscorable candidates require a nonempty reason; unknown is not zero.")
            elif type(score) not in (int, float):
                _invalid("Precomputed scalar scores must be finite numbers, never booleans.")
            else:
                try:
                    score_is_finite = isfinite(score)
                except OverflowError:
                    score_is_finite = False
                if not score_is_finite:
                    _invalid("Precomputed scalar scores must be finite representable numbers.")
    baseline = configuration.get("baseline_bundle")
    if not _text(baseline) or baseline not in identifiers:
        _invalid("baseline_bundle must identify a supplied score bundle.")
    return sorted(bundles, key=lambda bundle: (bundle["name"], bundle["version"])), baseline


def _summary(evaluation):
    return {key: evaluation[key] for key in (
        "status", "candidate_count", "candidate_coverage", "unscorable_count",
        "spearman", "mean_absolute_displacement", "maximum_absolute_displacement", "top_k",
    )} | {
        "promoted": [row["card_id"] for row in evaluation["rows"] if (row["rank_delta"] or 0) > 0],
        "demoted": [row["card_id"] for row in evaluation["rows"] if (row["rank_delta"] or 0) < 0],
        "unscorable": [{"card_id": row["card_id"], "reason": row["reason"]}
                       for row in evaluation["rows"] if row["score"] is None],
    }


def evaluate_snapshot(document):
    """Validate the harness extension while preserving #155 snapshot/result shapes."""
    try:
        candidates, context = load_snapshot(document)
    except OpeningRankingError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as error:
        _invalid("Invalid snapshot or numeric contract: " + type(error).__name__)
    if context.evaluation_scope != "selected_policy":
        _invalid("Corpus evaluations require explicit selected-policy snapshots.")
    if not candidates:
        _invalid("Corpus snapshots must have eligible candidates.")
    for candidate in candidates:
        metadata = candidate.metadata()
        if (metadata.get("state") != "new" or metadata.get("introduced_at") is not None
                or metadata.get("archived") != 0 or metadata.get("pending_validation") != 0):
            _invalid("Rank only eligible new candidates; introduced/mature cards belong in supporting history.")
    bundles, baseline = _bundles(candidates, context)
    return candidates, context, bundles, baseline


def run_corpus(manifest_path, *, scenario_ids=None, top_k=(1, 5, 10)):
    """Validate the complete corpus first; evaluate selected scenarios without services."""
    top_k = tuple(top_k)
    if not top_k or any(type(value) is not int or not 0 < value <= 2**53-1 for value in top_k) or len(set(top_k)) != len(top_k):
        raise OpeningRankingError("invalid_top_k", "K values must be distinct positive exact integers")
    if len(top_k) > MAX_TOP_K_VALUES:
        raise OpeningRankingError("limit_exceeded", "Corpus reports permit at most 16 K values.")
    manifest_path = Path(manifest_path).resolve()
    manifest, total_bytes = _read_document(manifest_path)
    if type(manifest.get("corpus_version")) is not int or manifest["corpus_version"] != 1:
        _invalid("Unsupported corpus version; expected 1.")
    if not _text(manifest.get("provenance")):
        _invalid("Corpus provenance must explicitly describe the supplied fixtures.")
    entries = manifest.get("scenarios")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        _invalid("Corpus must declare between 1 and 32 scenarios.")
    loaded, covered = {}, set()
    # Manifest provenance is emitted too, even for a selected subset.
    projected_report_bytes = total_bytes + 4096
    for entry in entries:
        if not isinstance(entry, dict) or not _text(entry.get("id")) or entry["id"] in loaded:
            _invalid("Scenario IDs must be nonempty and unique.")
        coverage = entry.get("coverage")
        if (not isinstance(coverage, list) or not coverage
                or any(not isinstance(tag, str) or tag not in SCENARIOS for tag in coverage)
                or len(set(coverage)) != len(coverage)):
            _invalid("Scenario coverage must contain unique recognized scenario tags.")
        snapshot_file = entry.get("snapshot")
        if not _text(snapshot_file):
            _invalid("Every scenario requires a local snapshot path.")
        snapshot_path = (manifest_path.parent / snapshot_file).resolve()
        if not snapshot_path.is_relative_to(manifest_path.parent):
            _invalid("Snapshot paths must remain within the corpus directory.")
        document, document_bytes = _read_document(snapshot_path)
        total_bytes += document_bytes
        if total_bytes > MAX_CORPUS_BYTES:
            raise OpeningRankingError("limit_exceeded", "Corpus inputs exceed the 16 MiB total budget.")
        candidates, context, bundles, baseline = evaluate_snapshot(document)
        if json.loads(context.source_versions_json).get("scenario_id") != entry["id"]:
            _invalid("Manifest scenario identity must match source_versions.scenario_id.")
        projected_report_bytes += comparison_expansion(document, document_bytes,
            len(bundles) + 1, top_k, summaries=True)
        if projected_report_bytes > MAX_REPORT_BYTES:
            raise OpeningRankingError("limit_exceeded", "Corpus comparison expansion exceeds the 64 MiB budget.")
        if entry.get("snapshot_id") != context.snapshot_id:
            _invalid("Manifest snapshot identity must match the validated snapshot digest.")
        try:
            validate_fixture_content(document, coverage)
        except OpeningRankingError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            _invalid("Invalid scenario content: " + type(error).__name__)
        loaded[entry["id"]] = (entry, document, candidates, context, bundles, baseline)
        covered.update(coverage)
    if covered != set(SCENARIOS):
        _invalid("Corpus must cover all 13 required scenarios.")
    requested = list(loaded) if scenario_ids is None else list(scenario_ids)
    if (not requested or any(not isinstance(identifier, str) or identifier not in loaded for identifier in requested)
            or len(set(requested)) != len(requested)):
        _invalid("Select nonempty, unique scenario IDs declared by the corpus.")
    results = []
    for identifier in sorted(requested):
        entry, document, candidates, context, bundles, baseline = loaded[identifier]
        comparison = compare(candidates, [rank(candidates, context, ProductionOrderScorer()),
            *(rank(candidates, context, PrecomputedScoreScorer(bundle)) for bundle in bundles)],
            tuple(sorted(top_k)))
        evaluations = {evaluation["alternative"]: evaluation for evaluation in comparison["evaluations"]}
        baseline_ranks = {row["card_id"]: row["alternative_rank"] for row in evaluations[baseline]["rows"]}
        sensitivity = []
        for bundle in bundles:
            alternative = f'{bundle["name"]}@{bundle["version"]}'
            if alternative == baseline:
                continue
            changes = []
            for row in evaluations[alternative]["rows"]:
                old_rank, new_rank = baseline_ranks[row["card_id"]], row["alternative_rank"]
                if old_rank != new_rank:
                    changes.append({"card_id": row["card_id"], "baseline_rank": old_rank,
                                    "variant_rank": new_rank,
                                    "rank_delta": old_rank - new_rank if old_rank is not None and new_rank is not None else None})
            sensitivity.append({"alternative": alternative, "axis": bundle["axis"],
                                "cohort_id": bundle["cohort_id"], "assumption": bundle["assumption"],
                                "changed_candidate_count": len(changes), "changes": changes})
        results.append({"scenario_id": identifier, "coverage": sorted(entry["coverage"]),
            "snapshot_id": context.snapshot_id, "policy_id": context.policy_id,
            "as_of": context.as_of, "study_day": context.study_day,
            "source_versions": json.loads(context.source_versions_json),
            "production_order_basis": document["production_order_basis"],
            "actual_admission_plan_basis": document["actual_admission_plan_basis"],
            "actual_admission_plan": document["actual_admission_plan"],
            "baseline_bundle": baseline,
            "score_assumptions": [{key: value for key, value in bundle.items() if key != "scores"} for bundle in bundles],
            "comparison": comparison,
            "summaries": {name: _summary(evaluation) for name, evaluation in sorted(evaluations.items())},
            "sensitivity": sensitivity})
    # Canonicalize manifest ordering too, so permutations retain the same corpus identity.
    canonical_manifest = dict(manifest, scenarios=sorted(
        [dict(entry, coverage=sorted(entry["coverage"])) for entry in entries], key=lambda entry: entry["id"]))
    report = {"harness_version": 1, "corpus_id": sha256(canonical_json(canonical_manifest).encode()).hexdigest(),
        "provenance": manifest["provenance"], "declared_coverage": sorted(covered),
        "selected_coverage": sorted({tag for result in results for tag in result["coverage"]}),
        "comparison_basis": "supplied_score_assumptions_not_valuation_validation",
        "historical_counterfactual_supported": False,
        "limitations": ["Synthetic fixtures are not observed games or calibrated learning gains.",
                        "Sensitivity variants are explicit precomputed assumptions, not recomputed valuations.",
                        "Production baseline is capacity-neutral planner order, not observed queue delivery.",
                        "No held-out-game or global-allocation qualification is claimed."],
        "results": results}

    if len(canonical_json(report).encode()) + 1 > MAX_REPORT_BYTES:
        raise OpeningRankingError("limit_exceeded", "Corpus report exceeds the 64 MiB output budget.")
    return report
