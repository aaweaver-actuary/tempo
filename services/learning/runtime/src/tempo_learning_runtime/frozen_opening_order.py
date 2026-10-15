from __future__ import annotations
import json
from .frozen_maia_math import content_fingerprint,READER_VERSION
from .real_game_feedback import MISS_REASON

def activation(database, *, lock=False, exclusive=False):
    from .postgres_store import PostgresConnection
    suffix = (' FOR UPDATE' if exclusive else ' FOR SHARE') if (
        lock and isinstance(database, PostgresConnection) and not database.read_only
    ) else ''
    row = database.execute('SELECT run_id,epoch FROM frozen_opening_activation WHERE id=1' + suffix).fetchone()
    return (row[0], int(row[1])) if row else (None, 0)

def annotate(database, candidates):
    run_id, epoch = activation(database)
    result = [dict(row) for row in candidates]
    if run_id is None:
        return [{**row, 'frozen_token': [None, epoch]} for row in result]
    identifiers = sorted({row.get('card_id', row.get('id')) for row in result})
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
            valid = card is not None and content_fingerprint(dict(card), memberships) == rank['fingerprint']
            ranks[rank['card_id']] = (rank['ordinal'], valid)
    for row in result:
        row['frozen_token'] = [run_id, epoch]
        ordinal, valid = ranks.get(row.get('card_id', row.get('id')), (None, True))
        row['frozen_rank'] = ordinal
        row['frozen_legacy_protected'] = row.get('card_id', row.get('id')) in legacy_cards
        row['frozen_valid'] = valid and not row['frozen_legacy_protected']
        row['frozen_route_valid'] = (row.get('card_id',row.get('id')),row.get('repertoire_id')) not in blocked_memberships
    return result

def proposal_valid(database, planned):
    token = list(activation(database, lock=True))
    if planned.get('frozen_token', [None, 0]) != token:
        return False
    if token[0] is None:
        return True
    if planned.get('reason') == MISS_REASON:
        from .real_game_feedback import has_outstanding_real_game_miss
        return has_outstanding_real_game_miss(database, planned['card_id'])
    return annotate(database, [{'card_id': planned['card_id']}])[0]['frozen_valid']

def order_queue_slots(database, rows, *, preserve_through_entry_id=None, annotated=None, exposed_ids=None):
    """Swap only valid captured unstarted introductions in their existing slots."""
    annotated = annotate(database, rows) if annotated is None else annotated
    movable_indices = []
    for index, row in enumerate(annotated):
        if row.get('content_type') != 'opening' or row.get('admission_kind') != 'new' or row.get('gameplay_priority_reason') == MISS_REASON:
            continue
        if not row.get('frozen_valid'):
            continue
        if preserve_through_entry_id is not None and row['id'] <= preserve_through_entry_id:
            continue
        exposed = row['card_id'] in exposed_ids if exposed_ids is not None else card_exposed(database, row['card_id'])
        if exposed:
            continue
        movable_indices.append(index)
    ranked = sorted((annotated[index] for index in movable_indices), key=lambda row: (row['frozen_rank'] if row['frozen_rank'] is not None else float('inf'), row.get('position',row['id']), row['id']))
    for index, row in zip(movable_indices, ranked):
        annotated[index] = row
    return annotated

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

def queue_hash(membership_hash, token):
    from .opening_segmentation import stable_key
    return membership_hash if list(token) == [None, 0] else stable_key(membership_hash, list(token))

def queue_state(database, day):
    from .opening_segmentation import stable_key
    rows = database.execute("""SELECT q.id,q.card_id,q.position,q.attempt_state,q.attempt_failed,
        q.review_result_json,c.revision,
        EXISTS(SELECT 1 FROM queue_attempt_origins origin WHERE origin.card_id=c.id
               AND origin.issued_as_head=1) AS issued,
        EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.card_id=c.id) AS presented
        FROM daily_queue q JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=? AND q.status='queued' ORDER BY q.id""", (day,)).fetchall()
    return stable_key([tuple(row) for row in rows])

def stale_queue_predicate():
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
        AND COALESCE(q.gameplay_priority_reason,'')<>""" + "'" + MISS_REASON.replace("'", "''") + "'" + """
        AND q.attempt_failed=0 AND q.review_result_json IS NULL
        AND q.attempt_state='clean'
        AND NOT EXISTS(SELECT 1 FROM reviews reviewed WHERE reviewed.card_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins origin
                       WHERE origin.card_id=c.id AND origin.issued_as_head=1)) """

def legacy_queue_predicate():
    """Hold uncertain, unstarted legacy openings while a frozen run is active."""
    from .real_game_feedback import outstanding_miss_sql

    mandatory_miss = outstanding_miss_sql(
        columns="1", additional_where="AND card.id=c.id LIMIT 1",
    )
    miss_reason = MISS_REASON.replace("'", "''")
    return """ AND NOT (
        c.content_type='opening'
        AND EXISTS(SELECT 1 FROM frozen_opening_activation active
                   WHERE active.id=1 AND active.run_id IS NOT NULL)
        AND EXISTS(SELECT 1 FROM queue_attempt_origins legacy_origin
                   WHERE legacy_origin.card_id=c.id AND legacy_origin.legacy=1)
        AND COALESCE(q.admission_kind,'')<>'explicit'
        AND NOT (q.gameplay_priority_reason='""" + miss_reason + "' AND EXISTS(" + mandatory_miss + "))" + """
        AND NOT EXISTS(SELECT 1 FROM reviews reviewed WHERE reviewed.card_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM opening_evidence_attempts attempt WHERE attempt.card_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM teaching_states taught WHERE taught.card_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins exposed_origin
            WHERE exposed_origin.card_id=c.id AND
              (exposed_origin.issued_as_head=1 OR exposed_origin.attempt_failed<>0
               OR exposed_origin.review_result_json IS NOT NULL OR exposed_origin.last_status='complete'))
        AND NOT EXISTS(SELECT 1 FROM daily_queue exposed_queue
            WHERE exposed_queue.card_id=c.id AND
              (exposed_queue.attempt_failed<>0 OR exposed_queue.review_result_json IS NOT NULL
               OR exposed_queue.status='complete' OR exposed_queue.attempt_state NOT IN ('clean','unstarted')))
    ) """
