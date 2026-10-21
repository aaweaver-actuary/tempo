"""Semantic feedback capabilities borrowing the command's transaction.

Implementations own queries, database locks and existing queue writers. No method
accepts SQL or owns commit/rollback; all effects must share the caller's receipt.
"""
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class MissCard:
    card_id: str
    state: str
    introduced_at: str | None


@dataclass(frozen=True)
class MissEvidence:
    event_id: str
    card_id: str
    game_id: str
    played_at: str


class RealGameFeedbackStore(Protocol):
    def lock_card(self, card_id: str) -> None: ...
    def outstanding_card(self, card_id: str) -> MissCard | None: ...
    def is_buried(self, card_id: str, queue_day: str) -> bool: ...
    def request_introduction(self, queue_day: str) -> None: ...
    def lock_queue_day(self, queue_day: str) -> None: ...
    def enqueue_review(self, card_id: str, queue_day: str, reason: str) -> None: ...
    def prioritize_queued_misses(self, queue_day: str) -> None: ...
    def event_card(self, event_id: str) -> str | None: ...
    def accepted_miss_priority(self, card_id: str) -> str | None: ...
    def replace_miss_priority(self, card_id: str, previous: str, replacement: str | None) -> None: ...


def utc_instant(timestamp: str) -> datetime:
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)


class FeedbackProjectionStore(Protocol):
    """Bounded typed read capabilities; no SQL strings cross this boundary."""
    def outstanding_cards(self, card_ids: tuple[str, ...]) -> tuple[MissCard, ...]: ...
    def missed_event_batches(self, game_id: str) -> Iterator[tuple[str, ...]]: ...
    def repertoire_miss_batches(self, repertoire_id: str) -> Iterator[tuple[MissEvidence, ...]]: ...
