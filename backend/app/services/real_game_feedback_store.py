"""Existing borrowed feedback capability and legacy queue-effect composition.

Typed facts, queue ordering, introduction/review callbacks and accepted-priority
choices stay here. Complete queries, cursor batches and dialect/lock mechanics
live in root database storage. This capability never opens or completes a
transaction; queue effects retain their existing writers.
"""
from collections.abc import Iterator

from tempo_legacy_database.adapters import real_game_feedback as storage
from app.services.real_game_feedback_ports import MissCard, MissEvidence


class BorrowedFeedbackStore:
    def __init__(self, database):
        self._database = database

    def lock_card(self, card_id: str) -> None:
        storage.lock_card(self._database, card_id)

    def outstanding_cards(self, card_ids: tuple[str, ...]) -> tuple[MissCard, ...]:
        """One complete, parameter-bound projection for at most one batch."""
        rows = storage.outstanding_cards(self._database, card_ids)
        return tuple(MissCard(str(row['id']), row['state'], row['introduced_at']) for row in rows)

    def outstanding_card(self, card_id: str) -> MissCard | None:
        cards = self.outstanding_cards((card_id,))
        return cards[0] if cards else None

    def is_buried(self, card_id: str, queue_day: str) -> bool:
        return storage.is_buried(self._database, card_id, queue_day)

    def request_introduction(self, queue_day: str) -> None:
        from app.services.queue_application import request_queue_refresh_in_transaction
        request_queue_refresh_in_transaction(self._database, queue_day)

    def lock_queue_day(self, queue_day: str) -> None:
        storage.lock_queue_day(self._database, queue_day)

    def enqueue_review(self, card_id: str, queue_day: str, reason: str) -> None:
        from app.services.review_service import ensure_card_queued_after
        ensure_card_queued_after(self._database, card_id, 0, priority_reason=reason,
                                 queue_date=queue_day, protect_presented=True, preserve_order=False)

    def prioritize_queued_misses(self, queue_day: str) -> None:
        from app.services.queue_application import presentation_rows
        from app.services.queue_ordering import _priority_queue_order
        entries = sorted(presentation_rows(self._database, queue_day), key=lambda row: (row['position'], row['id']))
        for slot, entry in zip(entries, _priority_queue_order(entries)):
            if slot['position'] != entry['position']:
                storage.set_queue_position(self._database, slot['position'], entry['id'])

    def event_card(self, event_id: str) -> str | None:
        return storage.event_card(self._database, event_id)

    def accepted_miss_priority(self, card_id: str) -> str | None:
        if storage.is_postgres(self._database):
            from app.services.legacy_study_remediation import has_current_miss
            from app.services.legacy_game_evidence_scope import BRIDGE_MISS_REASON
            if has_current_miss(self._database, card_id):
                return BRIDGE_MISS_REASON
        return None

    def replace_miss_priority(self, card_id: str, previous: str, replacement: str | None) -> None:
        storage.replace_miss_priority(self._database, card_id, previous, replacement)

    def missed_event_batches(self, game_id: str) -> Iterator[tuple[str, ...]]:
        for rows in storage.missed_event_batches(self._database, game_id):
            yield tuple(str(row[0]) for row in rows)

    def repertoire_miss_batches(self, repertoire_id: str) -> Iterator[tuple[MissEvidence, ...]]:
        for rows in storage.repertoire_miss_batches(self._database, repertoire_id):
            yield tuple(MissEvidence(row['id'], row['card_id'], row['game_id'], row['played_at']) for row in rows)
