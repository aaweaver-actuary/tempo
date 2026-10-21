"""Immutable opening graph occurrence shared contract; no graph builder or storage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphStep:
    repertoire_id: str
    line_id: str
    decision_index: int
    segment_kind: str
    first_decision_index: int
    last_decision_index: int
    decision_fen_keys: tuple[str, ...]
    card_id: str
    parent_card_id: str | None
    decision_fen_key: str
    starting_fen: str
    moves: tuple[str, ...]
    trained_color: str
