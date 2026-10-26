"""Policy inputs/results and the read-only subset of #496 retrieval.

Shared Proposal, engine, probability and card values remain #482 contracts.
Composition adapts retrieve(index, RetrievalRequest(...)) to SimilarityPort.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.contracts.product_v02.analysis import (
    DisplayedCardChange, EngineContext, EngineEvidence, Proposal,
)
from app.contracts.product_v02.repertoire import DecisionPresentation


class ComparisonFacts(Protocol):
    score: int
    similarities: tuple[str, ...]
    differences: tuple[str, ...]


class SourceFacts(Protocol):
    repertoire_id: str
    route_id: str
    ply: int
    source_revision: str
    full_fen: str
    starting_fen: str
    history_uci: tuple[str, ...]


class SupportFacts(Protocol):
    position_key: str
    selected_response_uci: str
    comparison: ComparisonFacts
    occurrences: tuple[SourceFacts, ...]


class CandidateFacts(Protocol):
    move_uci: str
    exact_continuation: bool
    supporting_positions: tuple[SupportFacts, ...]


class RetrievalRequestFacts(Protocol):
    repertoire_id: str
    target_fen: str
    learner_color: str
    variant: str


class RetrievalFacts(Protocol):
    request: RetrievalRequestFacts
    revision: int
    digest: str
    status: str
    target_position_key: str | None
    exact_reason: str
    source_complete: bool
    candidates: tuple[CandidateFacts, ...]
    truncation: tuple[str, ...]


class SimilarityPort(Protocol):
    def retrieve(self, *, repertoire_id: str, target_fen: str, learner_color: str,
                 variant: str, max_candidates: int) -> RetrievalFacts: ...


@dataclass(frozen=True)
class ProposalTarget:
    proposal_id: str
    repertoire_id: str
    source_revision: int
    source_digest: str
    route_id: str
    context: EngineContext
    kind: Literal["missing_reply", "covered_outlier", "common_blunder"] = "missing_reply"
    previous_response_uci: str | None = None
    existing_card: DecisionPresentation | None = None
    # Existing-card edits require the owner's exact displayed executable, never
    # a guessed replacement for a potentially longer physical card.
    displayed_card_changes: tuple[DisplayedCardChange, ...] = ()
    proposal_revision: int = 1


@dataclass(frozen=True)
class ProposalLimits:
    max_candidates: int = 32
    max_targets: int = 8
    min_outlier_support: int = 2

    def __post_init__(self):
        for name, maximum in (("max_candidates", 256), ("max_targets", 32),
                              ("min_outlier_support", 256)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"invalid_{name}")


@dataclass(frozen=True)
class ExistingCardCandidate:
    presentation: DecisionPresentation
    intent: Literal["practice_candidate", "introduction_candidate"]
    selected_response_uci: str
    source_revision: int
    source_digest: str
    explanation: str
    engine_evidence: tuple[EngineEvidence, ...]
    # This value is neither a Study command nor a record of a completed action.


@dataclass(frozen=True)
class ProposalResult:
    status: Literal["proposal", "existing_card", "no_sound_candidate", "inconclusive"]
    reason: str
    selected_move_uci: str | None = None
    proposal: Proposal | None = None
    existing_card: ExistingCardCandidate | None = None
    quality: tuple[object, ...] = ()
    engine_evidence: tuple[EngineEvidence, ...] = ()
    truncation: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposalBatch:
    results: tuple[ProposalResult, ...]
    examined_targets: int
    truncated: bool
