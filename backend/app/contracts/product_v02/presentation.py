"""Read-only screen projections. No UI state, replay, transport or mutation."""
from pydantic import Field

from . import Identifier, UciMove
from .games import Finding, GameSummary
from .learning import LearningReceipt


class GameDetail(GameSummary):
    """Original game history remains separate from its derived findings."""
    starting_fen: Identifier
    moves: tuple[UciMove, ...] = Field(max_length=1000)
    findings: tuple[Finding, ...]
    receipts: tuple[LearningReceipt, ...]
