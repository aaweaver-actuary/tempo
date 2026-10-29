"""Study's scoped conflict store. Caller owns connection/transaction lifetime.

Only Study queue status and this owner's hold/receipt rows are mutated. Source
conflict policy is an injected, current Repertoire capability, never copied SQL.
"""
from dataclasses import asdict
import json

import psycopg

from app.study.conflicts import ConflictError, ConflictReceipt, Occurrence, StudyConflicts
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position


class PostgresConflictStore:
    def __init__(self, database):
        self.database = database

    def writer(self):
        if self.database.read_only or self.database.raw.info.transaction_status.name != 'INTRANS':
            raise ConflictError('borrowed_writer_transaction_required')

    def lock(self, command):
        self.writer()
        cards = sorted({item.presentation_id for item in command.occurrences})
        for card in cards:
            self.database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                         ('tempo:card-edit:'+card,))
            self.database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE',(card,))
        days = self.database.execute_native("""SELECT DISTINCT queue_date FROM daily_queue
            WHERE card_id=ANY(%s) AND status IN ('queued','blocked')
            AND review_result_json IS NULL ORDER BY queue_date LIMIT 129""",(cards,)).fetchall()
        if len(days)>128:
            raise ConflictError('conflict_queue_scope_exceeds_bound')
        for row in days:
            lock_queue_date_for_position(self.database,row[0])
        self.database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                     ('tempo:study-conflict-command:'+command.command_id,))

    def receipt(self, command_id):
        row = self.database.execute_native('SELECT payload_digest,receipt_json FROM study_conflict_receipts WHERE command_id=%s',
                                            (command_id,)).fetchone()
        if not row:
            return None
        data=json.loads(row['receipt_json'])
        data['blocked']=tuple(Occurrence(**item) for item in data['blocked'])
        for key in ('restored_queue_ids','suspended_queue_ids','unproven_queue_ids'):
            data[key]=tuple(data[key])
        return row['payload_digest'],ConflictReceipt(**data)

    def fence_source(self, command):
        # Never wait backwards for Repertoire while holding card/day locks.
        # Existing source writers update these revision rows before commit.
        held=self.database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
            ('tempo:opening-graph:'+command.repertoire_id,)).fetchone()[0]
        scope=self.database.execute_native("""SELECT repertoire.id FROM repertoires repertoire
            CROSS JOIN repertoire_game_scope scope WHERE repertoire.id=%s AND scope.id=1
            FOR SHARE OF repertoire,scope SKIP LOCKED""",(command.repertoire_id,)).fetchone()
        if not held or not scope:
            raise ConflictError('current_repertoire_source_busy_or_missing')
        publication=self.database.execute_native("""SELECT generation FROM opening_graph_publications
            WHERE repertoire_id=%s FOR SHARE SKIP LOCKED""",(command.repertoire_id,)).fetchone()
        if publication is None:
            raise ConflictError('current_physical_occurrence_unproved')

    def validate_target(self, occurrence):
        row=self.database.execute_native("""SELECT c.revision FROM cards c
            JOIN opening_graph_steps step ON step.card_id=c.id AND step.repertoire_id=%s AND step.line_id=%s
            JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
              AND publication.generation=step.generation AND publication.state='ready'
            WHERE c.id=%s AND c.content_type='opening' AND c.revision=%s
              AND (c.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards link
                WHERE link.card_id=c.id AND link.repertoire_id=%s)) LIMIT 1""",
            (occurrence.repertoire_id,occurrence.route_id,occurrence.presentation_id,
             occurrence.presentation_revision,occurrence.repertoire_id,occurrence.repertoire_id)).fetchone()
        if not row:
            raise ConflictError('current_physical_occurrence_unproved')

    def hold(self, occurrence, fence, active, now):
        self.database.execute_native("""INSERT INTO study_conflict_holds(scope_key,repertoire_id,route_id,ply,
            card_id,card_revision,source_revision,source_generation,source_digest,active,first_seen_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(scope_key) DO UPDATE SET source_revision=EXCLUDED.source_revision,
              source_generation=EXCLUDED.source_generation,source_digest=EXCLUDED.source_digest,
              active=EXCLUDED.active,updated_at=EXCLUDED.updated_at""",
            (occurrence.identity(),occurrence.repertoire_id,occurrence.route_id,occurrence.ply,
             occurrence.presentation_id,occurrence.presentation_revision,fence.source_revision,
             fence.revision,fence.digest,active,now,now))

    def owners(self, card_id):
        return {row[0] for row in self.database.execute_native("""SELECT repertoire_id FROM cards WHERE id=%s
            UNION SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s""",(card_id,card_id)).fetchall() if row[0]}

    @staticmethod
    def queue_scope(queue, owners):
        explicit=queue['admission_repertoire_id']
        return explicit if explicit in owners else next(iter(owners)) if explicit is None and len(owners)==1 else None

    def suspend(self, occurrence):
        queues=self.database.execute_native("""SELECT * FROM daily_queue WHERE card_id=%s
            AND status IN ('queued','blocked') AND review_result_json IS NULL
            ORDER BY queue_date,id LIMIT 129 FOR UPDATE""",(occurrence.presentation_id,)).fetchall()
        if len(queues)>128:
            raise ConflictError('conflict_queue_scope_exceeds_bound')
        owners=self.owners(occurrence.presentation_id)
        changed,unknown=[],[]
        for queue in queues:
            repertoire=self.queue_scope(queue,owners)
            if repertoire is None:
                unknown.append(queue['id'])
                continue
            if repertoire!=occurrence.repertoire_id:
                continue
            ours=self.database.execute_native("""SELECT 1 FROM study_conflict_queue_holds retained
                JOIN study_conflict_holds hold ON hold.scope_key=retained.scope_key
                WHERE retained.queue_entry_id=%s AND hold.active LIMIT 1""",(queue['id'],)).fetchone()
            if queue['status']=='blocked' and not ours:
                continue  # Never claim another owner's preexisting suspension.
            self.database.execute_native("""INSERT INTO study_conflict_queue_holds
                (scope_key,queue_entry_id,card_id,card_revision,original_status)
                VALUES(%s,%s,%s,%s,'queued') ON CONFLICT DO NOTHING""",
                (occurrence.identity(),queue['id'],occurrence.presentation_id,occurrence.presentation_revision))
            if queue['status']=='queued':
                self.database.execute_native("UPDATE daily_queue SET status='blocked' WHERE id=%s AND status='queued'",(queue['id'],))
                changed.append(queue['id'])
        return tuple(changed),tuple(unknown)

    def restore(self, occurrence):
        queues=self.database.execute_native("""SELECT queue.* FROM study_conflict_queue_holds retained
            JOIN daily_queue queue ON queue.id=retained.queue_entry_id AND queue.card_id=retained.card_id
            JOIN cards card ON card.id=retained.card_id AND card.revision=retained.card_revision
            WHERE retained.scope_key=%s AND queue.status='blocked' AND queue.review_result_json IS NULL
              AND card.archived=0 AND card.pending_validation=0 AND card.superseded_by IS NULL
              AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
              AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
                             WHERE blocked.card_id=card.id AND blocked.repertoire_id=%s)
              AND NOT EXISTS(SELECT 1 FROM study_conflict_queue_holds other
                  JOIN study_conflict_holds hold ON hold.scope_key=other.scope_key AND hold.active
                  WHERE other.queue_entry_id=queue.id)
            ORDER BY queue.queue_date,queue.id LIMIT 129 FOR UPDATE OF queue""",
            (occurrence.identity(),occurrence.repertoire_id)).fetchall()
        if len(queues)>128:
            raise ConflictError('conflict_queue_scope_exceeds_bound')
        owners=self.owners(occurrence.presentation_id)
        changed=[]
        for queue in queues:
            if self.queue_scope(queue,owners)!=occurrence.repertoire_id:
                continue
            self.database.execute_native("UPDATE daily_queue SET status='queued' WHERE id=%s AND status='blocked'",(queue['id'],))
            changed.append(queue['id'])
        return tuple(changed)

    def save_receipt(self, digest, receipt, now):
        self.database.execute_native("""INSERT INTO study_conflict_receipts
            (command_id,payload_digest,receipt_json,processed_at) VALUES(%s,%s,%s,%s)""",
            (receipt.command_id,digest,json.dumps(asdict(receipt),sort_keys=True),now))

    def blocked(self, repertoire_id, card_id, revision):
        return bool(self.database.execute_native("""SELECT 1 FROM study_conflict_holds
            WHERE repertoire_id=%s AND card_id=%s AND card_revision=%s AND active LIMIT 1""",
            (repertoire_id,card_id,revision)).fetchone())

    def active_occurrences(self, repertoire_id, *, after='', limit=16):
        if not 1<=limit<=16:
            raise ConflictError('bounded_conflict_page_required')
        rows=self.database.execute_native("""SELECT * FROM study_conflict_holds
            WHERE repertoire_id=%s AND active AND scope_key>%s ORDER BY scope_key LIMIT %s""",
            (repertoire_id,after,limit)).fetchall()
        return tuple(Occurrence(row['repertoire_id'],row['route_id'],row['ply'],row['card_id'],row['card_revision']) for row in rows)

    def block_state(self, repertoire_id, route_ids):
        """Read-only #501 projection: blocked, pending, or None (not proved).

        A processing receipt is historical; this read inspects current holds and
        queue scope/status. Product composition must also wire the serving guards.
        """
        routes=tuple(sorted(set(route_ids)))
        if not routes or len(routes)>16:
            return None
        rows=self.database.execute_native("""SELECT * FROM study_conflict_holds
            WHERE repertoire_id=%s AND route_id=ANY(%s) AND active ORDER BY scope_key LIMIT 129""",
            (repertoire_id,list(routes))).fetchall()
        if len(rows)>128 or set(row['route_id'] for row in rows)!=set(routes):
            return None
        for row in rows:
            current=self.database.execute_native('SELECT revision FROM cards WHERE id=%s',(row['card_id'],)).fetchone()
            if not current or current[0]!=row['card_revision']:
                return 'pending'
            queues=self.database.execute_native("""SELECT admission_repertoire_id,status FROM daily_queue
                WHERE card_id=%s AND status IN ('queued','blocked') AND review_result_json IS NULL LIMIT 129""",
                (row['card_id'],)).fetchall()
            if len(queues)>128:
                return 'pending'
            owners=self.owners(row['card_id'])
            for queue in queues:
                scope=self.queue_scope(queue,owners)
                if scope is None or (scope==repertoire_id and queue['status']=='queued'):
                    return 'pending'
        return 'blocked'

    def guard_attempt(self, queue_entry_id, card_id, revision):
        """Caller invokes before review/recovery/failure marking under card locks."""
        queue=self.database.execute_native("""SELECT admission_repertoire_id FROM daily_queue
            WHERE id=%s AND card_id=%s""",(queue_entry_id,card_id)).fetchone()
        if queue is None:
            queue=self.database.execute_native("""SELECT admission_repertoire_id FROM queue_attempt_origins
                WHERE queue_entry_id=%s AND card_id=%s AND revision=%s""",
                (queue_entry_id,card_id,revision)).fetchone()
        if queue is None:
            raise ConflictError('current_attempt_scope_unproved')
        owners=self.owners(card_id)
        repertoire=self.queue_scope(queue,owners)
        if repertoire is None:
            held=self.database.execute_native("""SELECT 1 FROM study_conflict_holds
                WHERE card_id=%s AND card_revision=%s AND active LIMIT 1""",(card_id,revision)).fetchone()
            if held:
                raise ConflictError('current_attempt_scope_unproved')
        elif self.blocked(repertoire,card_id,revision):
            raise ConflictError('study_occurrence_conflict_suspended')

    def reconcile_queue(self, *, after='', limit=1):
        """Reapply durable holds after rebuild; caller supplies one short transaction."""
        if limit!=1:
            raise ConflictError('bounded_conflict_page_required')
        rows=self.database.execute_native("""SELECT * FROM study_conflict_holds WHERE active AND scope_key>%s
            ORDER BY scope_key LIMIT %s""",(after,limit)).fetchall()
        # Queue rebuild may already own a day lock. Never wait backwards for
        # its card/graph/other-day reservations; leave the hold for a later sweep.
        scopes=tuple(Occurrence(row['repertoire_id'],row['route_id'],row['ply'],row['card_id'],row['card_revision']) for row in rows)
        if scopes:
            target=scopes[0]
            if self.try_queue_target(target):
                try:
                    self.validate_target(target)
                except ConflictError:
                    pass  # An old definition never suspends a replacement.
                else:
                    active=self.database.execute_native('SELECT active FROM study_conflict_holds WHERE scope_key=%s',
                                                         (target.identity(),)).fetchone()
                    if active and active[0]:
                        self.suspend(target)
            after=target.identity()
        return after if len(rows)==limit else None

    def try_queue_target(self, target):
        self.writer()
        locks=[('pg_try_advisory_xact_lock','tempo:card-edit:'+target.presentation_id),
               ('pg_try_advisory_xact_lock_shared','tempo:prefix-transition:reservations'),
               ('pg_try_advisory_xact_lock_shared','tempo:opening-graph:'+target.repertoire_id)]
        for function,key in locks:
            if not self.database.execute_native('SELECT '+function+'(hashtextextended(%s,0))',(key,)).fetchone()[0]:
                return False
        if not self.database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED',
                                            (target.presentation_id,)).fetchone():
            return False
        days=self.database.execute_native("""SELECT DISTINCT queue_date FROM daily_queue
            WHERE card_id=%s AND status IN ('queued','blocked') ORDER BY queue_date LIMIT 129""",
            (target.presentation_id,)).fetchall()
        if len(days)>128:
            raise ConflictError('conflict_queue_scope_exceeds_bound')
        for row in days:
            if not self.database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                                 ('tempo:daily-queue-position:'+row[0],)).fetchone()[0]:
                return False
        return True


def current_study_conflicts(database, *, authority, clock):
    """#501 injects the current #492 eligibility/fence capability, not its policy."""
    return StudyConflicts(PostgresConflictStore(database),authority,clock=clock)


def installed(database):
    """Absent optional C schema preserves ordinary Study, including SQLite."""
    return isinstance(getattr(database, "raw", None), psycopg.Connection) and database.execute_native(
        "SELECT to_regclass('study_conflict_holds') IS NOT NULL").fetchone()[0]


def held_queue_sql(database):
    """Owned current-scope projection for the ordinary due-row restorer.

    Match guard_attempt's explicit valid owner, single inferred owner or unproved
    scope. Unknown scope with a current hold is closed; another explicit owner's
    shared-card attempt stays available. No old-revision hold binds new content.
    The outer query owns the fixed daily_queue reference, never arbitrary SQL.
    """
    if not installed(database):
        return 'FALSE'
    scope="""CASE
        WHEN daily_queue.admission_repertoire_id IS NOT NULL AND (
            daily_queue.admission_repertoire_id=conflict_card.repertoire_id OR EXISTS(
                SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=conflict_card.id
                  AND owner_link.repertoire_id=daily_queue.admission_repertoire_id))
        THEN daily_queue.admission_repertoire_id
        WHEN daily_queue.admission_repertoire_id IS NULL AND NOT EXISTS(
            SELECT 1 FROM repertoire_cards other_owner WHERE other_owner.card_id=conflict_card.id
              AND other_owner.repertoire_id<>conflict_card.repertoire_id)
        THEN conflict_card.repertoire_id ELSE NULL END"""
    return """EXISTS(SELECT 1 FROM study_conflict_holds hold
        JOIN cards conflict_card ON conflict_card.id=hold.card_id AND conflict_card.revision=hold.card_revision
        WHERE hold.active AND hold.card_id=daily_queue.card_id
          AND (("""+scope+""") IS NULL OR hold.repertoire_id=("""+scope+""")))"""


def held_unscoped_card_sql(database):
    """Due admission has no repertoire scope; a current hold must defer it.

    Existing explicit unaffected attempts are not changed. This is only the
    ordinary due-card selection; it neither introduces nor writes another owner.
    """
    if not installed(database):
        return 'FALSE'
    return """EXISTS(SELECT 1 FROM study_conflict_holds hold WHERE hold.active
        AND hold.card_id=cards.id AND hold.card_revision=cards.revision)"""
