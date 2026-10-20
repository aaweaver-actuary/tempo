"""Discovery acceptance facts and capabilities borrowing the caller's transaction."""

from dataclasses import dataclass
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


class DiscoveryAcceptanceStore(Protocol):
    def has_matching_intent(self, choice: DiscoveryChoice) -> bool: ...
    def lock_matching_intent(self, choice: DiscoveryChoice) -> MatchingDiscoveryIntent | None: ...
    def lock_opportunity_scope(self, opportunity_id: str) -> LockedDiscoveryOpportunity | None: ...
    def task_state(self, intent_id: str) -> str | None: ...
    def requeue_intent(self, intent_id: str) -> None: ...
    def persist_intent_and_task(self, intent: NewDiscoveryIntent) -> None: ...
