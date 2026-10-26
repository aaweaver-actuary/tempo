"""Study-owned facts/effects on the current caller's transaction."""
from dataclasses import dataclass
from typing import Literal, Protocol

from app.contracts.product_v02.analysis import RankingOutput
from .policy import Candidate, RankingContext, RequiredWork


@dataclass(frozen=True)
class OwnerFacts:
    context: RankingContext
    candidates: tuple[Candidate, ...]
    global_budget: int
    weights: tuple[tuple[str, float], ...]
    introduced_today: tuple[tuple[str, str], ...] = ()
    introduced: frozenset[str] = frozenset()
    mandatory: tuple[RequiredWork, ...] = ()
    enabled: bool = False
    paused: bool = False
    study_only: bool = False


@dataclass(frozen=True)
class Receipt:
    presentation_id: str
    day: str
    repertoire_id: str


@dataclass(frozen=True)
class AllocationState:
    rotation_after: str | None = None
    period_started: bool = False
    period_rotation_after: str | None = None
    charges: tuple[Receipt, ...] = ()


class IntroductionOwner(Protocol):
    """Existing owner supplies current facts and its sole canonical queue writer.

    commit caller holds normal owner card/day fences before allocation lock.
    introduce revalidates exact definition/membership/playable route and controls
    in the same borrowed transaction; it only appends, preserving issued work.
    It must not commit independently. Unknown eligibility is closed.
    """
    def facts(self, day: str) -> OwnerFacts: ...
    def introduce(self, day: str, candidate: Candidate) -> Literal["introduced", "already_introduced", "stale"]: ...


class IntroductionStore(Protocol):
    def state(self, day: str) -> AllocationState: ...
    def saved_ranking(self, context: RankingContext) -> RankingOutput | None: ...
    def save_ranking(self, context: RankingContext, ranking: RankingOutput) -> None: ...
    def lock_allocation(self) -> None: ...
    def receipt(self, presentation_id: str) -> Receipt | None: ...
    def record_introduction(self, receipt: Receipt, *, period_rotation_after: str | None) -> None: ...
