"""Study-owned facts/effects on the current caller's transaction."""
from dataclasses import dataclass
from typing import Literal, Protocol

from app.contracts.product_v02.analysis import RankingOutput
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken, fingerprint
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


class IntroductionConflicts(Protocol):
    """Existing Study conflict owner, read on the SAME borrowed transaction.

    PostgresConflictStore.blocked implements this public capability. Admission
    already holds the shared card-edit reservation at its commit recheck, which
    serializes it with the conflict owner's synchronize command. The conflict
    owner retains scoped hold identity, persistence and restoration semantics.
    """
    def blocked(self, repertoire_id: str, card_id: str, revision: int) -> bool: ...


class IntroductionStore(Protocol):
    def state(self, day: str) -> AllocationState: ...
    def saved_ranking(self, context: RankingContext) -> RankingOutput | None: ...
    def save_ranking(self, context: RankingContext, ranking: RankingOutput) -> None: ...
    def lock_allocation(self) -> None: ...
    def receipt(self, presentation_id: str) -> Receipt | None: ...
    def record_introduction(self, receipt: Receipt, *, period_rotation_after: str | None) -> None: ...


@dataclass(frozen=True)
class IntroductionSettings:
    revision: str
    global_budget: int
    weights: tuple[tuple[str, float], ...]
    enabled: bool = False
    paused: bool = False

    def __post_init__(self):
        from .policy import validated_weights
        object.__setattr__(self, 'weights', tuple(tuple(pair) for pair in self.weights))
        validated_weights(self.weights)
        if (not self.revision or type(self.global_budget) is not int or self.global_budget < 0
                or type(self.enabled) is not bool or type(self.paused) is not bool):
            raise ValueError("explicit settings revision and nonnegative global budget required")


@dataclass(frozen=True)
class IntroductionSource:
    """Existing retained-owner authority plus its shared #483 projection.

    Outer wiring supplies these existing factual values, not new eligibility
    callbacks. Current Study rechecks actual source/card/route rows itself.
    """
    snapshot: RepertoireSnapshot
    authority: LegacyAuthorityToken

    def __post_init__(self):
        object.__setattr__(self, 'authority', LegacyAuthorityToken.model_validate_json(self.authority.model_dump_json()))
        object.__setattr__(self, 'snapshot', RepertoireSnapshot.model_validate_json(self.snapshot.model_dump_json()))
        token, membership = self.authority, self.snapshot.membership
        if (self.snapshot.variant != "standard" or token.repertoire_id not in membership.scope_repertoire_ids
                or membership.revision != token.graph_generation
                or membership.source_revision != fingerprint(token.model_dump(mode="json"))):
            raise ValueError("retained source and snapshot bindings disagree")


@dataclass(frozen=True)
class RepertoireIntroductionSource:
    """Actual repertoire-only shared snapshot; no game/capture authority token."""
    snapshot: RepertoireSnapshot

    def __post_init__(self):
        object.__setattr__(self, 'snapshot', RepertoireSnapshot.model_validate_json(self.snapshot.model_dump_json()))
        membership = self.snapshot.membership
        if (self.snapshot.variant != 'standard' or len(membership.scope_repertoire_ids) != 1
                or not membership.complete or not membership.available or membership.unavailable_reason is not None
                or membership.scope_id != 'repertoire:' + membership.scope_repertoire_ids[0]
                or membership.source_revision != membership.digest):
            raise ValueError('complete current repertoire source required')

    @property
    def repertoire_id(self):
        return self.snapshot.membership.scope_repertoire_ids[0]
