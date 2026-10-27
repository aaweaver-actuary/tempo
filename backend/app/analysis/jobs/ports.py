"""Analysis publication facts and short, Analysis-owned storage operations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.analysis.coverage.ranking import CoverageRanking
from app.contracts.product_v02 import Digest, Identifier, Revision, Value


class AnalysisFence(Value):
    repertoire_id: Identifier
    repertoire_revision: Revision
    source_revision: Identifier
    snapshot_digest: Digest
    settings_revision: Revision
    settings_digest: Digest
    personal_revision: Revision
    evidence_revision: Revision


@dataclass(frozen=True)
class PreparedAnalysis:
    fence: AnalysisFence
    status: Literal["ready", "setup_required", "unavailable", "cancelled"]
    ranking: CoverageRanking | None = None
    reason: str | None = None

    def __post_init__(self):
        if (self.status == "ready") != (self.ranking is not None):
            raise ValueError("only_ready_analysis_has_ranking")
        if (self.status == "ready") != (self.reason is None):
            raise ValueError("unavailable_analysis_requires_reason")


class AnalysisPublications(Protocol):
    """Analysis adapter owns its transaction; no provider runs inside it.

    publish_if_current compares *all* fence fields against current owner facts
    and atomically stores a result only on equality. Identical replay is inert.
    False means obsolete work, never permission to overwrite the last result.
    Source/settings/personal/evidence generations must be updated by their
    owning commands before scheduling replacement work. No Study tables here.
    """
    def current(self, repertoire_id: str) -> AnalysisFence: ...
    def publish_if_current(self, result: PreparedAnalysis) -> bool: ...


class Cancellation(Protocol):
    def is_set(self) -> bool: ...
    async def wait(self) -> object: ...
