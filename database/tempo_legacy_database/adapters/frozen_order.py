"""Frozen-order persistence on the caller's connection/transaction.

SQL, dialect choices, row locks and atomic staging/rollback guards live here.
Preference and eligibility decisions consume the public Study fact interface.
"""
from __future__ import annotations
import json
from app.study.frozen_order.interfaces import FrozenAnnotationFacts, FrozenRankFacts


def activation(database, *, lock=False, exclusive=False):
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    suffix = (' FOR UPDATE' if exclusive else ' FOR SHARE') if (
        lock and isinstance(database, PostgresConnection) and not database.read_only
    ) else ''
    row = database.execute('SELECT run_id,epoch FROM frozen_opening_activation WHERE id=1' + suffix).fetchone()
    return (row[0], int(row[1])) if row else (None, 0)


def card_exposed(database, card_id):
    # A receipt/presentation, teaching checkpoint, or issued head is exposure,
    # regardless of current lifecycle or whether a review completed.
    for statement in (
        'SELECT 1 FROM reviews WHERE card_id=? LIMIT 1',
        'SELECT 1 FROM opening_evidence_attempts WHERE card_id=? LIMIT 1',
        'SELECT 1 FROM teaching_states WHERE card_id=? LIMIT 1',
        "SELECT 1 FROM queue_attempt_origins WHERE card_id=? AND (issued_as_head=1 OR attempt_failed<>0 OR review_result_json IS NOT NULL OR last_status='complete') LIMIT 1",
        "SELECT 1 FROM daily_queue WHERE card_id=? AND (attempt_failed<>0 OR review_result_json IS NOT NULL OR status='complete' OR attempt_state NOT IN ('clean','unstarted')) LIMIT 1",
    ):
        if database.execute(statement, (card_id,)).fetchone():
            return True
    return False


def has_legacy_origin(database, card_id: str) -> bool:
    """Whether history prevents proving that this card has never been shown."""
    return database.execute(
        "SELECT 1 FROM queue_attempt_origins WHERE card_id=? AND legacy=1 LIMIT 1",
        (card_id,),
    ).fetchone() is not None


def stage_run(database, plan, now, *, reader_version: str):
    previous, _ = activation(database)
    existing = database.execute('SELECT plan_checksum FROM frozen_opening_runs WHERE run_id=?', (plan['run_id'],)).fetchone()
    if existing and existing[0] != plan['checksum']:
        raise ValueError('Conflicting run identity')
    database.execute('INSERT INTO frozen_opening_runs(run_id,plan_checksum,plan_json,previous_run_id,created_at,reader_version) VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING',
                     (plan['run_id'], plan['checksum'], json.dumps({key: value for key, value in plan.items() if key not in ('inventory','ranks','decision_evidence','covered_decisions','previous_order')}, sort_keys=True), previous, now, reader_version))
    database.execute('INSERT INTO frozen_opening_pending(run_id) VALUES(?) ON CONFLICT(run_id) DO NOTHING', (plan['run_id'],))


def stage_ranks(database, plan, ranks):
    for row in ranks:
        fields=('revision','start_fen','moves_json','trained_color','source_ref','source_fen','kind','content_type')
        definition={field:row[field] for field in fields if field in row}
        if len(definition)<len(fields):
            card=database.execute('SELECT * FROM cards WHERE id=?',(row['id'],)).fetchone()
            definition={field:dict(card).get(field) for field in fields}
        definition['membership_count']=len(row['memberships'])
        database.execute('INSERT INTO frozen_opening_ranks(run_id,card_id,ordinal,fingerprint,memberships_json,definition_json) VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,card_id) DO NOTHING',
                         (plan['run_id'], row['id'], row['rank'], row['fingerprint'], json.dumps(row['memberships']),json.dumps(definition)))


def activate_run(database, plan):
    current, _ = activation(database,lock=True,exclusive=True)
    if current == plan['run_id']:
        return False
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    lock_clause=' FOR UPDATE' if isinstance(database,PostgresConnection) else ''
    saved = database.execute('SELECT previous_run_id,invalidated FROM frozen_opening_runs WHERE run_id=?'+lock_clause, (plan['run_id'],)).fetchone()
    if saved is not None and saved[1]:
        raise ValueError('Stale plan: study or source inputs changed during apply')
    if saved is None or saved[0] != current:
        raise ValueError('Activation conflict: active order changed')
    rows = database.execute('SELECT card_id,ordinal,fingerprint FROM frozen_opening_ranks WHERE run_id=? ORDER BY ordinal', (plan['run_id'],)).fetchall()
    if [tuple(row) for row in rows] != [(row['id'], row['rank'], row['fingerprint']) for row in plan['ranks']]:
        raise ValueError('Incomplete or conflicting staged ranks')
    database.execute('UPDATE frozen_opening_activation SET run_id=?,epoch=epoch+1 WHERE id=1', (plan['run_id'],))
    database.execute('DELETE FROM frozen_opening_pending WHERE run_id=?', (plan['run_id'],))
    return True


def rollback_run(database, run_id):
    current, _ = activation(database,lock=True,exclusive=True)
    if current != run_id:
        if database.execute('SELECT 1 FROM frozen_opening_pending WHERE run_id=?', (run_id,)).fetchone():
            database.execute('DELETE FROM frozen_opening_pending WHERE run_id=?', (run_id,))
            return current
        raise ValueError('Rollback conflict: this run is not active')
    saved = database.execute('SELECT previous_run_id FROM frozen_opening_runs WHERE run_id=?', (run_id,)).fetchone()
    database.execute('UPDATE frozen_opening_activation SET run_id=?,epoch=epoch+1 WHERE id=1', (saved[0],))
    # Immutable publications remain as audit/rollback evidence; no study data written.
    return saved[0]


_ROLLBACK_CONTENT_FIELDS = ('revision','start_fen','moves_json','trained_color','source_ref','source_fen')


def record_queue_changes(database, queue_date, entries):
    """Record only our position mutations, in the queue writer's transaction."""
    run_id, _ = activation(database)
    if run_id is None:
        return
    planned_positions = {entry['id']:entry['position'] for entry in entries}
    current = database.execute('SELECT q.id,q.card_id,q.position,c.* FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=? AND q.status=\'queued\'', (queue_date,)).fetchall()
    mutations = []
    for row in current:
        after_position = planned_positions.get(row[0],row[2])
        if after_position == row[2]:
            continue
        definition = json.dumps({field:row[field] for field in _ROLLBACK_CONTENT_FIELDS},sort_keys=True)
        mutations.append((run_id,row[0],queue_date,row[1],row[2],after_position,definition))
    if mutations:
        database.executemany('INSERT INTO frozen_opening_queue_changes(run_id,queue_id,queue_date,card_id,before_position,after_position,definition_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,queue_id) DO UPDATE SET after_position=excluded.after_position', mutations)


def restore_queue_changes(database, run_id):
    """Restore owned unchanged permutations; later study/content/order wins."""
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    changes = database.execute('SELECT * FROM frozen_opening_queue_changes WHERE run_id=? ORDER BY queue_date,queue_id',(run_id,)).fetchall()
    safe = {}
    conflicts = []
    occupied = {}
    for day in sorted({row['queue_date'] for row in changes}):
        if isinstance(database,PostgresConnection):
            database.execute_native('SELECT id FROM daily_queue WHERE queue_date=%s FOR UPDATE',(day,)).fetchall()
        for row in database.execute('SELECT id,position FROM daily_queue WHERE queue_date=?',(day,)).fetchall():
            occupied.setdefault((day,row['position']),set()).add(row['id'])
    for change in changes:
        current = database.execute('SELECT q.card_id,q.position,q.status,c.* FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=? AND q.queue_date=?',(change['queue_id'],change['queue_date'])).fetchone()
        valid = current is not None and current[0]==change['card_id'] and current[1]==change['after_position'] and current[2]=='queued'
        if valid:
            valid = (json.loads(change['definition_json'])=={field:current[field] for field in _ROLLBACK_CONTENT_FIELDS}
                     and not card_exposed(database,change['card_id'])
                     and not has_legacy_origin(database,change['card_id']))
        if valid:safe[change['queue_id']]=change
        else:conflicts.append(change['queue_id'])
    # A surviving half of a swap must never displace a protected occupant.
    while True:
        rejected = [identifier for identifier,change in safe.items() if not occupied.get((change['queue_date'],change['before_position']),set()) <= safe.keys()]
        if not rejected:break
        for identifier in rejected:
            conflicts.append(identifier);del safe[identifier]
    if safe:
        database.executemany('UPDATE daily_queue SET position=? WHERE id=?',[(change['before_position'],identifier) for identifier,change in safe.items()])
    return {'restored_queue_entries':sorted(safe),'queue_conflicts':sorted(conflicts)}


def queue_state_rows(database, day):
    rows = database.execute("""SELECT q.id,q.card_id,q.position,q.attempt_state,q.attempt_failed,
        q.review_result_json,c.revision,
        EXISTS(SELECT 1 FROM queue_attempt_origins origin WHERE origin.card_id=c.id
               AND origin.issued_as_head=1) AS issued,
        EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.card_id=c.id) AS presented
        FROM daily_queue q JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=? AND q.status='queued' ORDER BY q.id""", (day,)).fetchall()
    return [tuple(row) for row in rows]


def stale_queue_predicate(miss_reason: str):
    # Preserve previously issued/studied attempts. Hold only unstarted captured
    # content whose immutable definition no longer matches the serving card.
    changed = " OR ".join(
        f"COALESCE(CAST(json_extract(frozen.definition_json,'$.{field}') AS TEXT),'')<>COALESCE(CAST(c.{field} AS TEXT),'')"
        for field in ('revision','start_fen','moves_json','trained_color','source_ref','source_fen','kind','content_type'))
    # Repertoire identifiers are UUIDs (or retained system identifiers). Exact
    # quoted identifiers plus cardinality detect both added and removed links.
    changed += """ OR CAST(json_extract(frozen.definition_json,'$.membership_count') AS TEXT)<>
        CAST((SELECT COUNT(*) FROM (SELECT repertoire_id FROM repertoire_cards link WHERE link.card_id=c.id UNION SELECT c.repertoire_id) current_memberships) AS TEXT)
        OR instr(frozen.memberships_json,'"' || c.repertoire_id || '"')=0
        OR EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=c.id
                  AND instr(frozen.memberships_json,'"' || link.repertoire_id || '"')=0)"""
    return """ AND NOT EXISTS(SELECT 1 FROM frozen_opening_ranks frozen
        JOIN frozen_opening_activation active ON active.id=1 AND active.run_id=frozen.run_id
        WHERE frozen.card_id=c.id AND (""" + changed + """ )
        AND COALESCE(q.gameplay_priority_reason,'')<>""" + "'" + miss_reason.replace("'", "''") + "'" + """
        AND q.attempt_failed=0 AND q.review_result_json IS NULL
        AND q.attempt_state='clean'
        AND NOT EXISTS(SELECT 1 FROM reviews reviewed WHERE reviewed.card_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id)
        AND c.id NOT IN(SELECT origin.card_id FROM queue_attempt_origins origin
                        WHERE origin.issued_as_head=1 AND origin.card_id IS NOT NULL)) """


def legacy_queue_predicate(miss_reason: str):
    """Hold uncertain, unstarted legacy openings while a frozen run is active."""
    from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_sql

    mandatory_miss = outstanding_miss_sql(
        columns="1", additional_where="AND card.id=c.id LIMIT 1",
    )
    miss_reason = miss_reason.replace("'", "''")
    # Evaluate historical presentation/origin membership once, preserving
    # NULL-as-absent semantics, rather than rescanning it for each queued card.
    return """ AND NOT (
        c.content_type='opening'
        AND EXISTS(SELECT 1 FROM frozen_opening_activation active
                   WHERE active.id=1 AND active.run_id IS NOT NULL)
        AND c.id IN(SELECT legacy_origin.card_id FROM queue_attempt_origins legacy_origin
                    WHERE legacy_origin.legacy=1 AND legacy_origin.card_id IS NOT NULL)
        AND COALESCE(q.admission_kind,'')<>'explicit'
        AND NOT (q.gameplay_priority_reason='""" + miss_reason + "' AND EXISTS(" + mandatory_miss + "))" + """
        AND NOT EXISTS(SELECT 1 FROM reviews reviewed WHERE reviewed.card_id=c.id)
        AND c.id NOT IN(SELECT attempt.card_id FROM opening_evidence_attempts attempt
                         WHERE attempt.card_id IS NOT NULL)
        AND NOT EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id)
        AND c.id NOT IN(SELECT exposed_origin.card_id FROM queue_attempt_origins exposed_origin
            WHERE exposed_origin.card_id IS NOT NULL AND
              (exposed_origin.issued_as_head=1 OR exposed_origin.attempt_failed<>0
               OR exposed_origin.review_result_json IS NOT NULL OR exposed_origin.last_status='complete'))
        AND NOT EXISTS(SELECT 1 FROM daily_queue exposed_queue
            WHERE exposed_queue.card_id=c.id AND
              (exposed_queue.attempt_failed<>0 OR exposed_queue.review_result_json IS NOT NULL
               OR exposed_queue.status='complete' OR exposed_queue.attempt_state NOT IN ('clean','unstarted')))
    ) """


def annotation_facts(database, identifiers, *, token) -> FrozenAnnotationFacts:
    run_id, epoch = token
    if run_id is None:
        return FrozenAnnotationFacts((None, epoch), {}, set(), set())
    ranks = {}
    legacy_cards = set()
    blocked_memberships = set()
    # Bounded indexed reads; connections and transactions belong to the caller.
    for offset in range(0, len(identifiers), 16):
        page = identifiers[offset:offset + 16]
        if not page:
            continue
        placeholders = ','.join('?' for _ in page)
        blocked_memberships.update((row[0],row[1]) for row in database.execute('SELECT card_id,repertoire_id FROM repertoire_integrity_card_blocks WHERE card_id IN ('+placeholders+')',tuple(page)).fetchall())
        legacy_cards.update(row[0] for row in database.execute(
            'SELECT DISTINCT card_id FROM queue_attempt_origins WHERE legacy=1 AND card_id IN (' + placeholders + ')',
            tuple(page),
        ).fetchall())
        captured = database.execute(
            'SELECT * FROM frozen_opening_ranks WHERE run_id=? AND card_id IN (' + placeholders + ')',
            (run_id, *page),
        ).fetchall()
        for rank in captured:
            card = database.execute('SELECT * FROM cards WHERE id=?', (rank['card_id'],)).fetchone()
            memberships = sorted({row[0] for row in database.execute(
                'SELECT repertoire_id FROM repertoire_cards WHERE card_id=? UNION SELECT repertoire_id FROM cards WHERE id=?',
                (rank['card_id'], rank['card_id']),
            ).fetchall() if row[0] is not None})
            ranks[rank['card_id']] = FrozenRankFacts(rank['ordinal'], rank['fingerprint'], dict(card) if card is not None else None, tuple(memberships))
    return FrozenAnnotationFacts((run_id, epoch), ranks, legacy_cards, blocked_memberships)
