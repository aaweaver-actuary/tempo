"""Coverage-owned read capabilities; composition supplies the repertoire index."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.contracts.product_v02.repertoire import DecisionPresentation


class IndexedOccurrence(Protocol):
    route_id: str
    ply: int
    bindings: tuple[DecisionPresentation, ...]
    route_complete: bool


class IndexedPosition(Protocol):
    conflict: bool
    occurrences: tuple[IndexedOccurrence, ...]


class RepertoireIndex(Protocol):
    """Structural subset of #483 PositionIndex; never imports its implementation."""
    snapshot_id: str
    source_revision: str
    revision: int
    digest: str

    def lookup(self, repertoire_id: str, fen: str,
               variant: str = "standard") -> IndexedPosition | None: ...


@dataclass(frozen=True)
class AnalysisLimits:
    """Finite authored-route prefixes, never unbounded graph walks."""
    max_routes: int
    max_plies: int
    max_positions: int

    def __post_init__(self):
        for value, maximum in ((self.max_routes, 10000), (self.max_plies, 1000),
                               (self.max_positions, 100000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("analysis_limits_require_positive_finite_bounds")
