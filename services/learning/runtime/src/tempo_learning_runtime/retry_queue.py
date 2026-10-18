"""One bounded queue-selection write over the composition's existing locks.

The caller owns the transaction. Existing queue/review modules provide their
composition-specific presentation provenance and frozen eligibility adapters.
"""
from dataclasses import replace
from datetime import timezone
import json

from tempo_learning_review.retry_policy import (
    QueueSelection, choose_queue_action, retry_insertion_order,
)

from .queue_ordering import _protected_queue_ids
from .real_game_feedback import MISS_REASON
from .retry_store import _DETACHED_ISSUED, pending_retry_sql, queue_ready_facts, ready_attempt_sql


class QueueSelectionAdapter:
    def __init__(self, database, *, queue_application, review_service, lock_card, lock_day):
        self.database = database
        self.queue_application = queue_application
        self.review_service = review_service
        self.lock_card = lock_card
        self.lock_day = lock_day

    def queue_ready_facts(self, context):
        projection = self.database.execute(
            "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=?",
            (context.local_date,),
        ).fetchone()
        complete = bool(projection and projection['state'] == 'ready' and not projection['refresh_pending'])
        return queue_ready_facts(self.database, context.local_date, context.observed_at,
            base_statement=self.queue_application.available_attempt_sql(), complete=complete)

    @staticmethod
    def _same_identity(left, right):
        return (left.entry_id,left.card_id,left.revision,left.cycle,left.local_date) == (
            right.entry_id,right.card_id,right.revision,right.cycle,right.local_date)

    def apply_queue_action(self, proposal, context):
        if hasattr(self.database, 'execute_native'):
            if proposal.identity:
                self.lock_card(self.database, proposal.identity.entry_id)
            self.lock_day(self.database, context.local_date)
        else:
            raw = getattr(self.database, 'database', self.database)
            if not raw.in_transaction:
                raise RuntimeError('SQLite queue selection requires a caller-owned BEGIN IMMEDIATE transaction')
        current = choose_queue_action(self.queue_ready_facts(context), observed_at=context.observed_at)
        if current.kind == 'issue' and current.identity.issued:
            if proposal.identity and self._same_identity(current.identity, proposal.identity):
                return QueueSelection('issued', current.identity)
        matches = current.kind == proposal.kind and (
            current.identity is None and proposal.identity is None or
            current.identity is not None and proposal.identity is not None
            and self._same_identity(current.identity, proposal.identity))
        if not matches:
            return QueueSelection('pending', reason='authority_changed')
        if current.kind in {'pending','exhausted'}:
            return QueueSelection(current.kind, reason=current.reason)
        if current.kind == 'issue':
            return self._issue(current.identity, context)
        base = self.queue_application.available_attempt_sql()
        policy = self.database.execute(pending_retry_sql(base, 'retry.*') + ' AND q.id=?',
            (context.local_date,current.identity.entry_id)).fetchone()
        if not policy:
            return QueueSelection('pending', reason='retry_changed')
        timestamp = context.observed_at.astimezone(timezone.utc).isoformat()
        guard = ''
        arguments = [current.identity.entry_id,timestamp,current.reason,policy['review_id']]
        if current.kind == 'early_retry':
            # One atomic statement checks the whole ready set, including natural
            # retries not yet placed; neither query has pagination/SKIP LOCKED.
            guard = ' AND NOT EXISTS(' + ready_attempt_sql(self.database,base) + ')'
            guard += ' AND NOT EXISTS(' + pending_retry_sql(base,'q.id') + ' AND (retry.released_at IS NOT NULL OR real_game_instant(retry.ready_at)<=real_game_instant(?)))'
            guard += ' AND NOT EXISTS(' + pending_retry_sql(base,'q.id') + ' AND ' + _DETACHED_ISSUED + ')'
            guard += " AND EXISTS(SELECT 1 FROM queue_projections WHERE queue_date=? AND state='ready' AND refresh_pending=0)"
            arguments.extend((context.local_date,context.local_date,timestamp,context.local_date,context.local_date))
        if policy['released_at'] is not None:
            changed = self.database.execute('UPDATE queue_retry_policies SET retry_queue_entry_id=? '
                'WHERE review_id=? AND released_at IS NOT NULL AND retry_queue_entry_id!=?',
                (current.identity.entry_id,policy['review_id'],current.identity.entry_id)).rowcount
        else:
            changed = self.database.execute(
                'UPDATE queue_retry_policies SET retry_queue_entry_id=?,released_at=?,release_reason=? '
                'WHERE review_id=? AND released_at IS NULL' + guard, tuple(arguments)).rowcount
        if changed != 1:
            return QueueSelection('pending', reason='ready_work_changed')
        self._position_retry(current.identity, policy['order_token'], context)
        # A different resulting head requires a fresh card-before-day transaction.
        head = self.database.execute(ready_attempt_sql(self.database,base) +
            ' ORDER BY q.position,q.id LIMIT 1', (context.local_date,)).fetchone()
        if head and head[0] == current.identity.entry_id:
            return self._issue(current.identity, context)
        return QueueSelection('changed', reason='retry_placement_committed')

    def _position_retry(self, identity, token, context):
        # Serving already proved eligibility; fetch just the shared protection
        # facts without a per-card parameter list or unrelated history payload.
        presented = ("EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.queue_entry_id=q.id)"
                     if hasattr(self.database, 'execute_native') else '0')
        fields = """q.id,q.card_id,q.position,c.content_type,
            CASE WHEN q.admission_kind='explicit' THEN 'explicit'
                 WHEN q.cycle>0 OR EXISTS(SELECT 1 FROM reviews history
                     WHERE history.card_id=c.id AND history.invalidated_at IS NULL) THEN 'review'
                 ELSE 'new' END admission_kind,
            q.gameplay_priority_reason,q.cycle,q.attempt_state,q.attempt_failed,
            (q.review_result_json IS NOT NULL) review_completed,
            EXISTS(SELECT 1 FROM queue_attempt_origins origin
                WHERE origin.queue_entry_id=q.id AND origin.card_id=c.id
                  AND origin.revision=c.revision AND origin.issued_as_head=1) issued,
            """ + presented + """ presented,
            EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id) taught"""
        statement = ready_attempt_sql(self.database,self.queue_application.available_attempt_sql())
        statement = statement.replace('SELECT q.id FROM','SELECT ' + fields + ' FROM',1)
        rows = [dict(row) for row in self.database.execute(
            statement + ' ORDER BY q.position,q.id',(context.local_date,)).fetchall()]
        candidate = next((row for row in rows if row['id']==identity.entry_id),None)
        if candidate is None:
            raise ValueError('Retry entry disappeared during its locked placement')
        if candidate.get('issued') or candidate.get('presented') or candidate.get('attempt_failed'):
            return
        protected = _protected_queue_ids(rows)
        movable = [row for row in rows if row['id']==identity.entry_id or
                   row['id'] not in protected]
        ordered = retry_insertion_order(tuple((row['id'],row.get('gameplay_priority_reason')==MISS_REASON)
            for row in movable),retry_entry_id=identity.entry_id,order_token=token)
        previous = self.review_service.capture_daily_queue_order(self.database,context.local_date)
        positions = {row['id']:row['position'] for row in movable}
        placement = [{'id':entry_id,'position':slot['position']}
                     for slot,entry_id in zip(movable,ordered)
                     if positions[entry_id]!=slot['position']]
        if placement:
            payload = json.dumps(placement,separators=(',',':'))
            if hasattr(self.database, 'execute_native'):
                self.database.execute_native("""UPDATE daily_queue AS queued
                    SET position=placement.position
                    FROM jsonb_to_recordset(%s::jsonb) AS placement(id bigint,position bigint)
                    WHERE queued.id=placement.id AND queued.status='queued' AND queued.queue_date=%s""",
                    (payload,context.local_date))
            else:
                self.database.execute("""WITH placement AS (
                    SELECT json_extract(value,'$.id') id,json_extract(value,'$.position') position
                    FROM json_each(?))
                    UPDATE daily_queue AS queued SET position=placement.position FROM placement
                    WHERE queued.id=placement.id AND queued.status='queued' AND queued.queue_date=?""",
                    (payload,context.local_date))
        self.review_service.preserve_daily_queue_order(self.database,context.local_date,previous=previous)

    def _issue(self, identity, context):
        head = self.database.execute(ready_attempt_sql(self.database,self.queue_application.available_attempt_sql()) +
            ' ORDER BY q.position,q.id LIMIT 1',(context.local_date,)).fetchone()
        if not head or head[0]!=identity.entry_id:
            return QueueSelection('pending',reason='head_changed')
        changed = self.database.execute('UPDATE queue_attempt_origins SET issued_as_head=1 '
            'WHERE queue_entry_id=? AND card_id=? AND revision=?',
            (identity.entry_id,identity.card_id,identity.revision)).rowcount
        if not changed:
            return QueueSelection('pending',reason='origin_unavailable')
        self.database.execute('UPDATE queue_retry_policies SET issued_at=COALESCE(issued_at,?) '
            'WHERE retry_queue_entry_id=? AND released_at IS NOT NULL',
            (context.observed_at.astimezone(timezone.utc).isoformat(),identity.entry_id))
        return QueueSelection('issued',replace(identity,issued=True))
