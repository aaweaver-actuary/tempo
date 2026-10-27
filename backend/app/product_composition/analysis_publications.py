"""Opt-in source-owner binding for Analysis publication; no SQL or activation."""
from __future__ import annotations

from typing import Protocol

from app.analysis.jobs.interfaces import AnalysisFence
from app.repertoire.interfaces import RepertoireSnapshot
from tempo_legacy_database.adapters.analysis.publications import (
    AnalysisPublicationError, PublicationDatabase,
)
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import reserve_current_snapshot


class ReservedAnalysisGenerations(Protocol):
    """Actual Analysis-owned facts, reserved through this SAME caller commit.

    Acquire settings/personal/evidence reservations after the source owner, in
    their established order. Return the existing AnalysisFence bound to the
    supplied snapshot. Every generation writer must honor these reservations.
    This is a required capability, with no cached/static/default implementation.
    It owns no connection, commit, provider or Study effect.
    """

    def __call__(self, database: PublicationDatabase, snapshot: RepertoireSnapshot) -> AnalysisFence: ...


class CurrentRepertoireAnalysisFence:
    """Compose #483's actual source reservation with required Analysis facts.

    Both current reads and publication checks reserve the actual source, so
    separate owner reads cannot combine facts from different source versions.
    The owning transaction must already be a Read Committed writer and release
    all reservations on every exit. Source contention propagates to optional
    Analysis's existing unavailable path; no retry/wait loop is introduced.
    Native source concurrency and real Analysis generation capabilities remain
    separate qualification requirements; this class supplies no substitute.
    """

    def __init__(self, generations: ReservedAnalysisGenerations):
        self._generations = generations

    def __call__(self, database: PublicationDatabase, repertoire_id: str, *, reserve: bool) -> AnalysisFence:
        snapshot = reserve_current_snapshot(database, repertoire_id)
        fence = self._generations(database, snapshot)
        member = snapshot.membership
        if not isinstance(fence, AnalysisFence):
            raise AnalysisPublicationError("analysis_current_fence_invalid")
        if (member.scope_repertoire_ids != (repertoire_id,)
                or (fence.repertoire_id, fence.repertoire_revision, fence.source_revision, fence.snapshot_digest)
                != (repertoire_id, member.revision, member.source_revision, member.digest)):
            raise AnalysisPublicationError("analysis_current_fence_source_mismatch")
        return fence
