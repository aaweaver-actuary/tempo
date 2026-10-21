"""Existing native integrity storage; caller owns transaction, generation and task policy."""


def integrity_cursor(database):
    return database.raw.cursor()


def certificate_facts(database, values):
    return database.execute_native(
        """SELECT state.status integrity_status,state.scan_status,
        state.scan_generation,state.checked_at,state.scan_completed_sources,state.scan_total_sources,state.scan_error,
        graph.generation graph_task_generation,graph.state graph_task_state,
        scan.id scan_task_id,scan.generation scan_task_generation,scan.state scan_task_state,scan.payload_json
        FROM repertoire_integrity_state state
        JOIN background_tasks graph ON graph.kind='opening_graph_rebuild' AND graph.deduplication_key=state.repertoire_id
        JOIN background_tasks scan ON scan.kind='integrity_scan' AND scan.deduplication_key=state.repertoire_id
        WHERE state.repertoire_id=%s""",
        values,
    ).fetchone()


def queue_scan_state(database, values):
    return database.execute_native(
        "UPDATE repertoire_integrity_state SET scan_status='queued' "
        "WHERE repertoire_id=%s",
        values,
    )


def set_scan_generation(database, values):
    return database.execute_native(
        "UPDATE repertoire_integrity_state SET scan_generation=%s "
        "WHERE repertoire_id=%s",
        values,
    )


def first_source_color(database, values):
    return database.execute_native(
        "SELECT trained_color FROM repertoire_lines WHERE repertoire_id=%s "
            "ORDER BY created_at,id LIMIT 1",
        values,
    ).fetchone()


def next_line_source(database, values):
    return database.execute_native(
        "SELECT id,repertoire_id,name,trained_color,start_fen,moves_json,created_at "
                "FROM repertoire_lines WHERE repertoire_id=%s AND id>%s "
                "ORDER BY id LIMIT 1",
        values,
    ).fetchone()


def next_card_source(database, values):
    return database.execute_native(
        "SELECT card.id,card.repertoire_id,card.kind,card.start_fen,"
                "card.moves_json,COALESCE(card.trained_color,%s) trained_color "
                "FROM cards card WHERE card.id>%s AND card.archived=0 "
                "AND card.content_type='opening' AND "
                "(card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards link "
                "WHERE link.repertoire_id=%s AND link.card_id=card.id)) "
                "ORDER BY card.id LIMIT 1",
        values,
    ).fetchone()


def invalidate_scan_state(database, values):
    return database.execute_native(
        "INSERT INTO repertoire_integrity_state("
        "repertoire_id,status,checked_at,scan_status,scan_generation,"
        "scan_completed_sources,scan_total_sources,scan_error) "
        "VALUES(%s,'unchecked',NULL,'idle',NULL,0,0,NULL) "
        "ON CONFLICT(repertoire_id) DO UPDATE SET "
        "status='unchecked',checked_at=NULL,scan_status='idle',"
        "scan_generation=NULL,scan_completed_sources=0,scan_total_sources=0,scan_error=NULL",
        values,
    )


def set_scan_source_total(database, values):
    return database.execute_native(
        "UPDATE repertoire_integrity_state SET scan_total_sources=%s "
            "WHERE repertoire_id=%s AND scan_generation=%s",
        values,
    )


def stage_source(database, values):
    return database.execute_native(
        "INSERT INTO repertoire_integrity_source_runs("
        "run_id,source_offset,observations_json,invalid_json) "
        "VALUES(%s,%s,%s,%s) ON CONFLICT(run_id,source_offset) DO UPDATE SET "
        "observations_json=excluded.observations_json,"
        "invalid_json=excluded.invalid_json",
        values,
    )


def advance_source_state(database, values):
    return database.execute_native(
        "UPDATE repertoire_integrity_state SET status='unchecked',"
        "scan_status='running',scan_generation=%s,"
        "scan_completed_sources=%s,scan_error=NULL "
        "WHERE repertoire_id=%s AND scan_generation=%s",
        values,
    )


def staged_source(database, values):
    return database.execute_native(
        "SELECT observations_json,invalid_json "
            "FROM repertoire_integrity_source_runs "
            "WHERE run_id=%s AND source_offset=%s",
        values,
    ).fetchone()


def stage_invalid_candidates(cursor, values):
    return cursor.executemany(
        "INSERT INTO repertoire_integrity_issue_candidates("
                "run_id,id,repertoire_id,kind,fen_key,fen,trained_color,"
                "signature,moves_json,sources_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(run_id,id) DO NOTHING",
        values,
    )


def merge_position_observations(cursor, values):
    return cursor.executemany(
        "INSERT INTO repertoire_integrity_position_accumulators("
            "run_id,fen_key,fen,trained_color,moves_json,sources_json) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id,fen_key) DO UPDATE SET "
            "moves_json=(repertoire_integrity_position_accumulators.moves_json::jsonb "
            "|| excluded.moves_json::jsonb)::text,"
            "sources_json=(repertoire_integrity_position_accumulators.sources_json::jsonb "
            "|| excluded.sources_json::jsonb)::text",
        values,
    )


def next_accumulated_position(database, values):
    return database.execute_native(
        "SELECT fen_key,fen,trained_color,moves_json,sources_json "
            "FROM repertoire_integrity_position_accumulators "
            "WHERE run_id=%s AND fen_key>%s ORDER BY fen_key LIMIT 1",
        values,
    ).fetchone()


def stage_issue(database, values):
    return database.execute_native(
        "INSERT INTO repertoire_integrity_issue_candidates("
            "run_id,id,repertoire_id,kind,fen_key,fen,trained_color,"
            "signature,moves_json,sources_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(run_id,id) DO NOTHING",
        values,
    )


def graph_task(database, values):
    return database.execute_native(
        "SELECT generation,state FROM background_tasks "
        "WHERE kind='opening_graph_rebuild' AND deduplication_key=%s",
        values,
    ).fetchone()


def graph_publication(database, values):
    return database.execute_native(
        "SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s",
        values,
    ).fetchone()


def issue_candidate_count(database, values):
    return database.execute_native(
        "SELECT COUNT(*) FROM repertoire_integrity_issue_candidates WHERE run_id=%s",
        values,
    ).fetchone()[0]


def card_source_block_count(database, values):
    return database.execute_native(
        "SELECT COUNT(*) FROM repertoire_integrity_issue_candidates candidate "
        "CROSS JOIN LATERAL jsonb_array_elements(candidate.sources_json::jsonb) source "
        "WHERE candidate.run_id=%s AND source->>'type'='card'",
        values,
    ).fetchone()[0]


def clear_issues(database, values):
    return database.execute_native(
        "DELETE FROM repertoire_integrity_issues WHERE repertoire_id=%s",
        values,
    )


def publish_issues(database, values):
    return database.execute_native(
        "INSERT INTO repertoire_integrity_issues("
        "id,repertoire_id,kind,fen_key,fen,trained_color,signature,"
        "moves_json,sources_json,created_at,updated_at) "
        "SELECT id,repertoire_id,kind,fen_key,fen,trained_color,signature,"
        "moves_json,sources_json,%s,%s "
        "FROM repertoire_integrity_issue_candidates WHERE run_id=%s",
        values,
    )


def publish_card_blocks(database, values):
    return database.execute_native(
        "INSERT INTO repertoire_integrity_card_blocks("
        "repertoire_id,card_id,issue_id,scan_generation,published_at) "
        "SELECT DISTINCT %s,source->>'id',candidate.id,%s,%s "
        "FROM repertoire_integrity_issue_candidates candidate "
        "CROSS JOIN LATERAL jsonb_array_elements(candidate.sources_json::jsonb) source "
        "JOIN cards card ON card.id=source->>'id' "
        "WHERE candidate.run_id=%s AND source->>'type'='card' "
        "ON CONFLICT(repertoire_id,card_id,issue_id) DO NOTHING",
        values,
    )


def next_cards_to_validate(database, values):
    return database.execute_native(
        "SELECT card.id FROM cards card WHERE card.id>%s "
            "AND card.archived=0 AND card.content_type='opening' "
            "AND (card.repertoire_id=%s OR EXISTS("
            "SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=%s "
            "AND link.card_id=card.id)) ORDER BY card.id LIMIT 2",
        values,
    ).fetchall()


def validate_cards(cursor, values):
    return cursor.executemany(
        "UPDATE cards SET pending_validation=CASE WHEN EXISTS("
            "SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.card_id=cards.id) THEN 1 ELSE 0 END WHERE id=%s",
        values,
    )


def has_published_issue(database, values):
    return database.execute_native(
        "SELECT 1 FROM repertoire_integrity_issues WHERE repertoire_id=%s LIMIT 1",
        values,
    ).fetchone()


def complete_scan_state(database, values):
    return database.execute_native(
        "UPDATE repertoire_integrity_state SET status=%s,checked_at=%s,"
        "scan_status='idle',scan_generation=%s,"
        "scan_completed_sources=scan_total_sources,scan_error=NULL "
        "WHERE repertoire_id=%s AND scan_generation=%s",
        values,
    ).rowcount


def active_prefix_transition(database, values):
    return database.execute_native(
        "SELECT 1 FROM prefix_transition_applications WHERE repertoire_id=%s AND graph_generation=%s "
        "AND state IN ('publishing','recovery_required')",
        values,
    ).fetchone()
