"""Public read-only repertoire API; no application startup or concrete storage."""
from .position_index import (
    Diagnostic, PositionIndex, PositionKey, PositionNode,
    RouteAdjacency, RouteOccurrence, build_index, position_key,
)

__all__ = [
    "Diagnostic", "PositionIndex", "PositionKey", "PositionNode",
    "RouteAdjacency", "RouteOccurrence", "build_index", "position_key",
]

from .snapshot import CapturedSnapshotAdapter, project_capture

__all__ += ["CapturedSnapshotAdapter", "project_capture"]
