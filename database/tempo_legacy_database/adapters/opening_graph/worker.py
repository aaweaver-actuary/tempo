"""Existing graph staging/publication/cleanup queries; caller owns policy and transaction."""

from .. import postgres_store


def lock_transition_reservations(database):
    database.execute_native("SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))")


def lock_graph_rebuild(database, repertoire_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:opening-graph:{repertoire_id}",),
    )


def insert_cards(database, card_rows, *, strict=False):
    database.execute_native(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,"
        "due_date,content_type,trained_color,pending_validation,canonical_route_source) "
        "SELECT prepared.card_id,prepared.repertoire_id,prepared.kind,prepared.start_fen,"
        "prepared.moves_json,prepared.state,prepared.due_date,'opening',prepared.trained_color,0,0 "
        "FROM UNNEST(%s::text[],%s::text[],%s::text[],%s::text[],%s::text[],"
        "%s::text[],%s::text[],%s::text[]) AS prepared("
        "card_id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color) "
        + ("" if strict else "ON CONFLICT(id) DO NOTHING"),
        tuple(list(column) for column in zip(*card_rows)),
    )


def publish_generation(database, values):
    database.execute_native(
        "INSERT INTO opening_graph_publications(repertoire_id,generation,state,last_error,published_at) "
        "VALUES(%s,%s,'ready',NULL,%s) ON CONFLICT(repertoire_id) DO UPDATE SET "
        "generation=excluded.generation,state='ready',last_error=NULL,"
        "published_at=excluded.published_at",
        values,
    )


def retire_orphaned_queue(database, eligible_card_ids, repertoire_id):
    database.execute_native(
        "UPDATE daily_queue queue SET status='superseded' "
        "WHERE queue.card_id=ANY(%s::text[]) AND queue.status='queued' "
        "AND EXISTS(SELECT 1 FROM cards card WHERE card.id=queue.card_id "
        "AND (card.canonical_route_source=0 OR card.repertoire_id=%s)) "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_cards retained "
        "WHERE retained.card_id=queue.card_id AND retained.repertoire_id<>%s)",
        (eligible_card_ids, repertoire_id, repertoire_id),
    )


def archive_generated_owner(database, eligible_card_ids, repertoire_id):
    database.execute_native(
        "UPDATE cards SET archived=1 WHERE id=ANY(%s::text[]) AND repertoire_id=%s "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_cards retained "
        "WHERE retained.card_id=cards.id AND retained.repertoire_id<>%s)",
        (eligible_card_ids, repertoire_id, repertoire_id),
    )


def reassign_retained_owner(database, repertoire_id, eligible_card_ids):
    database.execute_native(
        "UPDATE cards SET repertoire_id=(SELECT MIN(retained.repertoire_id) "
        "FROM repertoire_cards retained WHERE retained.card_id=cards.id "
        "AND retained.repertoire_id<>%s) WHERE id=ANY(%s::text[]) AND repertoire_id=%s "
        "AND EXISTS(SELECT 1 FROM repertoire_cards retained "
        "WHERE retained.card_id=cards.id AND retained.repertoire_id<>%s)",
        (repertoire_id, eligible_card_ids, repertoire_id, repertoire_id),
    )


def delete_generated_links(database, repertoire_id, eligible_card_ids):
    database.execute_native(
        "DELETE FROM repertoire_cards WHERE repertoire_id=%s AND card_id=ANY(%s::text[]) "
        "AND canonical_route_source=0", (repertoire_id, eligible_card_ids),
    )


def delete_obsolete_blocks(database, repertoire_id, eligible_card_ids):
    database.execute_native(
        "DELETE FROM repertoire_integrity_card_blocks "
        "WHERE repertoire_id=%s AND card_id=ANY(%s::text[])", (repertoire_id, eligible_card_ids),
    )


def archive_unlinked_generated_cards(database, eligible_card_ids):
    database.execute_native(
        "UPDATE cards SET archived=1 WHERE id=ANY(%s::text[]) AND canonical_route_source=0 "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=cards.id)",
        (eligible_card_ids,),
    )


def insert_steps(cursor, values):
    cursor.executemany(
        "INSERT INTO opening_graph_steps("
        "repertoire_id,generation,line_id,decision_index,segment_kind,"
        "first_decision_index,last_decision_index,decision_fen_keys_json,"
        "card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color"
        ") VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(repertoire_id,generation,line_id,decision_index) DO NOTHING",
        values,
    )


def lock_card_edit(database, identifier):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{identifier}",))


def insert_links(cursor, values):
    cursor.executemany(
        "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) "
        "SELECT %s,%s,0 WHERE EXISTS(SELECT 1 FROM opening_graph_steps "
        "WHERE repertoire_id=%s AND generation=%s AND card_id=%s) "
        "ON CONFLICT(repertoire_id,card_id) DO NOTHING",
        values,
    )


def classify_card(database, kind, next_state, study_day, card_id):
    database.execute_native(
        "UPDATE cards SET archived=0,kind=%s,"
        "state=CASE WHEN state IN ('new','locked') AND introduced_at IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id) "
        "THEN %s ELSE state END,"
        "due_date=CASE WHEN %s='new' AND state='locked' "
        "AND introduced_at IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id) "
        "THEN %s ELSE due_date END WHERE id=%s AND canonical_route_source=0",
        (kind, next_state, next_state, study_day, card_id),
    )


def delete_card_blocks(database, repertoire_id, card_id):
    database.execute_native(
        "DELETE FROM repertoire_integrity_card_blocks "
        "WHERE repertoire_id=%s AND card_id=%s", (repertoire_id, card_id),
    )


def insert_card_blocks(database, values):
    database.execute_native(
        "INSERT INTO repertoire_integrity_card_blocks("
        "repertoire_id,card_id,issue_id,scan_generation,published_at) "
        "SELECT DISTINCT %s,%s,issue.id,%s,%s FROM opening_graph_steps step "
        "CROSS JOIN LATERAL jsonb_array_elements_text("
        "step.decision_fen_keys_json::jsonb) position(fen_key) "
        "JOIN repertoire_integrity_issues issue "
        "ON issue.repertoire_id=step.repertoire_id AND issue.fen_key=position.fen_key "
        "WHERE step.repertoire_id=%s AND step.generation=%s AND step.card_id=%s "
        "ON CONFLICT(repertoire_id,card_id,issue_id) DO NOTHING",
        values,
    )


def refresh_validation(database, card_id):
    database.execute_native(
        "UPDATE cards SET pending_validation=CASE WHEN EXISTS("
        "SELECT 1 FROM repertoire_integrity_card_blocks block "
        "WHERE block.card_id=cards.id) THEN 1 ELSE 0 END WHERE id=%s",
        (card_id,),
    )


def lock_graph_task(database, repertoire_id):
    return database.execute_native(
        "SELECT id FROM background_tasks WHERE kind='opening_graph_rebuild' "
        "AND deduplication_key=%s FOR UPDATE", (repertoire_id,),
    ).fetchone()


def latest_staged_generation(database, repertoire_id):
    return database.execute_native(
        "SELECT generation FROM opening_graph_steps WHERE repertoire_id=%s "
        "ORDER BY generation DESC LIMIT 1", (repertoire_id,),
    ).fetchone()


def published_generation(database, repertoire_id):
    return database.execute_native(
        "SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s",
        (repertoire_id,),
    ).fetchone()


def deleted_card_rows(database, route_ids):
    return database.execute_native("SELECT card_id FROM deleted_cards WHERE card_id=ANY(%s)", (route_ids,))


def missing_link(database, repertoire_id, generation):
    return database.execute_native(
        "SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=%s "
        "AND step.generation=%s AND NOT EXISTS("
        "SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=step.repertoire_id "
        "AND link.card_id=step.card_id) LIMIT 1",
        (repertoire_id, generation),
    ).fetchone()


def next_source_line(database, repertoire_id, after_line_id):
    return database.execute_native(
        "SELECT line.*,depth.learner_decision_count "
        "FROM repertoire_lines line "
        "LEFT JOIN repertoire_line_training_depths depth ON depth.line_id=line.id "
        "WHERE line.repertoire_id=%s AND line.id>%s ORDER BY line.id LIMIT 1",
        (repertoire_id, after_line_id),
    ).fetchone()


def staged_card_ids(database, repertoire_id, generation, after_card_id, limit):
    return database.execute_native(
        "SELECT card_id FROM opening_graph_steps WHERE repertoire_id=%s "
        "AND generation=%s AND card_id>%s GROUP BY card_id ORDER BY card_id LIMIT %s",
        (repertoire_id, generation, after_card_id, limit),
    ).fetchall()


def classification(database, card_id):
    return database.execute_native(
        "SELECT BOOL_OR(step.segment_kind='prefix'),"
        "BOOL_OR(step.parent_card_id IS NULL) "
        "FROM opening_graph_steps step "
        "JOIN opening_graph_publications publication "
        "ON publication.repertoire_id=step.repertoire_id "
        "AND publication.generation=step.generation "
        "WHERE step.card_id=%s",
        (card_id,),
    ).fetchone()


def cleanup_candidates(database, repertoire_id, after_card_id, candidate_page_size, generation):
    return database.execute_native(
        "WITH candidate_cards AS MATERIALIZED ("
        "SELECT card_id,canonical_route_source FROM repertoire_cards "
        "WHERE repertoire_id=%s AND card_id>%s ORDER BY card_id LIMIT %s) "
        "SELECT candidate.card_id,CASE WHEN card.content_type='opening' "
        "AND candidate.canonical_route_source=0 "
        "THEN NOT EXISTS(SELECT 1 FROM opening_graph_steps step "
        "WHERE step.repertoire_id=%s AND step.generation=%s "
        "AND step.card_id=candidate.card_id) ELSE FALSE END obsolete "
        "FROM candidate_cards candidate JOIN cards card ON card.id=candidate.card_id "
        "ORDER BY candidate.card_id",
        (repertoire_id, after_card_id, candidate_page_size, repertoire_id, generation),
    ).fetchall()


def protected_queue_entry(database, values):
    return database.execute_native(
        "SELECT 1 FROM daily_queue queue WHERE queue.card_id=ANY(%s::text[]) "
        "AND (EXISTS(SELECT 1 FROM queue_attempt_origins origin "
        "WHERE origin.queue_entry_id=queue.id AND origin.issued_as_head=1) "
        "OR queue.id=(SELECT head.id FROM daily_queue head WHERE head.queue_date=%s "
        "AND head.status='queued' ORDER BY head.position,head.id LIMIT 1)) LIMIT 1",
        values,
    ).fetchone()


def initial_depth(database):
    return database.execute_native(
        "SELECT initial_depth FROM settings WHERE id=1"
    ).fetchone()[0]


def prefix_override_rows(database):
    return database.execute_native(
        "SELECT split.source_card_id,split.shortened_card_id,"
        "shortened.start_fen shortened_start_fen,"
        "shortened.moves_json shortened_moves_json,"
        "split.continuation_card_id,"
        "continuation.start_fen continuation_start_fen,"
        "continuation.moves_json continuation_moves_json "
        "FROM prefix_splits split "
        "JOIN cards shortened ON shortened.id=split.shortened_card_id "
        "JOIN cards continuation ON continuation.id=split.continuation_card_id"
    )


def protected_card(database, card_id, kind):
    return database.execute_native(
        "SELECT 1 FROM cards WHERE id=%s AND kind<>%s AND (introduced_at IS NOT NULL "
        "OR EXISTS(SELECT 1 FROM reviews WHERE card_id=cards.id) "
        "OR EXISTS(SELECT 1 FROM queue_attempt_origins WHERE card_id=cards.id AND issued_as_head=1))",
        (card_id, kind),
    ).fetchone()


def eligible_cleanup_cards(database, repertoire_id, card_ids, generation):
    return database.execute_native(
        "SELECT link.card_id FROM repertoire_cards link JOIN cards card ON card.id=link.card_id "
        "WHERE link.repertoire_id=%s AND link.card_id=ANY(%s::text[]) "
        "AND link.canonical_route_source=0 AND NOT EXISTS("
        "SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=%s "
        "AND step.generation=%s AND step.card_id=link.card_id) "
        "ORDER BY link.card_id FOR UPDATE OF link,card",
        (repertoire_id, list(card_ids), repertoire_id, generation),
    ).fetchall()


def graph_cursor(database):
    return database.raw.cursor()


def writer_connection():
    return postgres_store.connection(read_only=False, background=True)
