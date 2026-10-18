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
from .retry_store import _DETACHED_ISSUED, missing_issued_sql, pending_retry_sql, queue_ready_facts, ready_attempt_sql


class QueueSelectionAdapter:
    def __init__(self, database, *, queue_application, review_service, lock_card, lock_day,
                 first_issuance_effect=None, essential_ready=None, priority_for_retry=None):
        self.database = database
        self.queue_application = queue_application
        self.review_service = review_service
        self.lock_card = lock_card
        self.lock_day = lock_day
        self.first_issuance_effect = first_issuance_effect
        self.essential_ready = essential_ready
        self.priority_for_retry = priority_for_retry

    def _complete_guard(self, day):
        predicate = "EXISTS(SELECT 1 FROM queue_projections WHERE queue_date=? AND state='ready' AND refresh_pending=0)"
        arguments = [day]
        if self.essential_ready:
            extra, parameters = self.essential_ready(day)
            predicate += ' AND (' + extra + ')'
            arguments.extend(parameters)
        return predicate,tuple(arguments)

    def queue_ready_facts(self, context):
        guard, parameters = self._complete_guard(context.local_date)
        complete = self.database.execute('SELECT 1 WHERE ' + guard,parameters).fetchone() is not None
        return queue_ready_facts(self.database, context.local_date, context.observed_at,
            base_statement=self.queue_application.available_attempt_sql(), complete=complete)

    @staticmethod
    def _same_identity(left, right):
        return (left.entry_id,left.card_id,left.revision,left.cycle,left.local_date) == (
            right.entry_id,right.card_id,right.revision,right.cycle,right.local_date)

    def apply_queue_action(self, proposal, context):
        if hasattr(self.database, 'execute_native'):
            if proposal.identity:
                self.lock_card(self.database, proposal.identity)
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
        if current.kind == 'recover':
            return self._recover_issued(current.identity,context)
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
            guard += ' AND NOT EXISTS(' + missing_issued_sql() + ')'
            complete_guard, complete_parameters = self._complete_guard(context.local_date)
            guard += ' AND ' + complete_guard
            arguments.extend((context.local_date,context.local_date,timestamp,context.local_date,context.local_date,*complete_parameters))
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

    def _recover_issued(self, identity, context):
        """Restore one proven SAME issued origin or return an actionable conflict.

        No new admission, retry decision, issued identity, or alternate card lock
        is acquired. A savepoint keeps a failed eligibility proof non-mutating.
        """
        origin = self.database.execute('SELECT retained.*,retry.issued_position FROM queue_retry_policies retry '
            'JOIN queue_attempt_origins retained ON retained.queue_entry_id=retry.retry_queue_entry_id '
            'AND retained.card_id=retry.card_id AND retained.revision=retry.revision '
            "WHERE retained.queue_entry_id=? AND retained.card_id=? AND retained.revision=? "
            "AND retained.queue_date=? AND retained.issued_as_head=1 AND retained.last_status='queued' "
            'AND retained.review_result_json IS NULL',
            (identity.entry_id,identity.card_id,identity.revision,context.local_date)).fetchone()
        card = self.database.execute('SELECT revision,start_fen,moves_json,trained_color,content_type '
            'FROM cards WHERE id=?',(identity.card_id,)).fetchone()
        conflict = QueueSelection('conflict',identity,'issued_retry_recovery_required')
        if not origin or not card or origin['issued_position'] is None or any(
                origin[field]!=card[field] for field in
                ('revision','start_fen','moves_json','trained_color','content_type')):
            return conflict
        if self.database.execute('SELECT 1 FROM daily_queue WHERE id=?',(identity.entry_id,)).fetchone():
            return conflict
        presented = ("EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.queue_entry_id=q.id)"
                     if hasattr(self.database,'execute_native') else '0')
        replacements = self.database.execute("SELECT q.id,q.admission_kind,q.attempt_failed,q.review_result_json,"
            'EXISTS(SELECT 1 FROM queue_attempt_origins other WHERE other.queue_entry_id=q.id '
            'AND other.card_id=q.card_id AND other.revision=? AND other.issued_as_head=1) issued,'+
            presented+" presented FROM daily_queue q WHERE q.card_id=? AND q.queue_date=? AND q.status='queued'",
            (identity.revision,identity.card_id,context.local_date)).fetchall()
        if len(replacements)>1 or any(row['issued'] or row['presented'] or row['attempt_failed']
                or row['review_result_json'] is not None or row['admission_kind']=='explicit' for row in replacements):
            return conflict
        previous = self.review_service.capture_daily_queue_order(self.database,context.local_date)
        self.database.execute('SAVEPOINT issued_retry_recovery')
        try:
            for row in replacements:
                # The replaced unissued identity must not remain recoverable as
                # a second queued attempt after the original is restored.
                self.database.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(row['id'],))
                self.database.execute('DELETE FROM daily_queue WHERE id=?',(row['id'],))
            priority = self.priority_for_retry(self.database,identity.card_id) if self.priority_for_retry else None
            self.database.execute('INSERT INTO daily_queue(id,queue_date,card_id,cycle,position,status,attempt_state,'
                'attempt_failed,card_bucket,admission_kind,admission_repertoire_id,gameplay_priority_reason) '
                "VALUES(?,?,?,?,?,'queued','guided',?,?,?,?,?)",
                (identity.entry_id,context.local_date,identity.card_id,origin['cycle'],origin['issued_position'],
                 origin['attempt_failed'],origin['content_type'],origin['admission_kind'],
                 origin['admission_repertoire_id'],priority))
            eligible = self.database.execute(self.queue_application.available_attempt_sql()+' AND q.id=?',
                (context.local_date,identity.entry_id)).fetchone()
            if not eligible:
                self.database.execute('ROLLBACK TO SAVEPOINT issued_retry_recovery')
                return conflict
            self.review_service.preserve_daily_queue_order(self.database,context.local_date,previous=previous)
        finally:
            self.database.execute('RELEASE SAVEPOINT issued_retry_recovery')
        return QueueSelection('changed',identity,'issued_origin_restored')

    def _issue(self, identity, context):
        head = self.database.execute(ready_attempt_sql(self.database,self.queue_application.available_attempt_sql()) +
            ' ORDER BY q.position,q.id LIMIT 1',(context.local_date,)).fetchone()
        if not head or head[0]!=identity.entry_id:
            return QueueSelection('pending',reason='head_changed')
        origin = self.database.execute('SELECT issued_as_head FROM queue_attempt_origins '
            'WHERE queue_entry_id=? AND card_id=? AND revision=?',
            (identity.entry_id,identity.card_id,identity.revision)).fetchone()
        if origin is None:
            return QueueSelection('pending',reason='origin_unavailable')
        if not origin[0] and self.first_issuance_effect:
            self.first_issuance_effect(self.database,identity,context)
        changed = self.database.execute('UPDATE queue_attempt_origins SET issued_as_head=1 '
            'WHERE queue_entry_id=? AND card_id=? AND revision=?',
            (identity.entry_id,identity.card_id,identity.revision)).rowcount
        if not changed:
            return QueueSelection('pending',reason='origin_unavailable')
        self.database.execute('UPDATE queue_retry_policies SET issued_at=COALESCE(issued_at,?), '
            'issued_position=COALESCE(issued_position,(SELECT position FROM daily_queue WHERE id=?)) '
            'WHERE retry_queue_entry_id=? AND released_at IS NOT NULL',
            (context.observed_at.astimezone(timezone.utc).isoformat(),identity.entry_id,identity.entry_id))
        return QueueSelection('issued',replace(identity,issued=True))


def selection_store(database):
    """Independent runtime retains its canonical issuance/admission owners."""
    from . import queue_application, review_service
    from .queue_commands import _lock_attempt_card
    from .queue_position_lock import lock_queue_date_for_position
    from .remediation_adapter import bind_issued_attempt
    from .real_game_feedback import MISS_REASON, has_outstanding_real_game_miss
    def bind(database,identity,_context):
        bind_issued_attempt(database,identity.entry_id,identity.card_id,identity.revision)
    def essential(_day):
        # A failed essential task is also incomplete until its owner repairs it.
        # Includes other-day unfinished work: no optimistic absence from a stale
        # task cursor or a still-pending rescan/generation.
        return ("NOT EXISTS(SELECT 1 FROM background_tasks WHERE kind='remediation' "
                "AND deduplication_key='current' AND state!='complete')",())
    return QueueSelectionAdapter(database,queue_application=queue_application,
        review_service=review_service,
        lock_card=lambda database,identity: _lock_attempt_card(database,identity.entry_id,
            retained_identity=identity if identity.issued else None),
        lock_day=lock_queue_date_for_position,first_issuance_effect=bind,essential_ready=essential,
        priority_for_retry=lambda database,card_id: MISS_REASON if has_outstanding_real_game_miss(database,card_id) else None)
