"""Authored PGN import response contract."""
from typing import Literal
from pydantic import BaseModel, Field


class ImportResult(BaseModel):
    """Model representing the result of importing games into the repertoire."""

    repertoire_id: str
    source_name: str
    games_found: int
    unique_lines: int
    cards_created: int
    duplicates_merged: int
    cards_admitted_today: int = 0
    integrity: dict = Field(default_factory=dict)
    decision_cards_created: int = 0
    shared_decisions_reused: int = 0
    prefix_cards_created: int = 0
    shared_prefixes_reused: int = 0
    descendant_decision_cards_created: int = 0
    graph_state: Literal["refreshing", "ready", "failed"] = "refreshing"
