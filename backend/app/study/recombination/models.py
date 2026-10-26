"""Study-owned immutable policy values, not shared transport DTOs.

The read protocols accept frozen #482/#483 facts or a task fixture. Composition
adapts their supplied values; this module imports no sibling implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class CleanStreak:
    presentation_id: str
    presentation_revision: int
    evidence_epoch: str
    count: int = 0
    seen_event_ids: frozenset[str] = frozenset()
    completed_attempt_ids: frozenset[str] = frozenset()
    completed_review_ids: frozenset[str] = frozenset()
    tainted_attempt_ids: frozenset[str] = frozenset()
    last_event_at: datetime | None = None

    def __post_init__(self):
        if not self.presentation_id or not self.evidence_epoch or self.presentation_revision < 1:
            raise ValueError("a streak needs exact presentation/revision/epoch identity")
        if not 0 <= self.count <= 3:
            raise ValueError("clean count must be between zero and three")
        for field in ("seen_event_ids", "completed_attempt_ids", "completed_review_ids", "tainted_attempt_ids"):
            object.__setattr__(self, field, frozenset(getattr(self, field)))
        if self.last_event_at is not None and self.last_event_at.utcoffset() is None:
            raise ValueError("last_event_at must be timezone-aware")

    @property
    def eligible(self) -> bool:
        return self.count == 3


@dataclass(frozen=True)
class ReviewEvent:
    event_id: str
    presentation_id: str
    presentation_revision: int
    evidence_epoch: str
    occurred_at: datetime
    source: Literal["study", "real_game", "admitted_lapse"]
    outcome: Literal["correct", "wrong", "again", "hint", "reveal", "guided", "failure"]
    attempt_id: str | None = None
    review_id: str | None = None
    complete: bool = False
    assistance: Literal["unassisted", "assisted", "unknown"] = "unknown"

    def __post_init__(self):
        if not self.event_id or not self.presentation_id or not self.evidence_epoch:
            raise ValueError("event and presentation/epoch identities are required")
        if self.presentation_revision < 1 or self.occurred_at.utcoffset() is None:
            raise ValueError("event needs a positive revision and aware occurrence time")
        if self.source not in {"study", "real_game", "admitted_lapse"}:
            raise ValueError("unknown review source")
        if self.outcome not in {"correct", "wrong", "again", "hint", "reveal", "guided", "failure"}:
            raise ValueError("unknown review outcome")
        if self.assistance not in {"unassisted", "assisted", "unknown"}:
            raise ValueError("unknown assistance evidence")
        if self.source == "study" and not self.attempt_id:
            raise ValueError("Study events need an attempt identity")
        if self.source == "study" and self.complete and not self.review_id:
            raise ValueError("completed Study reviews need a review identity")
        if self.source == "admitted_lapse" and self.outcome != "failure":
            raise ValueError("only an explicitly admitted failure can invalidate a game streak")


class PresentationFacts(Protocol):
    """Read capability only; #482 owns the factual presentation DTO."""
    presentation_id: str
    presentation_revision: int
    repertoire_id: str
    learner_color: str
    variant: str
    starting_fen: str
    ending_fen: str
    moves: tuple[str, ...]
    introduction: str
    available: bool


class AuthoredRouteFacts(Protocol):
    """Existing authored-route fields, also exposed by #483's route index."""
    route_id: str
    repertoire_id: str
    learner_color: str
    policy_id: str
    source_revision: str
    starting_fen: str
    moves: tuple[str, ...]


@dataclass(frozen=True, order=True)
class RouteUse:
    route_id: str
    occurrence_id: str
    start_ply: int
    stop_ply: int  # Exclusive; includes the presentation's own opponent cues.

    def __post_init__(self):
        if not self.route_id or not self.occurrence_id or not 0 <= self.start_ply < self.stop_ply:
            raise ValueError("a route occurrence needs a nonempty exact ply range")


@dataclass(frozen=True)
class JoinMember:
    presentation: PresentationFacts
    streak: CleanStreak
    current_revision: int
    route_uses: tuple[RouteUse, ...]
    active_attempt: bool = False
    pending_attempt: bool = False

    def __post_init__(self):
        object.__setattr__(self, "route_uses", tuple(self.route_uses))


@dataclass(frozen=True, order=True)
class ComponentRef:
    presentation_id: str
    presentation_revision: int
    evidence_epoch: str
    route_use: RouteUse


@dataclass(frozen=True)
class ParentConnection:
    parent: ComponentRef
    child: ComponentRef


@dataclass(frozen=True)
class RouteContext:
    route: AuthoredRouteFacts
    repertoire_revision: int
    variant: str
    complete: bool
    available: bool
    parent_connections: tuple[ParentConnection, ...] = ()
    authored_routes: tuple[AuthoredRouteFacts, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "parent_connections", tuple(self.parent_connections))
        object.__setattr__(self, "authored_routes", tuple(self.authored_routes))


@dataclass(frozen=True, order=True)
class RetainedUse:
    presentation_id: str
    presentation_revision: int
    route_use: RouteUse


@dataclass(frozen=True)
class JoinPlan:
    repertoire_id: str
    repertoire_revision: int
    source_revision: str
    route_id: str
    policy_id: str
    learner_color: str
    variant: str
    route_starting_fen: str
    route_moves: tuple[str, ...]
    components: tuple[ComponentRef, ComponentRef]
    starting_fen: str
    ending_fen: str
    moves: tuple[str, ...]
    intervening_opponent_moves: tuple[str, ...]
    retained_uses: tuple[RetainedUse, ...]
    reason: Literal["recorded_parent", "authored_adjacency"]
    initial_clean_count: Literal[0] = 0
    introduced: Literal[True] = True
    introduction_slots: Literal[0] = 0


@dataclass(frozen=True, order=True)
class JoinHold:
    code: str
    presentation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class JoinDecision:
    plan: JoinPlan | None
    holds: tuple[JoinHold, ...] = ()
