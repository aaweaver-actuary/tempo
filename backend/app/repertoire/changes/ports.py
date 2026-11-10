"""Current repertoire source conflict validation for editing and import."""
from __future__ import annotations
from dataclasses import dataclass
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
        return (
            self.available
            and self.source_complete
            and not self.conflicts
            and not self.diagnostics
        )


class ChangeError(ValueError):
    """Stable owner error code, translated to transport status at the edge."""
