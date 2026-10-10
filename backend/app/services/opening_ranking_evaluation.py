"""Pure, identity-preserving comparisons of opening-card rankings.

Scorers are trusted, pure Python code. No storage, clock, network or scheduler
capability is supplied to them. JSON strings keep nested evidence immutable.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Protocol, Sequence


SCHEMA_VERSION = 1
REPLAY_LIMITATIONS = (
    "Exact replay covers captured ranking inputs only, not learning gains or actual queue delivery.",
    "Historical eligibility, introduction state, quota consumption and deleted routes cannot be reconstructed from current rows.",
    "Retained priority generations alone do not establish historical counterfactual accuracy.",
    "Historical probability/profile evidence is not captured by this infrastructure.",
)


class OpeningRankingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _object_json(value: str) -> str:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise OpeningRankingError("invalid_input", "Evidence and configuration must be JSON objects.")
    return canonical_json(decoded)


@dataclass(frozen=True)
class OpeningRankingCandidate:
    card_id: str
    production_rank: int
    metadata_json: str = "{}"

    def __post_init__(self):
        if not isinstance(self.card_id, str) or not self.card_id:
            raise OpeningRankingError("invalid_identity", "Each candidate needs a nonempty card ID.")
        if type(self.production_rank) is not int or self.production_rank < 1:
            raise OpeningRankingError("invalid_order", "Production ranks must be positive integers.")
        object.__setattr__(self, "metadata_json", _object_json(self.metadata_json))
        metadata = self.metadata()
        for field_name in ("memberships", "routes"):
            if field_name in metadata and (not isinstance(metadata[field_name], list)
                    or any(not isinstance(item, dict) for item in metadata[field_name])):
                raise OpeningRankingError("invalid_metadata", "Membership and route metadata must be lists of objects.")

    def metadata(self) -> dict:
        """Return a detached diagnostic projection, never mutable source evidence."""
        return json.loads(self.metadata_json)


@dataclass(frozen=True)
class OpeningRankingContext:
    as_of: str
    study_day: str
    snapshot_id: str
    source_versions_json: str = "{}"
    scorer_configuration_json: str = "{}"

    def __post_init__(self):
        instant = datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise OpeningRankingError("invalid_as_of", "As-of must include a UTC offset.")
        object.__setattr__(self, "as_of", instant.astimezone(timezone.utc).isoformat())
        date.fromisoformat(self.study_day)
        for field_name in ("source_versions_json", "scorer_configuration_json"):
            object.__setattr__(self, field_name, _object_json(getattr(self, field_name)))


@dataclass(frozen=True)
class ScoreResult:
    score: float | None
    reason: str | None = None


class OpeningCardScorer(Protocol):
    name: str
    version: str

    def score(self, candidate: OpeningRankingCandidate, context: OpeningRankingContext) -> ScoreResult: ...


@dataclass(frozen=True)
class ProductionOrderScorer:
    name: str = "production-order"
    version: str = "1"

    def score(self, candidate, context):
        return ScoreResult(-candidate.production_rank)


@dataclass(frozen=True)
class ShorterCardTestScorer:
    """Diagnostic example only; no claim of preparation or learning benefit."""
    name: str = "shorter-card-test"
    version: str = "1"

    def score(self, candidate, context):
        moves = candidate.metadata().get("moves")
        if not isinstance(moves, list) or not moves or not all(isinstance(move, str) for move in moves):
            return ScoreResult(None, "missing_or_invalid_moves")
        return ScoreResult(-len(moves))


@dataclass(frozen=True)
class RankedOpeningCandidate:
    candidate: OpeningRankingCandidate
    score: float | None
    alternative_rank: int | None
    reason: str | None


@dataclass(frozen=True)
class OpeningRanking:
    name: str
    version: str
    context: OpeningRankingContext
    rows: tuple[RankedOpeningCandidate, ...]


def _production_candidates(candidates) -> tuple[OpeningRankingCandidate, ...]:
    ordered = tuple(sorted(candidates, key=lambda candidate: candidate.production_rank))
    if len({candidate.card_id for candidate in ordered}) != len(ordered):
        raise OpeningRankingError("duplicate_identity", "A physical card must appear exactly once.")
    if [candidate.production_rank for candidate in ordered] != list(range(1, len(ordered) + 1)):
        raise OpeningRankingError("invalid_order", "Production order must cover every candidate exactly once.")
    return ordered


def rank(candidates: Sequence[OpeningRankingCandidate], context: OpeningRankingContext,
         scorer: OpeningCardScorer) -> OpeningRanking:
    ordered = _production_candidates(candidates)
    if not isinstance(scorer.name, str) or not scorer.name or not isinstance(scorer.version, str) or not scorer.version:
        raise OpeningRankingError("invalid_scorer", "Scorers require a stable nonempty name and version.")
    scored_rows = []
    for candidate in ordered:
        try:
            result = scorer.score(candidate, context)
            if not isinstance(result, ScoreResult):
                result = ScoreResult(None, "invalid_score_result")
            elif result.score is None:
                if not isinstance(result.reason, str) or not result.reason.strip():
                    result = ScoreResult(None, "missing_score_without_reason")
            elif type(result.score) not in (int, float) or not isfinite(result.score):
                result = ScoreResult(None, "invalid_nonfinite_or_nonnumeric_score")
        except Exception as error:
            # Do not expose exception messages: provider errors can include credentials.
            result = ScoreResult(None, f"scorer_error:{type(error).__name__}")
        scored_rows.append(RankedOpeningCandidate(candidate, result.score, None, result.reason))
    scored_order = sorted((row for row in scored_rows if row.score is not None),
                          key=lambda row: (-row.score, row.candidate.production_rank, row.candidate.card_id))
    alternative_ranks = {row.candidate.card_id: index for index, row in enumerate(scored_order, 1)}
    return OpeningRanking(scorer.name, scorer.version, context,
                          tuple(replace(row, alternative_rank=alternative_ranks.get(row.candidate.card_id))
                                for row in scored_rows))


def _concentration(candidates) -> dict:
    distributions = {}
    for dimension in ("repertoires", "frontier_ply", "routes"):
        counts = {}
        for candidate in candidates:
            metadata = candidate.metadata()
            if dimension == "repertoires":
                values = {membership.get("repertoire_id") for membership in metadata.get("memberships", [])}
            elif dimension == "frontier_ply":
                values = {membership.get("frontier_ply") for membership in metadata.get("memberships", [])}
            else:
                values = {canonical_json([route["repertoire_id"], route["generation"], route["line_id"]])
                          if all(route.get(key) is not None for key in ("repertoire_id", "generation", "line_id")) else None
                          for route in metadata.get("routes", [])}
            labels = {"unknown" if value is None else str(value) for value in values} or {"unknown"}
            for label in labels:
                counts[label] = counts.get(label, 0.0) + 1 / len(labels)
        shares = {label: count / len(candidates) for label, count in sorted(counts.items())} if candidates else {}
        distributions[dimension] = {"card_mass": dict(sorted(counts.items())), "shares": shares,
                                    "concentration": sum(share ** 2 for share in shares.values()) if shares else None,
                                    "undefined_reason": None if shares else "empty_selection"}
    return distributions


def compare(production_order: Sequence[OpeningRankingCandidate], alternatives: Sequence[OpeningRanking],
            top_k: Sequence[int] = (1, 5, 10)) -> dict:
    production = _production_candidates(production_order)
    if any(type(value) is not int or value < 1 for value in top_k) or len(set(top_k)) != len(top_k):
        raise OpeningRankingError("invalid_top_k", "K values must be distinct positive integers.")
    identifiers = [(alternative.name, alternative.version) for alternative in alternatives]
    if len(set(identifiers)) != len(identifiers):
        raise OpeningRankingError("duplicate_scorer", "Alternative scorer identifiers must be unique.")
    evaluations = []
    for alternative in alternatives:
        if tuple(row.candidate for row in alternative.rows) != production:
            raise OpeningRankingError("invalid_identity", "Alternative results must preserve every production candidate.")
        if alternatives and alternative.context != alternatives[0].context:
            raise OpeningRankingError("context_mismatch", "Alternatives must use identical context inputs.")
        for row in alternative.rows:
            if ((row.score is None) != (row.alternative_rank is None)
                    or (row.score is not None and (type(row.score) not in (int, float) or not isfinite(row.score)))
                    or (row.alternative_rank is not None and type(row.alternative_rank) is not int)):
                raise OpeningRankingError("invalid_order", "Alternative scores and ranks must have matching scored status.")
            if row.score is None and (not isinstance(row.reason, str) or not row.reason.strip()):
                raise OpeningRankingError("invalid_unscorable_result", "Every missing score requires an explicit unscorable reason.")
        scored = sorted((row for row in alternative.rows if row.alternative_rank is not None),
                        key=lambda row: row.alternative_rank)
        if [row.alternative_rank for row in scored] != list(range(1, len(scored) + 1)):
            raise OpeningRankingError("invalid_order", "Alternative ranks must be consecutive and unique.")
        common_production = [row for row in alternative.rows if row.alternative_rank is not None]
        common_ranks = {row.candidate.card_id: index for index, row in enumerate(common_production, 1)}
        common_count = len(scored)
        spearman = (1 - 6 * sum((common_ranks[row.candidate.card_id] - index) ** 2
                               for index, row in enumerate(scored, 1)) / (common_count * (common_count ** 2 - 1))
                    if common_count > 1 else None)
        displacements = [abs(row.candidate.production_rank - row.alternative_rank) for row in scored]
        top_metrics = {}
        for requested_k in top_k:
            effective_k = min(requested_k, len(production))
            production_top = production[:effective_k]
            alternative_top = [row.candidate for row in scored[:effective_k]]
            production_ids = [candidate.card_id for candidate in production_top]
            alternative_ids = [candidate.card_id for candidate in alternative_top]
            intersection = set(production_ids) & set(alternative_ids)
            production_weights = {card_id: 1 / index for index, card_id in enumerate(production_ids, 1)}
            alternative_weights = {card_id: 1 / index for index, card_id in enumerate(alternative_ids, 1)}
            union = set(production_ids) | set(alternative_ids)
            total_weight = sum(max(production_weights.get(card_id, 0), alternative_weights.get(card_id, 0))
                               for card_id in sorted(union))
            top_metrics[str(requested_k)] = {
                "effective_k": effective_k, "alternative_selected_count": len(alternative_top),
                "intersection_count": len(intersection),
                "overlap": len(intersection) / effective_k if effective_k else None,
                "weighted_overlap": sum(min(production_weights.get(card_id, 0), alternative_weights.get(card_id, 0))
                                        for card_id in sorted(union)) / total_weight if total_weight else None,
                "undefined_reason": None if effective_k else "empty_candidates",
                "production_only": [card_id for card_id in production_ids if card_id not in intersection],
                "alternative_only": [card_id for card_id in alternative_ids if card_id not in intersection],
                "production_concentration": _concentration(production_top),
                "alternative_concentration": _concentration(alternative_top),
            }
        evaluations.append({
            "alternative": f"{alternative.name}@{alternative.version}",
            "status": "partial" if common_count != len(production) else "complete",
            "candidate_count": len(production), "scored_count": common_count,
            "candidate_coverage": common_count / len(production) if production else None,
            "unscorable_count": len(production) - common_count,
            "unscorable_percentage": 100 * (len(production) - common_count) / len(production) if production else None,
            "coverage_undefined_reason": None if production else "empty_candidates",
            "spearman": spearman, "spearman_common_count": common_count,
            "spearman_undefined_reason": None if spearman is not None else "fewer_than_two_common_candidates",
            "mean_absolute_displacement": sum(displacements) / len(displacements) if displacements else None,
            "maximum_absolute_displacement": max(displacements) if displacements else None,
            "displacement_undefined_reason": None if displacements else "no_scored_candidates",
            "rows": [{"card_id": row.candidate.card_id, "production_rank": row.candidate.production_rank,
                      "score": row.score, "alternative_rank": row.alternative_rank,
                      "rank_delta": row.candidate.production_rank - row.alternative_rank if row.alternative_rank else None,
                      "scoring_status": "scored" if row.score is not None else "unscorable",
                      "reason": row.reason, "metadata": row.candidate.metadata()} for row in alternative.rows],
            "top_k": top_metrics,
        })
    return {"schema_version": SCHEMA_VERSION, "comparison_basis": "rankings_only_not_queue_admission",
            "evaluations": evaluations}


def snapshot_document(candidates, context, *, admission_plan=(),
                      production_order_basis="supplied_complete_order",
                      admission_plan_basis="caller_supplied_not_observed_queue_delivery") -> dict:
    ordered = _production_candidates(candidates)
    payload = {"schema_version": SCHEMA_VERSION, "as_of": context.as_of, "study_day": context.study_day,
               "source_versions": json.loads(context.source_versions_json),
               "scorer_configuration": json.loads(context.scorer_configuration_json),
               "production_order_basis": production_order_basis,
               "actual_admission_plan_basis": admission_plan_basis,
               "actual_admission_plan": list(admission_plan),
               "historical_counterfactual_supported": False, "limitations": list(REPLAY_LIMITATIONS),
               "candidates": [{"card_id": candidate.card_id, "production_rank": candidate.production_rank,
                               "metadata": candidate.metadata()} for candidate in ordered]}
    return {**payload, "snapshot_id": sha256(canonical_json(payload).encode()).hexdigest()}


def load_snapshot(document: dict) -> tuple[tuple[OpeningRankingCandidate, ...], OpeningRankingContext]:
    if document.get("schema_version") != SCHEMA_VERSION:
        raise OpeningRankingError("unsupported_snapshot", "Unsupported snapshot schema version.")
    payload = {key: value for key, value in document.items() if key != "snapshot_id"}
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    if document.get("snapshot_id") != digest:
        raise OpeningRankingError("invalid_snapshot_digest", "Snapshot content differs from its recorded digest.")
    context = OpeningRankingContext(document["as_of"], document["study_day"], digest,
                                    canonical_json(document["source_versions"]),
                                    canonical_json(document["scorer_configuration"]))
    candidates = _production_candidates(tuple(OpeningRankingCandidate(
        row["card_id"], row["production_rank"], canonical_json(row["metadata"])) for row in document["candidates"]))
    return candidates, context
