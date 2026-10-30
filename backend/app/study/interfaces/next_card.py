"""Bounded local next-card work; no provider, storage or startup implementation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NextCardPreparation:
    progressed: bool = False
    pending: bool = False
    reason: str | None = None
