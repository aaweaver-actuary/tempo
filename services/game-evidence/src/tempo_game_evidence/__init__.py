"""Game Evidence owns observed games; it never writes controlled study state."""
from .source import normalize_pgn, replay_positions
from .snapshot import ComparisonSnapshot
from .comparison import compare_game
from .legacy_authority import LegacyAuthorityToken, LegacyComparisonSnapshot, PhysicalCard
from .store import EvidenceStore

__all__ = ["normalize_pgn", "replay_positions", "ComparisonSnapshot", "compare_game", "EvidenceStore",
           "LegacyAuthorityToken", "LegacyComparisonSnapshot", "PhysicalCard"]
