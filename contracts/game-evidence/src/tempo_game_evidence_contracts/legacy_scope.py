"""Explicit legacy-local capture proof; not a Content or wire-version fallback.

The backend owns exhaustion and legal source matching. These types bind its
bounded capture facts; a producer's self-reported EOF never grants acceptance.
"""
import hashlib
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ._wire import Identifier, Model, Timestamp, UciMove
from .legacy import canonical, fingerprint

class LegacyIntegrityCertificate(Model):
    integrity_status: Literal["clean", "needs_repair"]
    scan_status: Literal["idle"]
    scan_generation: Identifier
    checked_at: Timestamp
    scan_completed_sources: int = Field(ge=0)
    scan_total_sources: int = Field(ge=0)
    scan_error: None
    graph_task_generation: int = Field(ge=1)
    graph_task_state: Literal["complete"]
    scan_task_id: Identifier
    scan_task_generation: int = Field(ge=1)
    scan_task_state: Literal["complete"]

    @model_validator(mode="after")
    def published_run(self):
        if (self.scan_completed_sources != self.scan_total_sources
                or self.scan_generation != f"{self.scan_task_id}:{self.scan_task_generation}"):
            raise ValueError("legacy_integrity_run_incomplete")
        return self


class LegacyStreamManifest(Model):
    name: Literal["repertoires", "repertoire_lines", "cards", "repertoire_cards", "graphs"]
    expected_rows: int = Field(ge=0)
    visited_rows: int = Field(ge=0)
    highwater: list[str | int] = Field(max_length=2)
    last_key: list[str | int] = Field(max_length=2)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    exhausted: Literal[True]

    @field_validator("exhausted", mode="before")
    @classmethod
    def literal_exhaustion(cls, value):
        if value is not True:
            raise ValueError("legacy_stream_not_exhausted")
        return value

    @model_validator(mode="after")
    def complete(self):
        if self.expected_rows != self.visited_rows or self.highwater != self.last_key:
            raise ValueError("legacy_stream_not_exhausted")
        if bool(self.highwater) != bool(self.expected_rows):
            raise ValueError("legacy_stream_highwater_missing")
        return self


class LegacyDecisionScope(Model):
    game_id: Identifier
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurred_at: Timestamp
    learner_color: Literal["white", "black"]
    starting_position_key: str = Field(min_length=1, max_length=120)
    moves: list[UciMove] = Field(min_length=1, max_length=1000)
    decision_plies: list[int] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_decisions(self):
        if (self.decision_plies != sorted(set(self.decision_plies))
                or any(not 0 <= ply < len(self.moves) for ply in self.decision_plies)):
            raise ValueError("legacy_decision_scope_invalid")
        return self


class LegacyScopedCatalogueProof(Model):
    schema_version: Literal[1]
    digest_algorithm: Literal["legacy-row-stream-v2"]
    authority_id: str = Field(pattern=r"^legacy-graph:[0-9a-f]{64}$")
    catalogue_generation: int = Field(ge=0)
    integrity_certificate: LegacyIntegrityCertificate
    scope: LegacyDecisionScope
    streams: list[LegacyStreamManifest] = Field(min_length=5, max_length=5)
    candidate_route_ids: list[Identifier] = Field(max_length=64)
    # Complete policy context along the actual ordered prefix, including routes
    # that ended/diverged before a retained decision. No FEN-only reentry.
    prefix_policies: list[list[Identifier]] = Field(min_length=1, max_length=1001)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("legacy_scope_version_requires_integer")
        return value

    @model_validator(mode="after")
    def coherent_scope(self):
        if {item.name for item in self.streams} != {
                "repertoires", "repertoire_lines", "cards", "repertoire_cards", "graphs"}:
            raise ValueError("legacy_capture_stream_inventory")
        if len(set(self.candidate_route_ids)) != len(self.candidate_route_ids):
            raise ValueError("legacy_duplicate_scope_candidate")
        if len(self.prefix_policies) != max(self.scope.decision_plies) + 1:
            raise ValueError("legacy_prefix_policy_coverage")
        if any(items != sorted(set(items)) for items in self.prefix_policies):
            raise ValueError("legacy_prefix_policy_inventory")
        if any(not set(after) <= set(before) for before, after in zip(self.prefix_policies, self.prefix_policies[1:])):
            raise ValueError("legacy_prefix_policy_reentry_forbidden")
        if sum(map(len, self.prefix_policies)) > 10000:
            raise ValueError("legacy_prefix_policy_budget")
        return self

    def stream(self, name):
        return next(item for item in self.streams if item.name == name)

    def source_identity_digests(self):
        # No game ID, decision scope, retained-candidate list or capture time.
        # Full physical inventory/membership streams are retained evidence, but
        # availability is separately fenced. Reusable repertoire authority is
        # authored source + graph; physical ID/revision/executable stay explicit
        # in the accepted decision and shared current-miss card projection.
        return dict(source_digest=fingerprint({"algorithm": self.digest_algorithm,
            "streams": {item.name: item.digest for item in self.streams
                        if item.name in {"repertoires", "repertoire_lines"}}}),
            catalogue_digest=self.stream("repertoire_lines").digest,
            graph_digest=self.stream("graphs").digest)

    def authority_anchor(self, policy_id):
        return next((ply for ply, policies in enumerate(self.prefix_policies)
                     if policies == [policy_id]), None)


class LegacyRowDigest:
    """Length-framed, caller-ordered rows; explicit algorithm differs from v1."""
    def __init__(self):
        self.hash = hashlib.sha256(b"tempo:legacy-row-stream-v2\0")

    def add(self, row):
        data = canonical(row).encode()
        self.hash.update(len(data).to_bytes(8, "big"))
        self.hash.update(data)
        return len(data)

    def hexdigest(self):
        return self.hash.hexdigest()
