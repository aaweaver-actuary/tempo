"""Private T22 storage and retained-Study capabilities; caller owns the transaction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.contracts.product_v02.presentation import Presentation, PresentationChangeReceipt
from .models import CleanStreak, JoinMember, ReviewEvent, RouteContext, RouteUse


@dataclass(frozen=True)
class Obligation:
    """An unresolved #487 identity/deadline, never a new lapse or settlement."""
    logical_id: str
    decision_id: str
    available_by: datetime

    def __post_init__(self):
        if not self.logical_id or not self.decision_id or self.available_by.utcoffset() is None:
            raise ValueError("original obligation identity and aware deadline required")


@dataclass(frozen=True)
class CurrentRoute:
    context: RouteContext
    members: tuple[JoinMember, ...]
    due: tuple[tuple[str, datetime], ...]
    obligations: tuple[Obligation, ...] = ()


@dataclass(frozen=True)
class NewPresentation:
    presentation: Presentation
    evidence_epoch: str
    starting_fen: str
    ending_fen: str
    moves: tuple[str, ...]
    route_use: RouteUse
    due_at: datetime


@dataclass(frozen=True)
class PresentationChange:
    command_id: str
    kind: str
    route_id: str
    source_revision: str
    retired_uses: tuple[tuple[str, RouteUse], ...]
    results: tuple[NewPresentation, ...]
    obligations: tuple[Obligation, ...]


class StudyBoundary(Protocol):
    """Integration must bind actual current Study rows in this borrowed writer.

    try_current fences current route/source/content/conflicts/memberships and ALL
    issued/pending attempt facts. It takes existing card locks in sorted order
    before day locks, without waiting when invoked from a saved-review boundary.
    Missing authority returns None; it must not trust a producer snapshot. Apply
    initializes fresh owned scheduling state (no invented reviews/FSRS copy),
    changes only this route's queue projections and preserves other uses. Both
    calls share the store's transaction. No Games or optional producer is needed.
    """
    def try_current(self, route_id: str) -> CurrentRoute | None: ...
    def saved_review(self, event: ReviewEvent) -> bool: ...
    def identity_available(self, presentation_id: str) -> bool: ...
    def apply(self, change: PresentationChange) -> None: ...


class RecombinationStore(Protocol):
    def try_lock_route(self, route_id: str) -> bool: ...
    def try_lock_evidence(self, identities: tuple[CleanStreak, ...]) -> bool: ...
    def streak(self, identity: CleanStreak) -> CleanStreak: ...
    def save_streak(self, state: CleanStreak) -> None: ...
    def event(self, event_id: str) -> ReviewEvent | None: ...
    def save_event(self, event: ReviewEvent) -> None: ...
    def recent_completed(self, identity: CleanStreak) -> tuple[ReviewEvent, ...]: ...
    def receipt(self, command_id: str) -> tuple[str, PresentationChangeReceipt] | None: ...
    def presentation_exists(self, presentation_id: str) -> bool: ...
    def save_change(self, change: PresentationChange, digest: str, receipt: PresentationChangeReceipt) -> None: ...
