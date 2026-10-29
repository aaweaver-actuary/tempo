"""Explicit Game Evidence snapshots and pure retained-observation preparation."""
from ..legacy_authority import LegacyComparisonSnapshot, PhysicalCard
from ..legacy_payloads import preview_legacy_observation
from ..legacy_scoped_authority import LegacyScopedComparisonSnapshot
from ..snapshot import ComparisonSnapshot, ComparisonSnapshotV2, parse_snapshot

__all__ = (
    "ComparisonSnapshot", "ComparisonSnapshotV2", "LegacyComparisonSnapshot",
    "LegacyScopedComparisonSnapshot", "PhysicalCard", "parse_snapshot",
    "preview_legacy_observation",
)
