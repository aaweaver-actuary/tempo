"""Pure shared values for position-owned Study.

Current RepertoireSnapshotPort remains authoritative.
PositionKey.position is ALREADY normalized by tempo_evidence_math.opening_position_key.
No database, HTTP, schedulers or Repertoire imports belong in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import chess
from ._identity import opening_position_key
import re

UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def required(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field}_required")


@dataclass(frozen=True, order=True)
class PositionKey:
    repertoire_id: str
    position: str  # 4 canonical chess FEN fields, including legal en passant
    variant: Literal["standard"] = "standard"

    def __post_init__(self) -> None:
        required(self.repertoire_id, "repertoire_id")
        if self.variant != "standard" or not isinstance(self.position, str) or len(self.position.split()) != 4:
            raise ValueError("invalid_canonical_position_key")
        try:
            board = chess.Board(self.position)
            if not board.is_valid() or opening_position_key(self.position) != self.position:
                raise ValueError("invalid_canonical_position_key")
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid_canonical_position_key") from exc


@dataclass(frozen=True)
class PositionDefinition:
    key: PositionKey
    expected_uci: str
    policy_version: str

    def __post_init__(self) -> None:
        if not UCI_RE.fullmatch(self.expected_uci):
            raise ValueError("invalid_selected_response_uci")
        required(self.policy_version, "policy_version")


@dataclass(frozen=True)
class SourceCertificate:
    """Detached source-owner claims; fields alone do not establish authority.

    A trusted owner supplies and fences the source facts; callers cannot certify
    completeness or currentness merely by constructing this value.
    """
    snapshot_id: str
    source_revision: str
    source_digest: str
    complete: bool
    current_integrity: bool

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "source_revision", "source_digest"):
            required(getattr(self, name), name)


@dataclass(frozen=True)
class DecisionOccurrence:
    ply: int  # 0 based ply, just BEFORE learner response
    definition: PositionDefinition
    fen: str  # full FEN at decision, from validated authored graph

    def __post_init__(self) -> None:
        if self.ply < 0:
            raise ValueError("negative_ply")
        required(self.fen, "fen")


@dataclass(frozen=True)
class RouteOption:
    """Detached route claims, subject to source-owner and issuance fences."""
    route_id: str
    start_fen: str
    moves_uci: tuple[str, ...]
    learner_color: Literal["white", "black"]
    decisions: tuple[DecisionOccurrence, ...]
    certificate: SourceCertificate
    legal_verified: bool  # supplied by Repertoire index owner, NOT inferred by Study

    def __post_init__(self) -> None:
        required(self.route_id, "route_id")
        required(self.start_fen, "start_fen")
        if not self.legal_verified or not self.certificate.complete or not self.certificate.current_integrity:
            raise ValueError("route_not_certified")
        if self.learner_color not in {"white", "black"}:
            raise ValueError("invalid_learner_color")
        if len(self.moves_uci) > 1000 or len(self.decisions) > 20:
            raise ValueError("route_exceeds_reference_bound")
        if any(not UCI_RE.fullmatch(m) for m in self.moves_uci):
            raise ValueError("invalid_route_uci")
        if tuple(sorted(d.ply for d in self.decisions)) != tuple(d.ply for d in self.decisions):
            raise ValueError("unordered_decisions")
        if len({d.ply for d in self.decisions}) != len(self.decisions):
            raise ValueError("duplicate_decision_occurrence")
        if any(d.ply >= len(self.moves_uci) or self.moves_uci[d.ply] != d.definition.expected_uci
               for d in self.decisions):
            raise ValueError("route_decision_mismatch")


@dataclass(frozen=True)
class DueAtIssuance:
    """Captured entitlement; positive credit also requires transaction recheck."""
    definition: PositionDefinition
    due: bool
    study_day: date
    owner_epoch: str
    position_revision: int
    issued_at: datetime

    def __post_init__(self) -> None:
        required(self.owner_epoch, "owner_epoch")
        if self.position_revision < 1 or self.issued_at.utcoffset() is None:
            raise ValueError("invalid_due_witness")


@dataclass(frozen=True)
class FirstResponse:
    attempt_id: str
    index: int
    definition: PositionDefinition
    actual_uci: str | None
    assistance_before: bool
    observed_at: datetime
    event_id: str
    manual_failure: bool = False

    def __post_init__(self) -> None:
        required(self.attempt_id, "attempt_id")
        required(self.event_id, "event_id")
        if self.index < 0 or self.observed_at.utcoffset() is None:
            raise ValueError("invalid_first_response")
        if self.actual_uci is not None and not UCI_RE.fullmatch(self.actual_uci):
            raise ValueError("invalid_actual_uci")
        if self.actual_uci is None and not self.manual_failure:
            raise ValueError("first_response_requires_action_or_manual_failure")

    @property
    def correct(self) -> bool:
        return not self.manual_failure and self.actual_uci == self.definition.expected_uci


@dataclass(frozen=True)
class AcceptedEvidence:
    """Already saved by the real owner; this value alone cannot persist anything."""
    first_response: FirstResponse
    saved: bool
    disposition: Literal["expected", "wrong", "illegal", "manual_failure", "alternate", "unverified"]
    terminal_complete: bool
    aggregate_review_saved: bool
    owner_epoch: str
    source_digest: str
    accepted_at: datetime
    accepted_study_day: date  # Owner day at this grade acceptance, persisted on replay.

    def __post_init__(self) -> None:
        if not isinstance(self.accepted_study_day, date) or isinstance(self.accepted_study_day, datetime):
            raise ValueError("accepted_study_day_required")
        if self.accepted_at.utcoffset() is None:
            raise ValueError("accepted_at_aware_required")


Grade = Literal["good", "again", "observe", "pending"]


@dataclass(frozen=True)
class GradeProposal:
    position: PositionKey
    policy_version: str
    event_id: str
    study_day: date
    owner_epoch: str
    issued_position_revision: int
    grade: Grade
    reason: str
    # Original occurrence time remains audit evidence; FSRS updates at acceptance time.
    observed_at: datetime
    accepted_at: datetime


@dataclass(frozen=True)
class Hold:
    reason: str
    detail: str = ""
