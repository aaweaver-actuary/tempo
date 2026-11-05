"""Validated capture facts and failures; no transaction or transport dependency."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidatedCapture:
    capture_id: str
    card_id: str
    starting_fen: str
    identity_fen: str
    moves: tuple[str, ...]
    trained_color: str
    source_kind: str
    source_ref: str | None
    source_url: str | None
    note: str
    game_finding: bool
    request_json: str


class TacticCaptureValidationError(ValueError):
    """The captured source or line is not legal."""


class TacticCaptureConflict(ValueError):
    """A retained identity or existing card conflicts with this capture."""
