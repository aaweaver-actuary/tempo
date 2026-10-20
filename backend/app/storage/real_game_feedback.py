"""Feedback persistence and legacy queue effects on a borrowed transaction.

This adapter never opens, commits or rolls back a transaction. PostgreSQL owns
card-before-day locks; SQLite compatibility is confined to this implementation
and its projection module. Queue effects delegate to the existing writers.
"""
from collections.abc import Iterator

from ..postgres_store import PostgresConnection
from ..queue_position_lock import lock_queue_date_for_position
from ..services.real_game_feedback_ports import MissCard, MissEvidence
from .real_game_miss_projection import outstanding_miss_query

BATCH_SIZE = 128


class BorrowedFeedbackStore:
    def __init__(self, database):
        self._database = database

    def lock_card(self, card_id: str) -> None:
        if isinstance(self._database, PostgresConnection):
            self._database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                          (f'tempo:card-edit:{card_id}',))
            self._database.execute('SELECT id FROM cards WHERE id=? FOR UPDATE', (card_id,))

    def outstanding_cards(self, card_ids: tuple[str, ...]) -> tuple[MissCard, ...]:
        """One complete, parameter-bound projection for at most one batch."""
        if len(card_ids) > BATCH_SIZE:
            raise ValueError('feedback card batch exceeds 128 identities')
        if not card_ids:
            return ()
        placeholders = ','.join('?' for _ in card_ids)
        rows = self._database.execute(outstanding_miss_query(
            self._database, columns='DISTINCT card.id,card.state,card.introduced_at',
            additional_where=f'AND card.id IN ({placeholders}) ORDER BY card.id'), card_ids).fetchall()
        return tuple(MissCard(str(row['id']), row['state'], row['introduced_at']) for row in rows)

    def outstanding_card(self, card_id: str) -> MissCard | None:
        cards = self.outstanding_cards((card_id,))
        return cards[0] if cards else None

    def is_buried(self, card_id: str, queue_day: str) -> bool:
        return self._database.execute(
            "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND status='buried' LIMIT 1",
            (queue_day, card_id)).fetchone() is not None

    def request_introduction(self, queue_day: str) -> None:
        from ..services.queue_application import request_queue_refresh_in_transaction
        request_queue_refresh_in_transaction(self._database, queue_day)

    def lock_queue_day(self, queue_day: str) -> None:
        if isinstance(self._database, PostgresConnection):
            lock_queue_date_for_position(self._database, queue_day)

    def enqueue_review(self, card_id: str, queue_day: str, reason: str) -> None:
        from ..services.review_service import ensure_card_queued_after
        ensure_card_queued_after(self._database, card_id, 0, priority_reason=reason,
                                 queue_date=queue_day, protect_presented=True, preserve_order=False)

    def prioritize_queued_misses(self, queue_day: str) -> None:
        from ..services.queue_application import presentation_rows
        from ..services.queue_ordering import _priority_queue_order
        entries = sorted(presentation_rows(self._database, queue_day), key=lambda row: (row['position'], row['id']))
        for slot, entry in zip(entries, _priority_queue_order(entries)):
            if slot['position'] != entry['position']:
                self._database.execute('UPDATE daily_queue SET position=? WHERE id=?', (slot['position'], entry['id']))

    def event_card(self, event_id: str) -> str | None:
        row = self._database.execute(
            "SELECT card_id FROM current_repertoire_decision_events WHERE id=? AND outcome='miss'", (event_id,)).fetchone()
        return row['card_id'] if row else None

    def accepted_miss_priority(self, card_id: str) -> str | None:
        if isinstance(self._database, PostgresConnection):
            from ..services.legacy_study_remediation import has_current_miss
            from ..services.legacy_game_evidence_scope import BRIDGE_MISS_REASON
            if has_current_miss(self._database, card_id):
                return BRIDGE_MISS_REASON
        return None

    def replace_miss_priority(self, card_id: str, previous: str, replacement: str | None) -> None:
        self._database.execute('UPDATE daily_queue SET gameplay_priority_reason=? '
                               'WHERE card_id=? AND gameplay_priority_reason=?', (replacement, card_id, previous))

    def missed_event_batches(self, game_id: str) -> Iterator[tuple[str, ...]]:
        # One query retains the original selection snapshot and order. Fetch in
        # bounded batches rather than issuing a new query/snapshot per page.
        cursor = self._database.execute(
            "SELECT id FROM current_repertoire_decision_events WHERE game_id=? AND outcome='miss' ORDER BY ply,id", (game_id,))
        while rows := cursor.fetchmany(BATCH_SIZE):
            yield tuple(str(row[0]) for row in rows)

    def repertoire_miss_batches(self, repertoire_id: str) -> Iterator[tuple[MissEvidence, ...]]:
        cursor = self._database.execute(outstanding_miss_query(
            self._database, columns='event.id,event.card_id,event.game_id,event.played_at',
            additional_where='AND event.repertoire_id=?'), (repertoire_id,))
        while rows := cursor.fetchmany(BATCH_SIZE):
            yield tuple(MissEvidence(row['id'], row['card_id'], row['game_id'], row['played_at']) for row in rows)
