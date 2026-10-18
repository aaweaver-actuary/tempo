"""Trusted Learning facts and immutable module boundary records."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken


@dataclass(frozen=True)
class AcceptedDecision:
    card_id: str
    card_revision: int
    decision_id: str
    publication_id: str
    publication_generation: int
    position_key: str
    accepted_moves: tuple[str, ...]
    workspace_id: str
    active: bool = True
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class LegacyAcceptedDecision:
    """Owner-rechecked physical legacy facts, never an activated publication."""
    card_id: str
    card_revision: int
    decision_id: str
    authority: LegacyAuthorityToken
    position_key: str
    accepted_moves: tuple[str, ...]
    workspace_id: str
    route_id: str
    route_ply: int
    exercise_ply: int
    executable_digest: str
    authored_moves: tuple[str, ...]
    physical_binding_complete: bool
    introduced: bool
    introduced_before_game: bool = False
    active: bool = True
    aliases: tuple[str, ...] = ()


def accepted_storage_identity(accepted: AcceptedDecision | LegacyAcceptedDecision) -> tuple[str, int]:
    """Compatibility with the module's historical publication-named columns."""
    if isinstance(accepted, LegacyAcceptedDecision):
        return accepted.authority.storage_identity()
    return accepted.publication_id, accepted.publication_generation


class LegacyAuthority(Protocol):
    """Backend supplies current facts under its source/card locks."""
    def validate_legacy_source(self, delivery) -> None: ...
    def legacy_decision(self, card_id: str, decision_id: str,
                        authority: LegacyAuthorityToken, comparison) -> LegacyAcceptedDecision | None: ...


@dataclass(frozen=True)
class CurrentMissProjection:
    """Owner-provided current physical-card/authority columns, not SQL text.

    The owner must project a coherent, rechecked authority identity. This only
    adapts a read shape; it grants no acceptance or settlement permission.
    """
    card_id_column: str = "id"
    card_revision_column: str = "revision"
    authority_id_column: str = "publication_id"
    authority_generation_column: str = "publication_generation"


class ContentAuthority(Protocol):
    """Caller loads/fences these facts in the same Learning transaction."""
    def decision(self, card_id: str, decision_id: str, publication_id: str,
                 publication_generation: int) -> AcceptedDecision | None: ...


@dataclass(frozen=True)
class ReceiveContext:
    workspace_id: str
    game_id: str
    game_revision: int
    evidence_id: str
    evidence_revision: int
    received_at: datetime
    content: ContentAuthority | LegacyAuthority


@dataclass(frozen=True)
class Contribution:
    obligation_id: str
    obligation_revision: int
    incident_id: str
    card_id: str
    card_revision: int
    publication_id: str
    publication_generation: int
    occurred_at: datetime | None
    kind: str
    payload_json: str


@dataclass(frozen=True)
class Candidate:
    card_id: str
    card_revision: int
    publication_id: str
    publication_generation: int
    route_id: str
    introduced: bool
    outstanding_miss: bool
    content_valid: bool = True
    current_revision: bool = True
    retired: bool = False
    deleted: bool = False
    buried: bool = False
    progression_allowed: bool = True
    remaining_allowance: int = 0
    protected: bool = False


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str | None
    path: Literal["introduction", "review"]


@dataclass(frozen=True)
class AdmissionProposal:
    card_id: str
    card_revision: int
    publication_id: str
    publication_generation: int
    route_id: str
    path: Literal["introduction", "review"]
    priority: Literal["real_game_miss", "ordinary"]


@dataclass(frozen=True)
class AdmissionResult:
    status: Literal["applied", "deferred", "rejected"]
    reason: str | None = None
    queue_entry_id: int | None = None


class AdmissionPort(Protocol):
    """A's sole writer rechecks facts, quota, dedup and head under its locks."""
    def apply(self, proposal: AdmissionProposal) -> AdmissionResult: ...


@dataclass(frozen=True)
class AttemptAuthority:
    attempt_id: str
    card_id: str
    card_revision: int
    publication_id: str
    publication_generation: int
    issued_at: datetime


@dataclass(frozen=True)
class ReviewOutcome:
    review_id: str
    attempt: AttemptAuthority
    reviewed_at: datetime
    outcome: Literal["correct", "again"]
    guided: bool = False
    failed: bool = False
    invalidated: bool = False
    source_kind: str = "study"


@dataclass(frozen=True)
class WorkItem:
    card_id: str
    accepted_sequence: int
    state: Literal["pending", "deferred", "admitted"]
    reason: str | None


@dataclass(frozen=True)
class WorkPage:
    items: tuple[WorkItem, ...]
    cursor: str
    has_more: bool


class RemediationError(Exception):
    def __init__(self, status: str, reason: str):
        self.status, self.reason = status, reason
        super().__init__(reason)
