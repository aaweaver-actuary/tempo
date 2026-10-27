"""Supported repertoire lookup/projection surface; no storage or runtime startup.

Canonical T01 values retain their existing identity during interface migration.
Only composition supplies owner reads to the pure snapshot adapters.
"""
from app.contracts.product_v02.repertoire import (
    DecisionPresentation,
    IntroductionGameScope,
    RepertoireSnapshot,
    RepertoireSnapshotPort,
    RouteBoundary,
)

from ..position_index import (
    Diagnostic,
    PositionIndex,
    PositionKey,
    PositionNode,
    RouteAdjacency,
    RouteOccurrence,
    build_index,
    position_key,
)
from ..snapshot import (
    CapturedSnapshotAdapter,
    CurrentSnapshotAdapter,
    PublishedSnapshotAdapter,
    project_capture,
    project_published_repertoire,
    project_repertoire,
)

__all__ = [
    "CapturedSnapshotAdapter",
    "CurrentSnapshotAdapter",
    "DecisionPresentation",
    "Diagnostic",
    "IntroductionGameScope",
    "PositionIndex",
    "PositionKey",
    "PositionNode",
    "PublishedSnapshotAdapter",
    "RepertoireSnapshot",
    "RepertoireSnapshotPort",
    "RouteAdjacency",
    "RouteBoundary",
    "RouteOccurrence",
    "build_index",
    "position_key",
    "project_capture",
    "project_published_repertoire",
    "project_repertoire",
]
