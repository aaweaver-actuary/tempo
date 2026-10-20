"""Legacy queue admission storage, borrowing the caller's connection.

All queries, parameter expansion, and legacy dialect selection stay here or in
existing storage collaborators. No connection, transaction, or writer is created.
Feedback's public query remains the accepted-scope exclusion authority.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import frozen_opening_order
from .real_game_feedback import MISS_REASON, outstanding_miss_query, has_outstanding_real_game_miss

from .queue_admission import IntroductionCountPage, OpeningCandidatePage

READ_BATCH_SIZE = 16


_PRIORITY_OPENING_CANDIDATE_BODY = """SELECT DISTINCT c.id,linked.id repertoire_id,c.moves_json,c.due_date,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ?
                    WHEN c.state='locked' AND opportunity.id IS NOT NULL THEN
                    'Priority introduction · reached ' || json_extract(opportunity.evidence_json,'$.encounter_count') ||
                    ' times in games, missed ' || json_extract(opportunity.evidence_json,'$.miss_count') || ' times'
                    ELSE p.reason END gameplay_priority_reason,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ? ELSE p.priority_date END priority_date,
                  COALESCE(published_priority.priority_score,legacy_priority.priority_score) priority_score,
                  COALESCE(published_priority.completed_line_ids_json,legacy_priority.completed_line_ids_json) completed_line_ids_json,
                  COALESCE(published_priority.frontier_decisions_json,legacy_priority.frontier_decisions_json) frontier_decisions_json
           FROM cards c
           JOIN repertoires linked ON (
               linked.id=c.repertoire_id OR EXISTS(
                   SELECT 1 FROM repertoire_cards candidate_link
                   WHERE candidate_link.card_id=c.id
                     AND candidate_link.repertoire_id=linked.id
               )
           )
           LEFT JOIN current_gameplay_card_priorities p ON p.card_id=c.id AND p.priority_date<=?
           LEFT JOIN active_miss ON active_miss.card_id=c.id
           LEFT JOIN current_repertoire_opportunities opportunity ON opportunity.repertoire_id=linked.id
             AND opportunity.card_id=c.id AND opportunity.kind='weak_known_decision'
             AND opportunity.status='active'
             AND json_extract(opportunity.evidence_json,'$.analysis_based') IS NULL
           LEFT JOIN repertoire_priority_publications publication
             ON publication.repertoire_id=linked.id
           LEFT JOIN current_repertoire_card_priority_generations published_priority
             ON published_priority.card_id=c.id
            AND published_priority.repertoire_id=linked.id
            AND published_priority.generation=publication.generation
           LEFT JOIN current_repertoire_card_introduction_priorities legacy_priority
             ON legacy_priority.card_id=c.id AND legacy_priority.repertoire_id=linked.id
           WHERE c.content_type='opening' AND (c.due_date<=? OR p.card_id IS NOT NULL OR active_miss.card_id IS NOT NULL OR opportunity.id IS NOT NULL)
             AND (c.state='new' OR (c.state='locked' AND (opportunity.id IS NOT NULL OR active_miss.card_id IS NOT NULL)))
             AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
             AND EXISTS(SELECT 1 FROM repertoires r_ok
                        WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))
                          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                         WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id))
             AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?)"""


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
            cursor = "" if after is None else " AND (q.position>? OR (q.position=? AND q.id>?))"
            rows = self.database.execute(
                """SELECT q.id,q.card_id,q.position,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id
                   FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.status='queued' AND c.content_type='opening'
                     AND c.repertoire_id<>'__retained_cards__'
                     AND COALESCE(q.admission_kind,'')!='explicit'
                     AND (c.introduced_at IS NULL OR c.introduced_at=?)
                     AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)"""
                + cursor + " ORDER BY q.position,q.id LIMIT ?",
                (day, day, *((after[0], after[0], after[1]) if after is not None else ()), READ_BATCH_SIZE),
            ).fetchall()
            if not rows:
                return result
            result.extend(dict(row) for row in rows)
            after = (rows[-1]['position'], rows[-1]['id'])

    def introduction_count_page(self, day, after_repertoire_id, *, reviewed_only=False):
        # Historical introductions consume allowance even if integrity or
        # membership eligibility changes. Requeues never charge a second time.
        statement = ("""SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
            FROM cards c LEFT JOIN daily_queue q
              ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0
            WHERE c.content_type='opening' AND c.introduced_at=?
              AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)""" if reviewed_only else
            """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
            FROM daily_queue q JOIN cards c ON c.id=q.card_id
            WHERE q.queue_date=? AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=?""")
        parameters = (day,) if reviewed_only else (day, day)
        if after_repertoire_id is not None:
            statement += " AND COALESCE(q.admission_repertoire_id,c.repertoire_id)>?"
            parameters += (after_repertoire_id,)
        rows = self.database.execute(
            statement + " GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id) "
            "ORDER BY COALESCE(q.admission_repertoire_id,c.repertoire_id) LIMIT ?",
            (*parameters, READ_BATCH_SIZE),
        ).fetchall()
        return IntroductionCountPage(tuple((row[0], int(row[1])) for row in rows),
                                     rows[-1][0] if len(rows) == READ_BATCH_SIZE else None)

    def has_outstanding_miss(self, card_id):
        return has_outstanding_real_game_miss(self.database, card_id)

    def retain_unseen(self, day, card_id):
        self.database.execute("UPDATE cards SET introduced_at=?,state='learning' WHERE id=?", (day, card_id))

    def remove_unseen(self, entry_id, card_id):
        self.database.execute("DELETE FROM daily_queue WHERE id=?", (entry_id,))
        self.database.execute("UPDATE cards SET introduced_at=NULL,state='new' WHERE id=?", (card_id,))

    def _priority_page(self, day, card_ids, *, capacity=False):
        if not card_ids:
            return []
        if len(card_ids) > READ_BATCH_SIZE:
            raise ValueError('Opening candidate page exceeds the adapter bound')
        body = _PRIORITY_OPENING_CANDIDATE_BODY
        if capacity:
            from .opening_capacity_admission import capacity_priority_statement
            body = capacity_priority_statement(body)
        rows = self.database.execute(
            f"WITH active_miss AS ({outstanding_miss_query(self.database)}) " + body
            + ' AND c.id IN (' + ','.join('?' for _ in card_ids) + ')',
            (MISS_REASON, day, day, day, day, *card_ids),
        ).fetchall()
        return [dict(row) for row in rows]

    def opening_candidate_page(self, day, after_card_id):
        identifiers = [row[0] for row in self.database.execute(
            "SELECT id FROM cards WHERE content_type='opening' AND state IN ('new','locked') "
            "AND introduced_at IS NULL AND archived=0 AND COALESCE(pending_validation,0)=0 "
            "AND id>? ORDER BY id LIMIT ?", (after_card_id, READ_BATCH_SIZE),
        ).fetchall()]
        return OpeningCandidatePage(tuple(self._priority_page(day, identifiers)) if identifiers else (),
                                    identifiers[-1] if len(identifiers) == READ_BATCH_SIZE else None)

    def annotate_openings(self, candidates):
        return frozen_opening_order.annotate(self.database, candidates)

    def opening_limits(self, default):
        if isinstance(default, dict):
            return default
        result, after = {}, ''
        while True:
            rows = self.database.execute('SELECT id FROM repertoires WHERE id>? ORDER BY id LIMIT ?',
                                         (after, READ_BATCH_SIZE)).fetchall()
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
        from .opening_capacity_admission import read_capacity_routes
        return read_capacity_routes(lambda statement, parameters: self.database.execute(statement, parameters).fetchall(),
                                    repertoire_id, parent_ids)

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
        return int(self.database.execute(
            """SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id
               WHERE q.queue_date=? AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=?
                 AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=?""", (day, day, repertoire_id),
        ).fetchone()[0])

    def current_limit(self, repertoire_id):
        row = self.database.execute(
            'SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
            'FROM repertoires r CROSS JOIN settings s WHERE r.id=? AND s.id=1', (repertoire_id,),
        ).fetchone()
        return int(row[0]) if row else None

    def introduce_opening(self, day, proposal, position):
        self.database.execute(
            """INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,admission_repertoire_id)
               VALUES(?,?,?,?,?)""",
            (day, proposal['card_id'], position, proposal['reason'], proposal['repertoire_id']),
        )
        self.retain_unseen(day, proposal['card_id'])
        self.database.execute(
            """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
               WHERE repertoire_id=? AND card_id=? AND kind='weak_known_decision'
                 AND json_extract(evidence_json,'$.analysis_based') IS NULL AND status='active'""",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
             proposal['repertoire_id'], proposal['card_id']),
        )
