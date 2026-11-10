"""Fixed legacy admission queries on the caller connection; no admission policy."""
from tempo_storage.queue_refresh.capacity import read_capacity_route_rows

def retained_generated_card_sql(qualified_card_id):
    """Frozen generated exercises retain introduced status through historical identity."""
    if qualified_card_id not in {'c.id', 'card.id', 'cards.id', 'daily_queue.card_id'}:
        raise ValueError('unsupported_generated_card_reference')
    return 'EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=' + qualified_card_id + ')'
_PRIORITY_OPENING_CANDIDATE_BODY = "SELECT DISTINCT c.id,linked.id repertoire_id,c.moves_json,c.due_date,\n    NULL gameplay_priority_reason,NULL priority_date,NULL priority_score,\n    NULL completed_line_ids_json,NULL frontier_decisions_json\n    FROM cards c JOIN repertoires linked ON (\n        linked.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards candidate_link\n        WHERE candidate_link.card_id=c.id AND candidate_link.repertoire_id=linked.id))\n    WHERE c.content_type='opening' AND c.due_date<=%s AND c.state='new'\n      AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0\n      AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n          WHERE block.repertoire_id=linked.id AND block.card_id=c.id)\n      AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=%s)"

def unseen_opening_page(database, day, after, batch_size):
    cursor = '' if after is None else ' AND (q.position>%s OR (q.position=%s AND q.id>%s))'
    return database.execute("SELECT q.id,q.card_id,q.position,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id\n                   FROM daily_queue q JOIN cards c ON c.id=q.card_id\n                   WHERE q.queue_date=%s AND q.status='queued' AND c.content_type='opening'\n                     AND c.repertoire_id<>'__retained_cards__'\n                     AND COALESCE(q.admission_kind,'')!='explicit'\n                     AND (c.introduced_at IS NULL OR c.introduced_at=%s)\n                     AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)" + ' AND NOT (' + retained_generated_card_sql('c.id') + ')' + cursor + ' ORDER BY q.position,q.id LIMIT %s', (day, day, *((after[0], after[0], after[1]) if after is not None else ()), batch_size)).fetchall()

def introduction_count_rows(database, day, after_repertoire_id, reviewed_only, batch_size):
    statement = "SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)\n            FROM cards c LEFT JOIN daily_queue q\n              ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0\n            WHERE c.content_type='opening' AND c.introduced_at=%s\n              AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)" if reviewed_only else "SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)\n            FROM daily_queue q JOIN cards c ON c.id=q.card_id\n            WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s"
    statement += ' AND NOT (' + retained_generated_card_sql('c.id') + ')'
    parameters = (day,) if reviewed_only else (day, day)
    if after_repertoire_id is not None:
        statement += ' AND COALESCE(q.admission_repertoire_id,c.repertoire_id)>%s'
        parameters += (after_repertoire_id,)
    return database.execute(statement + ' GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id) ORDER BY COALESCE(q.admission_repertoire_id,c.repertoire_id) LIMIT %s', (*parameters, batch_size)).fetchall()

def retain_unseen(database, day, card_id):
    database.execute("UPDATE cards SET introduced_at=%s,state='learning' WHERE id=%s", (day, card_id))

def remove_unseen(database, entry_id, card_id):
    database.execute('DELETE FROM daily_queue WHERE id=%s', (entry_id,))
    database.execute("UPDATE cards SET introduced_at=NULL,state='new' WHERE id=%s", (card_id,))

def priority_rows(database, day, card_ids, miss_reason, *, capacity=False):
    body = _PRIORITY_OPENING_CANDIDATE_BODY
    if capacity:
        body = body.replace("c.state='new'", "c.state='locked'")
    return database.execute(body + ' AND c.id IN (' + ','.join(('%s' for _ in card_ids)) + ')', (day, day, *card_ids)).fetchall()

def candidate_identifiers(database, after_card_id, batch_size):
    return database.execute("SELECT id FROM cards WHERE content_type='opening' AND state IN ('new','locked') AND introduced_at IS NULL AND archived=0 AND COALESCE(pending_validation,0)=0 AND id>%s ORDER BY id LIMIT %s", (after_card_id, batch_size)).fetchall()

def repertoire_identifiers(database, after, batch_size):
    return database.execute('SELECT id FROM repertoires WHERE id>%s ORDER BY id LIMIT %s', (after, batch_size)).fetchall()

def introduced_count(database, day, repertoire_id):
    return database.execute("SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id\n               WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s\n                 AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s", (day, day, repertoire_id)).fetchone()[0]

def current_limit(database, repertoire_id):
    return database.execute('SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) FROM repertoires r CROSS JOIN settings s WHERE r.id=%s AND s.id=1', (repertoire_id,)).fetchone()

def borrowed_capacity_route_rows(database, repertoire_id, parent_ids, batch_size):
    return read_capacity_route_rows(lambda statement, parameters: database.execute(statement, parameters).fetchall(), repertoire_id, parent_ids, batch_size)

def append_opening(database, day, card_id, position, reason, repertoire_id):
    database.execute('INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,admission_repertoire_id)\n               VALUES(%s,%s,%s,%s,%s)', (day, card_id, position, reason, repertoire_id))

def product_source_facts(database, repertoire_id, card_ids, line_ids, *, fence=False):
    """Bounded exact current source rows; no eligibility decision or new writer."""
    if len(card_ids) > 128 or len(line_ids) > 64:
        raise ValueError('product introduction source exceeds current-owner bound')
    suffix = ' FOR SHARE SKIP LOCKED' if fence else ''
    if fence and (not database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))', ('tempo:opening-graph:' + repertoire_id,)).fetchone()[0]):
        return (None, (), (), ())
    header = database.execute_native('SELECT repertoire.scope_source_revision,\n        repertoire.canonical_prefix_revision,repertoire.canonical_prefix_moves_json,scope.generation catalogue_generation,\n        publication.generation graph_generation,publication.state graph_state,\n        identity.target_id,identity.workspace_id,repertoire.new_cards_per_day legacy_repertoire_limit\n        FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope\n        CROSS JOIN legacy_learning_identity identity\n        JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id\n        WHERE repertoire.id=%s AND scope.id=1 AND identity.id=1' + suffix, (repertoire_id,)).fetchone()
    lines = database.execute_native('SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=%s AND id=ANY(%s) ORDER BY id' + suffix, (repertoire_id, list(line_ids))).fetchall()
    cards = database.execute_native('SELECT card.*, EXISTS(\n        SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.repertoire_id=%s AND block.card_id=card.id) blocked,\n        EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id) deleted,\n        (card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards membership\n            WHERE membership.repertoire_id=%s AND membership.card_id=card.id)) member\n        ,EXISTS(SELECT 1 FROM repertoire_cards membership\n            WHERE membership.repertoire_id=%s AND membership.card_id=card.id) linked_member\n        FROM cards card WHERE card.id=ANY(%s) ORDER BY card.id' + suffix, (repertoire_id, repertoire_id, repertoire_id, repertoire_id, list(card_ids))).fetchall()
    graphs = database.execute_native('SELECT * FROM opening_graph_steps WHERE repertoire_id=%s AND generation=(SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s) AND card_id=ANY(%s) ORDER BY line_id,decision_index LIMIT 257' + suffix, (repertoire_id, repertoire_id, list(card_ids))).fetchall()
    return (header, lines, cards, graphs)

def product_current_queue_facts(database, day, *, fence=False):
    suffix = ' FOR SHARE SKIP LOCKED' if fence else ''
    controls = database.execute_native('SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1' + suffix, ()).fetchone()
    introduced = database.execute_native("SELECT card.id,card.introduced_at,\n        COALESCE((SELECT admission_repertoire_id FROM daily_queue WHERE card_id=card.id\n            AND cycle=0 AND queue_date=card.introduced_at ORDER BY id LIMIT 1),card.repertoire_id)\n        FROM cards card WHERE card.content_type='opening' AND card.introduced_at=%s" + ' AND NOT (' + retained_generated_card_sql('card.id') + ')', (day,)).fetchall()
    required = database.execute_native("SELECT c.id,q.gameplay_priority_reason\n        FROM cards c LEFT JOIN daily_queue q ON q.card_id=c.id AND q.queue_date=%s AND q.cycle=0\n        WHERE c.content_type='opening' AND c.archived=0 AND COALESCE(c.pending_validation,0)=0\n        AND c.introduced_at IS NOT NULL AND (q.gameplay_priority_reason IS NOT NULL\n            OR (c.state IN ('learning','mature') AND c.due_date<=%s))\n        ORDER BY CASE WHEN q.gameplay_priority_reason IS NOT NULL THEN 0 ELSE 1 END,c.due_date,c.id", (day, day)).fetchall()
    queued = database.execute_native("SELECT DISTINCT card_id FROM daily_queue WHERE queue_date=%s\n        OR (status='queued' AND attempt_state NOT IN ('clean','unstarted'))", (day,)).fetchall()
    return (controls, introduced, required, frozenset((row[0] for row in queued)))

def product_target_route_page(database, repertoire_id, after_line_id, *, limit=16):
    """Bounded discovery page, never a claimed complete prefix of the source.

    The caller retains the existing repertoire/publication fence and completes
    the stream before trusting relevant alternatives. Oversized rows return an
    explicit sentinel instead of unbounded raw text. No expected-response filter.
    """
    if not 1 <= limit <= 16:
        raise ValueError('target source page exceeds bound')
    return database.execute_native("WITH page AS MATERIALIZED (\n        SELECT id,repertoire_id,trained_color,start_fen,moves_json,\n            octet_length(concat_ws('',id,repertoire_id,trained_color,start_fen,moves_json))>65536 oversized\n        FROM repertoire_lines WHERE repertoire_id=%s AND id>%s ORDER BY id LIMIT %s\n        ) SELECT CASE WHEN octet_length(id)<=200 THEN id END id,repertoire_id,trained_color,\n            CASE WHEN NOT oversized THEN start_fen END start_fen,\n            CASE WHEN NOT oversized THEN moves_json END moves_json,oversized\n        FROM page ORDER BY id", (repertoire_id, after_line_id, limit)).fetchall()

def lock_product_admission(database, day, card_id):
    from tempo_storage.queue_position_lock import lock_queue_date_for_position
    if not database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:card-edit:' + card_id,)).fetchone()[0]:
        return False
    if not database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED', (card_id,)).fetchone():
        return False
    lock_queue_date_for_position(database, day)
    return True

def has_missing_due_card(database, day):
    from tempo_storage.queue_refresh.eligibility import _DUE_CARD_ELIGIBILITY, position_due_exclusion
    return database.execute_native('SELECT 1 FROM cards c WHERE ' + _DUE_CARD_ELIGIBILITY + position_due_exclusion(database) + ' LIMIT 1', (day, day, day)).fetchone() is not None

def introduction_source_binding(header, card, graphs, routes):
    """Exact bounded source facts, excluding adapter-only route sentinels."""
    import json
    route_fields = ('id', 'repertoire_id', 'trained_color', 'start_fen', 'moves_json')
    return json.dumps([dict(header), dict(card), [dict(row) for row in graphs], [{field: row[field] for field in route_fields} for row in routes]], sort_keys=True, default=str, separators=(',', ':'))
