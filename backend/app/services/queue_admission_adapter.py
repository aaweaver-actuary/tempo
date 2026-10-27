"""Legacy queue admission storage, borrowing the caller's connection.

Fixed queries, parameter expansion and dialect mechanics live in root database
storage. This composition retains admission/page decisions, callbacks and clocks;
no connection, transaction or writer is created.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import frozen_opening_order
from .real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query
from .real_game_feedback_adapter import has_outstanding_real_game_miss

from .queue_admission import IntroductionCountPage, OpeningCandidatePage

READ_BATCH_SIZE = 16


from tempo_legacy_database.adapters.queue_refresh.application import admission as admission_storage
from tempo_legacy_database.adapters.queue_refresh.application.admission import _PRIORITY_OPENING_CANDIDATE_BODY


class QueueAdmissionAdapter:
    miss_reason = MISS_REASON

    def __init__(self, database):
        self.database = database

    def frozen_active(self):
        return frozen_opening_order.activation(self.database)[0] is not None

    def unseen_openings(self, day):
        result, after = [], None
        while True:
            # Capture all facts before reconciliation changes their eligibility.
            rows = admission_storage.unseen_opening_page(self.database, day, after, READ_BATCH_SIZE)
            if not rows:
                return result
            result.extend(dict(row) for row in rows)
            after = (rows[-1]['position'], rows[-1]['id'])

    def introduction_count_page(self, day, after_repertoire_id, *, reviewed_only=False):
        # Historical introductions consume allowance even if integrity or
        # membership eligibility changes. Requeues never charge a second time.
        rows = admission_storage.introduction_count_rows(
            self.database, day, after_repertoire_id, reviewed_only, READ_BATCH_SIZE,
        )
        return IntroductionCountPage(tuple((row[0], int(row[1])) for row in rows),
                                     rows[-1][0] if len(rows) == READ_BATCH_SIZE else None)

    def has_outstanding_miss(self, card_id):
        # Legacy reconciliation uses this predicate to retain required work; it
        # is not a miss classifier. Preserve an actual same-definition encounter
        # introduction without manufacturing a failure or introducing anything.
        from tempo_legacy_database.adapters.study.game_feedback import retains_encounter_introduction
        if retains_encounter_introduction(self.database, card_id):
            return True
        return has_outstanding_real_game_miss(self.database, card_id)

    def retain_unseen(self, day, card_id):
        admission_storage.retain_unseen(self.database, day, card_id)

    def remove_unseen(self, entry_id, card_id):
        admission_storage.remove_unseen(self.database, entry_id, card_id)

    def _priority_page(self, day, card_ids, *, capacity=False):
        if not card_ids:
            return []
        if len(card_ids) > READ_BATCH_SIZE:
            raise ValueError('Opening candidate page exceeds the adapter bound')
        rows = admission_storage.priority_rows(
            self.database, day, card_ids, MISS_REASON, capacity=capacity,
        )
        return [dict(row) for row in rows]

    def opening_candidate_page(self, day, after_card_id):
        identifiers = [row[0] for row in admission_storage.candidate_identifiers(self.database, after_card_id, READ_BATCH_SIZE)]
        return OpeningCandidatePage(tuple(self._priority_page(day, identifiers)) if identifiers else (),
                                    identifiers[-1] if len(identifiers) == READ_BATCH_SIZE else None)

    def annotate_openings(self, candidates):
        return frozen_opening_order.annotate(self.database, candidates)

    def opening_limits(self, default):
        if isinstance(default, dict):
            return default
        result, after = {}, ''
        while True:
            rows = admission_storage.repertoire_identifiers(self.database, after, READ_BATCH_SIZE)
            if not rows:
                return result
            result.update((row[0], default) for row in rows)
            after = rows[-1][0]

    def capacity_enabled(self):
        from .opening_capacity_admission import capacity_expansion_enabled
        return capacity_expansion_enabled()

    def study_only(self):
        from .study_mode import study_only_enabled
        return study_only_enabled(self.database)

    def capacity_routes(self, repertoire_id, parent_ids):
        from .opening_capacity_admission import CAPACITY_READ_BATCH_SIZE, capacity_routes_from_rows
        return capacity_routes_from_rows(admission_storage.borrowed_capacity_route_rows(
            self.database, repertoire_id, parent_ids, CAPACITY_READ_BATCH_SIZE,
        ))

    def capacity_candidates(self, day, card_ids):
        result = []
        for offset in range(0, len(card_ids), READ_BATCH_SIZE):
            result.extend(self._priority_page(day, card_ids[offset:offset+READ_BATCH_SIZE], capacity=True))
        return result

    def frozen_proposal_valid(self, proposal):
        # This is the existing frozen-activation fence, not general card/route
        # eligibility. It returns true when the inactive token still matches.
        return frozen_opening_order.proposal_valid(self.database, proposal)

    def capacity_route_valid(self, proposal, day):
        from .opening_capacity_admission import capacity_route_still_valid
        return capacity_route_still_valid(self.database, proposal, day)

    def introduced_count(self, day, repertoire_id):
        return int(admission_storage.introduced_count(self.database, day, repertoire_id))

    def current_limit(self, repertoire_id):
        row = admission_storage.current_limit(self.database, repertoire_id)
        return int(row[0]) if row else None

    def introduce_opening(self, day, proposal, position):
        admission_storage.append_opening(self.database, day, proposal['card_id'], position, proposal['reason'], proposal['repertoire_id'])
        self.retain_unseen(day, proposal['card_id'])
        admission_storage.resolve_opportunity(
            self.database, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
            proposal['repertoire_id'], proposal['card_id'],
        )
