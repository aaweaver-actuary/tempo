"""Existing queue presentation, quarantine and projection operations."""
from tempo_storage import postgres
from tempo_storage.http_composition.availability import available_attempt_sql
_QUARANTINE_OPENING_ELIGIBILITY = "q.queue_date=%s AND q.status='queued'\n    AND c.archived=0 AND c.content_type='opening'\n    AND EXISTS(SELECT 1 FROM repertoires repertoire\n               WHERE (repertoire.id=c.repertoire_id OR EXISTS(\n                   SELECT 1 FROM repertoire_cards link\n                   WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))\n                 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n                                WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id))"
_PROJECTION_BLOCKED_COUNT_SQL = "SELECT COUNT(*) FROM (\n    SELECT queue.card_id FROM daily_queue queue\n    WHERE queue.queue_date=%s AND queue.status='blocked'\n    UNION\n    SELECT card.id FROM cards card\n    WHERE card.content_type='opening' AND card.archived=0\n      AND card.state IN ('learning','mature') AND card.due_date<=%s\n      AND EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n                 WHERE block.card_id=card.id\n                   AND (block.repertoire_id=card.repertoire_id OR EXISTS(\n                       SELECT 1 FROM repertoire_cards link\n                       WHERE link.card_id=card.id\n                         AND link.repertoire_id=block.repertoire_id)))\n) blocked"

def saved_plan(database, queue_date):
    return database.execute('SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=%s', (queue_date,)).fetchone()

def current_membership(database, queue_date):
    return database.execute_native("SELECT id,card_id FROM daily_queue WHERE queue_date=%s AND status='queued' ORDER BY id", (queue_date,)).fetchall()

def has_queue_priority(database, queue_date):
    """Check stored queued reasons while the caller holds the queue-date lock."""
    return bool(database.execute_native("SELECT EXISTS(SELECT 1 FROM daily_queue\n           WHERE queue_date=%s AND status='queued' AND gameplay_priority_reason IS NOT NULL)", (queue_date,)).fetchone()[0])

def locked_membership_hash(database, queue_date):
    return database.execute_native('SELECT membership_hash FROM daily_queue_days WHERE queue_date=%s FOR UPDATE', (queue_date,)).fetchone()

def apply_plan(database, entry_ids, positions, buckets, kinds, queue_date):
    """Apply a plan in the caller's transaction and return its matched membership."""
    if not len(entry_ids) == len(positions) == len(buckets) == len(kinds):
        raise ValueError('Queue plan arrays must have equal lengths')
    if any((type(entry_id) is not int or entry_id <= 0 for entry_id in entry_ids)):
        raise ValueError('Queue plan entry IDs must be positive integers')
    if len(set(entry_ids)) != len(entry_ids):
        raise ValueError('Queue plan entry IDs must be distinct')
    if not entry_ids:
        return 0
    matched = database.execute_native("SELECT id FROM daily_queue\n           WHERE id=ANY(%s::bigint[]) AND queue_date=%s AND status='queued'\n           ORDER BY id FOR UPDATE", (entry_ids, queue_date)).fetchall()
    matched_count = len(matched)
    if matched_count != len(entry_ids):
        return matched_count
    values = (entry_ids, positions, buckets, kinds, queue_date)
    database.execute_native("UPDATE daily_queue AS queue\n           SET position=planned.position,card_bucket=planned.bucket,\n               admission_kind=planned.kind\n           FROM unnest(%s::bigint[],%s::bigint[],%s::text[],%s::text[])\n                AS planned(id,position,bucket,kind)\n           WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'\n             AND queue.admission_kind IS DISTINCT FROM planned.kind", values)
    database.execute_native("UPDATE daily_queue AS queue\n           SET position=planned.position,card_bucket=planned.bucket\n           FROM unnest(%s::bigint[],%s::bigint[],%s::text[],%s::text[])\n                AS planned(id,position,bucket,kind)\n           WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'\n             AND queue.admission_kind IS NOT DISTINCT FROM planned.kind\n             AND (queue.position,queue.card_bucket) IS DISTINCT FROM\n                 (planned.position,planned.bucket)", values)
    return matched_count

def save_plan(database, queue_date, seed, membership_hash, generated_at):
    return database.execute_native('INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)\n           VALUES(%s,%s,%s,%s) ON CONFLICT(queue_date) DO UPDATE SET\n           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at', (queue_date, seed, membership_hash, generated_at))

def locked_quarantine_card(database, queue_date, queue_entry_id, card_id):
    return database.execute_native('SELECT c.start_fen,c.moves_json,\n                  COALESCE(c.trained_color,(SELECT line.trained_color FROM repertoire_lines line\n                           WHERE line.repertoire_id=c.repertoire_id\n                           ORDER BY line.created_at LIMIT 1)) trained_color\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE ' + _QUARANTINE_OPENING_ELIGIBILITY + ' AND q.id=%s AND c.id=%s FOR UPDATE OF q,c', (queue_date, queue_entry_id, card_id)).fetchone()

def lock_incomplete_card(database, card_id):
    return database.execute_native("UPDATE cards SET state='locked' WHERE id=%s", (card_id,))

def skip_incomplete_entry(database, queue_entry_id):
    return database.execute_native("UPDATE daily_queue SET status='skipped' WHERE id=%s", (queue_entry_id,))

def record_incomplete_diagnostic(database, queue_date, card_id, message):
    return database.execute_native('INSERT INTO queue_projection_diagnostics(queue_date,card_id,message)\n           VALUES(%s,%s,%s) ON CONFLICT(queue_date,card_id) DO UPDATE SET\n           message=excluded.message', (queue_date, card_id, message))

def publish_projection(database, queue_date, blocked_count, updated_at):
    return database.execute_native("INSERT INTO queue_projections(queue_date,state,generation,\n                                                   refresh_pending,last_error,\n                                                   blocked_count,updated_at)\n                   VALUES(%s,'ready',1,0,NULL,%s,%s)\n                   ON CONFLICT(queue_date) DO UPDATE SET state='ready',\n                   generation=queue_projections.generation+1,\n                   refresh_pending=0,last_error=NULL,\n                   blocked_count=excluded.blocked_count,\n                   updated_at=excluded.updated_at", (queue_date, blocked_count, updated_at))

def clear_diagnostics(database, queue_date):
    return database.execute_native('DELETE FROM queue_projection_diagnostics WHERE queue_date=%s', (queue_date,))
_QUEUE_RANDOMIZATION_ROWS_SQL = "SELECT q.id,q.card_id,q.position,c.content_type,\n                  CASE WHEN q.admission_kind='explicit' THEN 'explicit'\n                       WHEN EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id AND r.invalidated_at IS NULL) THEN 'review'\n                       ELSE 'new' END admission_kind,\n                  q.gameplay_priority_reason\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND q.status='queued'\n           ORDER BY q.id"

def prepare_presentation_queries(miss_reason: str):
    """Compile immutable availability SQL before opening a short read/write unit.

    Native array parameters keep this one query shape at every queue/page size.
    No data, eligibility result or mutable authority is cached here.
    """
    return available_attempt_sql(miss_reason)

def presentation_facts(database, day, miss_reason: str, *, read=None, after_entry_id=None, limit=None):
    """Capture eligibility and attempt protection without changing membership."""
    if read is None:
        read = lambda statement, parameters: database.execute_native(statement, parameters).fetchall()
    presented = 'q.id IN (SELECT attempt.queue_entry_id FROM opening_evidence_attempts attempt WHERE attempt.queue_entry_id IS NOT NULL)'
    statement = 'SELECT q.id,q.card_id,q.position,c.content_type,q.admission_kind,\n                  q.gameplay_priority_reason,q.cycle,q.attempt_state,q.attempt_failed,\n                  (q.review_result_json IS NOT NULL) review_completed,c.revision,\n                  COALESCE((q.id,c.id,c.revision) IN (\n                    SELECT origin.queue_entry_id,origin.card_id,origin.revision\n                    FROM queue_attempt_origins origin WHERE origin.issued_as_head=1),FALSE) issued,\n                  ' + presented + " presented,\n                  c.id IN (SELECT taught.card_id FROM teaching_states taught) taught\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND q.status='queued'"
    parameters = [day]
    if after_entry_id is not None:
        statement += ' AND q.id>%s'
        parameters.append(after_entry_id)
    statement += ' ORDER BY q.id'
    if limit is not None:
        statement += ' LIMIT %s'
        parameters.append(limit)
    rows = [dict(row) for row in read(statement, tuple(parameters))]
    if not rows:
        return (rows, set(), set())
    identifiers = [row['card_id'] for row in rows]
    frozen = {row[0] for row in read('SELECT presentation_id FROM study_recombination_presentations WHERE presentation_id=ANY(%s)', (identifiers,))}
    for row in rows:
        row['retained_generated'] = row['card_id'] in frozen
    reviewed = {row[0] for row in read('SELECT DISTINCT card_id FROM reviews WHERE invalidated_at IS NULL AND card_id=ANY(%s)', (identifiers,))}
    eligible = {row[0] for row in read(prepare_presentation_queries(miss_reason) + ' AND q.id=ANY(%s)', (day, [row['id'] for row in rows]))}
    return (rows, reviewed, eligible)

def priority_ownership_state(database, day, *, target_card_id=None):
    """Fence queued ownership/identity and issuance, not derived eligibility policy.

    The target's current admission/content/source is independently fenced by Study.
    Ordering never grants eligibility to another card. Changed owners or protected
    attempts invalidate prepared positions; no whole-queue domain query runs here.
    Uncorrelated membership sets avoid a historical-evidence scan per queued row
    when current-day statistics underestimate queue size. Nullable legacy evidence
    IDs must remain False, matching the original EXISTS predicates.
    """
    return database.execute_native("WITH owned AS (\n        SELECT q.id,jsonb_build_array(q.id,q.card_id,q.xmin::text,\n            CASE WHEN c.id=%s THEN jsonb_build_array(c.id,c.revision,c.repertoire_id,c.kind,\n                c.start_fen,c.moves_json,c.trained_color,c.content_type,c.archived,\n                c.pending_validation,c.superseded_by)::text ELSE c.xmin::text END,\n            COALESCE((q.id,c.id,c.revision) IN (SELECT o.queue_entry_id,o.card_id,o.revision\n                FROM queue_attempt_origins o WHERE o.issued_as_head=1),FALSE),\n            q.id IN (SELECT a.queue_entry_id FROM opening_evidence_attempts a\n                WHERE a.queue_entry_id IS NOT NULL),\n            c.id IN (SELECT t.card_id FROM teaching_states t))::text value\n        FROM daily_queue q JOIN cards c ON c.id=q.card_id\n        WHERE q.queue_date=%s AND q.status='queued'\n    ) SELECT encode(sha256(convert_to(\n        COALESCE((SELECT string_agg(value,',' ORDER BY id) FROM owned),'') || '|' ||\n        COALESCE((SELECT xmin::text FROM frozen_opening_activation WHERE id=1),'') || '|' ||\n        COALESCE((SELECT xmin::text FROM settings WHERE id=1),''),'UTF8')),'hex')", (target_card_id, day)).fetchone()[0]
