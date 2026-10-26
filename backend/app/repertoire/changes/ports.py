"""Repertoire-owned audit facts and narrow borrowed-transaction capabilities.

These are internal report/storage values, not replacement shared proposal DTOs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ContextManager, Protocol

from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire.position_index import Diagnostic, PositionKey


@dataclass(frozen=True, order=True)
class AffectedOccurrence:
    repertoire_id: str
    route_id: str
    ply: int
    response_uci: str
    presentation_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResponseConflict:
    position: PositionKey
    responses_uci: tuple[str, ...]
    occurrences: tuple[AffectedOccurrence, ...]


@dataclass(frozen=True)
class ConflictReport:
    snapshot_id: str
    source_revision: str
    revision: int
    digest: str
    conflicts: tuple[ResponseConflict, ...]
    diagnostics: tuple[Diagnostic, ...]
    available: bool
    source_complete: bool

    @property
    def clean(self) -> bool:
        return self.available and self.source_complete and not self.conflicts and not self.diagnostics


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason: str | None
    suspend_without_lapse: bool


class ChangeError(ValueError):
    """Stable owner error code, translated to transport status at the edge."""


class AuditReader(Protocol):
    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot: ...
