"""Discovery acceptance facts and capabilities borrowing the caller's transaction."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


@dataclass(frozen=True)
class DiscoveryChoice:
    opportunity_id: str
    selected_move_uci: str
    evidence_fingerprint: str


@dataclass(frozen=True)
class MatchingDiscoveryIntent:
    id: str
    state: str


@dataclass(frozen=True)
class DiscoveryScope:
    canonical_prefix_revision: int = 0
    canonical_scope_source_revision: int | None = None
    canonical_scope_preview_id: str | None = None
    game_scope_generation: int = 0


@dataclass(frozen=True)
class DiscoveryOpportunity:
    repertoire_id: str
    status: str
    card_id: str | None
    evidence_fingerprint: str
    scope: DiscoveryScope


@dataclass(frozen=True)
class LockedDiscoveryOpportunity:
    opportunity: DiscoveryOpportunity
    current_scope: DiscoveryScope
    restricted_to_prefix: bool


@dataclass(frozen=True)
class NewDiscoveryIntent:
    id: str
    choice: DiscoveryChoice
    repertoire_id: str
    starting_fen: str
    preview_moves_uci: tuple[str, ...]
    recommendation: dict[str, Any]
    line_id: str
    accepted_at: str


class DiscoveryAcceptanceReason(str, Enum):
    UNAVAILABLE = "unavailable"
    ALREADY_SAVED = "already_saved"
    EVIDENCE_CHANGED = "evidence_changed"
    INVALID_CHOICE = "invalid_choice"


class DiscoveryAcceptanceStore(Protocol):
    """All write capabilities borrow one caller-owned transaction and never commit.

    Locks survive until that caller commits or rolls back. Implementations must
    not split the intent, opportunity marker and durable task across transactions.
    """

    def has_matching_intent(self, choice: DiscoveryChoice) -> bool:
        """Read committed matching intent without taking a write lock."""
        ...

    def lock_matching_intent(self, choice: DiscoveryChoice) -> MatchingDiscoveryIntent | None:
        """Serialize existing-choice replay for the caller transaction's lifetime."""
        ...

    def lock_opportunity_scope(self, opportunity_id: str) -> LockedDiscoveryOpportunity | None:
        """Lock repertoire metadata, game scope, then opportunity in that order.

        Return coherent observed/current scope facts held stable for the caller's
        transaction, including the subsequent matching-intent duplicate recheck.
        """
        ...

    def task_state(self, intent_id: str) -> str | None: ...

    def requeue_intent(self, intent_id: str) -> None:
        """Durably schedule preparation; this does not admit or issue a Study card."""
        ...

    def persist_intent_and_task(self, intent: NewDiscoveryIntent) -> None:
        """Persist intent, opportunity preparation marker and durable task atomically.

        All effects use the borrowed transaction, so the gateway's receipt either
        commits with them or rolls back with them. No internal commit is allowed.
        """
        ...
