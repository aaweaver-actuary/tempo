"""Stable persisted identity; no chess-engine or move-canonicalization behavior."""
from ._identity import canonical_fen_key, card_id

__all__ = ["canonical_fen_key", "card_id"]
