"""Pure, identity-preserving comparisons of opening-card rankings.

Scorers are trusted, pure Python code. No storage, clock, network or scheduler
capability is supplied to them. JSON strings keep nested evidence immutable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Protocol, Sequence
from ._json import CanonicalObject, validate_json
from ._json import immutable_dataclass as dataclass


SCHEMA_VERSION = 2
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
    validate_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _object_json(value: str) -> str:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise OpeningRankingError("invalid_input", "Evidence and configuration must be JSON objects.")
    return canonical_json(decoded)


def validate_policy_id(policy_id):
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise OpeningRankingError("invalid_policy", "Selected policy requires a nonempty policy identity.")


@dataclass(frozen=True)
class OpeningRankingCandidate:
    card_id: str
    production_rank: int
    metadata_json: str = "{}"
    policy_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self):
        if not isinstance(self.card_id, str) or not self.card_id:
            raise OpeningRankingError("invalid_identity", "Each candidate needs a nonempty card ID.")
        if type(self.production_rank) is not int or self.production_rank < 1:
            raise OpeningRankingError("invalid_order", "Production ranks must be positive integers.")
        if self.policy_id is not None:
            validate_policy_id(self.policy_id)
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
    evaluation_scope: str = field(default="global_diagnostic", kw_only=True)
    policy_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self):
        if self.evaluation_scope not in ("global_diagnostic", "selected_policy"):
            raise OpeningRankingError("invalid_scope", "Choose global_diagnostic or selected_policy scope.")
        if self.evaluation_scope == "selected_policy":
            validate_policy_id(self.policy_id)
        elif self.policy_id is not None:
            raise OpeningRankingError("invalid_policy", "Global diagnostics cannot imply a selected policy.")
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


def _validate_policy_candidates(candidates, context):
    for candidate in candidates:
        if candidate.policy_id != context.policy_id:
            raise OpeningRankingError("policy_mismatch", "Candidate policy differs from the evaluation policy.")


def snapshot_document(candidates, context, *, admission_plan=(),
                      production_order_basis="supplied_complete_order",
                      admission_plan_basis="caller_supplied_not_observed_queue_delivery") -> dict:
    ordered = _production_candidates(candidates)
    _validate_policy_candidates(ordered, context)
    payload = {"schema_version": SCHEMA_VERSION, "evaluation_scope": context.evaluation_scope,
               "policy_id": context.policy_id, "as_of": context.as_of, "study_day": context.study_day,
               "source_versions": json.loads(context.source_versions_json),
               "scorer_configuration": json.loads(context.scorer_configuration_json),
               "production_order_basis": production_order_basis,
               "actual_admission_plan_basis": admission_plan_basis,
               "actual_admission_plan": list(admission_plan),
               "historical_counterfactual_supported": False, "limitations": list(REPLAY_LIMITATIONS),
               "candidates": [{"card_id": candidate.card_id, "production_rank": candidate.production_rank,
                               "metadata": candidate.metadata(), "policy_id": candidate.policy_id} for candidate in ordered]}
    return {**payload, "snapshot_id": sha256(canonical_json(payload).encode()).hexdigest()}


def load_snapshot(document: dict) -> tuple[tuple[OpeningRankingCandidate, ...], OpeningRankingContext]:
    if type(document.get("schema_version")) is not int or document["schema_version"] not in (1, SCHEMA_VERSION):
        raise OpeningRankingError("unsupported_snapshot", "Unsupported snapshot schema version.")
    payload = {key: value for key, value in document.items() if key != "snapshot_id"}
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    if document.get("snapshot_id") != digest:
        raise OpeningRankingError("invalid_snapshot_digest", "Snapshot content differs from its recorded digest.")
    legacy = document["schema_version"] == 1
    if legacy and ("evaluation_scope" in document or "policy_id" in document
                   or any("policy_id" in row for row in document["candidates"])):
        raise OpeningRankingError("invalid_policy", "Legacy snapshots are global diagnostics only.")
    context = OpeningRankingContext(document["as_of"], document["study_day"], digest,
                                    canonical_json(document["source_versions"]),
                                    canonical_json(document["scorer_configuration"]),
                                    evaluation_scope="global_diagnostic" if legacy else document["evaluation_scope"],
                                    policy_id=None if legacy else document["policy_id"])
    candidates = _production_candidates(tuple(OpeningRankingCandidate(
        row["card_id"], row["production_rank"], canonical_json(row["metadata"]), policy_id=None if legacy else row["policy_id"])
        for row in document["candidates"]))
    _validate_policy_candidates(candidates, context)
    return candidates, context

SCORER_CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class RankingSnapshot:
    """Digest-bound immutable wire document; detached projections on access."""
    document_json: str

    def __post_init__(self):
        document = json.loads(self.document_json)
        load_snapshot(document)
        object.__setattr__(self, 'document_json', canonical_json(document))

    @classmethod
    def from_document(cls, document):
        return cls(canonical_json(document))

    def document(self):
        return json.loads(self.document_json)

    @property
    def candidates(self):
        return load_snapshot(self.document())[0]

    @property
    def context(self):
        return load_snapshot(self.document())[1]


@dataclass(frozen=True, slots=True)
class ScoreBundle:
    name: str
    version: str
    axis: str
    cohort_id: str
    assumption: str
    provenance: str
    scores: CanonicalObject

    def __post_init__(self):
        from ._json import identifier, number
        for name in ('name', 'version', 'axis', 'cohort_id', 'assumption', 'provenance'):
            identifier(getattr(self, name))
        object.__setattr__(self, 'scores', CanonicalObject(self.scores))
        for result in self.scores.values():
            if result.get('score') is None:
                identifier(result.get('reason'))
            else:
                number(result['score'])
