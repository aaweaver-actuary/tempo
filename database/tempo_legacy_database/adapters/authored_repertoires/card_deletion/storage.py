"""Existing deletion queries, native/compatibility scopes and fixed purge targets."""
from __future__ import annotations


def is_card_deleted(database, identifier: str) -> bool:
    if hasattr(database, 'execute_native'):
        return database.execute_native('SELECT 1 FROM deleted_cards WHERE card_id=%s', (identifier,)).fetchone() is not None
    return database.execute('SELECT 1 FROM deleted_cards WHERE card_id=?', (identifier,)).fetchone() is not None


def _delete_matching(database, table: str, column: str, identifiers: list[str | int]) -> None:
    if not identifiers:
        return
    if hasattr(database, 'execute_native'):
        database.execute_native(f'DELETE FROM {table} WHERE {column}=ANY(%s)', (identifiers,))
    else:
        database.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join(('?' for _ in identifiers))})", identifiers)


def native_cleanup_supported(database):
    return hasattr(database, 'execute_native')


def cancellation_candidates(database, repertoire_id, now_factory):
    statement = "SELECT id,deduplication_key,payload_json FROM background_tasks WHERE state IN ('queued','leased','retrying') AND (deduplication_key=? OR instr(payload_json,?)>0) ORDER BY id"
    if hasattr(database, 'execute_native'):
        candidates = database.execute_native(statement.replace('?', '%s').replace('instr(payload_json,%s)', 'position(%s in payload_json)'), (repertoire_id, repertoire_id))
    else:
        candidates = database.execute(statement, (repertoire_id, repertoire_id))
    now = now_factory()
    return (now, candidates.fetchall())


def locked_cancellation_candidate(database, task):
    if hasattr(database, 'execute_native'):
        current = database.execute_native("SELECT deduplication_key,payload_json FROM background_tasks WHERE id=%s AND state IN ('queued','leased','retrying') FOR UPDATE", (task['id'],)).fetchone()
        return (True, current)
    return (False, None)


def cancel_task(database, now, task_id):
    database.execute("UPDATE background_tasks SET state='complete',phase='cancelled',lease_token=NULL,lease_expires_at=NULL,completed_at=?,updated_at=? WHERE id=?", (now, now, task_id))


def purge_sources(database, identifiers):
    placeholders = ','.join(('?' for _ in identifiers))
    queue_ids = [row[0] for row in database.execute(f'SELECT id FROM daily_queue WHERE card_id IN ({placeholders})', identifiers)]
    puzzle_ids = [row[0] for row in database.execute(f"SELECT source_ref FROM cards WHERE id IN ({placeholders}) AND content_type='tactic' AND source_ref IS NOT NULL", identifiers)]
    return (queue_ids, puzzle_ids)


def purge_tactic_attempts(database, puzzle_ids):
    _delete_matching(database, 'tactic_discovery_attempts', 'puzzle_id', puzzle_ids)


def clear_source_references(database, identifiers, now_factory):
    placeholders = ','.join(('?' for _ in identifiers))
    database.execute(f'UPDATE game_findings SET card_id=NULL WHERE card_id IN ({placeholders})', identifiers)
    database.execute(f'UPDATE threat_training_candidates SET paused_at=? WHERE card_id IN ({placeholders})', (now_factory(), *identifiers))


def purge_training_records(database, identifiers, queue_ids):
    for table, column, selected in (('study_attempts', 'card_id', identifiers), ('study_attempts', 'queue_entry_id', queue_ids), ('card_revisions', 'card_id', identifiers), ('queue_projection_diagnostics', 'card_id', identifiers), ('endgame_templates', 'card_id', identifiers), ('tactic_progress', 'card_id', identifiers)):
        _delete_matching(database, table, column, selected)


def record_tombstones(database, identifiers, now):
    database.executemany('INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?) ON CONFLICT(card_id) DO NOTHING', [(identifier, now) for identifier in identifiers])


def purge_queue_and_cards(database, identifiers):
    _delete_matching(database, 'daily_queue', 'card_id', identifiers)
    _delete_matching(database, 'cards', 'id', identifiers)


def affected_evidence_decisions(database, identifiers):
    affected_decisions = [row[0] for row in database.execute_native('SELECT DISTINCT observation.decision_id FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt ON attempt.attempt_id=observation.attempt_id WHERE attempt.card_id=ANY(%s)', (identifiers,))]
    return affected_decisions


def purge_evidence_records(database, identifiers, queue_ids, affected_decisions):
    _delete_matching(database, 'opening_evidence_attempts', 'card_id', identifiers)
    database.execute_native('DELETE FROM opening_evidence_queue_contexts WHERE queue_entry_id=ANY(%s) OR presentation_snapshot_id IN (SELECT id FROM opening_evidence_presentations WHERE card_id=ANY(%s))', (queue_ids, identifiers))
    _delete_matching(database, 'opening_evidence_presentations', 'card_id', identifiers)
    _delete_matching(database, 'opening_evidence_clean_days', 'decision_id', affected_decisions)
    _delete_matching(database, 'opening_evidence_summaries', 'decision_id', affected_decisions)


def remaining_evidence_observations(database, affected_decisions):
    return database.execute_native('SELECT decision_id,observation_json FROM opening_evidence_observations WHERE decision_id=ANY(%s)', (affected_decisions,))


def record_evidence_summary(database, decision_id, summary):
    database.execute_native('INSERT INTO opening_evidence_summaries(decision_id,first_responses,clean_successes,incorrect_responses,assisted_responses,manual_failures,corrections,distinct_clean_days) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)', (decision_id, *summary['counts'], len(summary['days'])))


def record_evidence_clean_days(database, decision_id, days):
    database.executemany('INSERT INTO opening_evidence_clean_days(decision_id,study_day) VALUES(?,?)', [(decision_id, day) for day in days])


def lock_main_repertoire(database):
    if hasattr(database, 'execute_native'):
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:main-repertoire',))


def locked_repertoire(database, repertoire_id):
    return database.execute('SELECT id,is_main FROM repertoires WHERE id=?' + (' FOR UPDATE' if hasattr(database, 'execute_native') else ''), (repertoire_id,)).fetchone()


def lock_affected_cards(database, repertoire_id):
    if hasattr(database, 'execute_native'):
        affected_card_ids = database.execute("SELECT id FROM cards WHERE repertoire_id=? UNION SELECT card_id FROM daily_queue WHERE admission_repertoire_id=? AND status='queued' ORDER BY 1", (repertoire_id, repertoire_id)).fetchall()
        for affected_card in affected_card_ids:
            database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'tempo:card-edit:{affected_card[0]}',))


def owned_cards(database, repertoire_id):
    return database.execute('SELECT card.id,card.start_fen,card.moves_json,COALESCE(card.trained_color,(SELECT line.trained_color FROM repertoire_lines line WHERE line.repertoire_id=card.repertoire_id ORDER BY line.created_at,line.id LIMIT 1)) AS effective_trained_color,card.introduced_at,card.first_correct_at,EXISTS(SELECT 1 FROM reviews WHERE card_id=card.id) AS has_reviews,(SELECT MIN(link.repertoire_id) FROM repertoire_cards link WHERE link.card_id=card.id AND link.repertoire_id<>?) AS replacement FROM cards card WHERE card.repertoire_id=? ORDER BY card.id' + (' FOR UPDATE OF card' if hasattr(database, 'execute_native') else ''), (repertoire_id, repertoire_id)).fetchall()


def create_retained_repertoire(database, replacement, now_factory):
    database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,'Retained cards','Cards kept after repertoire deletion',?,0) ON CONFLICT(id) DO NOTHING", (replacement, now_factory()))


def retain_membership(database, replacement, identifier):
    database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1) ON CONFLICT(repertoire_id,card_id) DO NOTHING', (replacement, identifier))


def set_retained_color(database, retained_color, identifier):
    database.execute('UPDATE cards SET trained_color=? WHERE id=? AND trained_color IS NULL', (retained_color, identifier))


def reassign_card(database, replacement, retained_repertoire_id, identifier):
    database.execute('UPDATE cards SET repertoire_id=?,pending_validation=CASE WHEN ?=? THEN 0 ELSE pending_validation END WHERE id=?', (replacement, replacement, retained_repertoire_id, identifier))


def reassign_queued_admission(database, repertoire_id):
    database.execute("UPDATE daily_queue SET admission_repertoire_id=(\n        SELECT card.repertoire_id FROM cards card WHERE card.id=daily_queue.card_id\n    ) WHERE admission_repertoire_id=? AND status='queued'\n      AND EXISTS(SELECT 1 FROM cards card JOIN repertoires owner ON owner.id=card.repertoire_id\n                 WHERE card.id=daily_queue.card_id AND owner.id<>?)", (repertoire_id, repertoire_id))


def clear_repertoire_findings(database, repertoire_id):
    database.execute('UPDATE game_findings SET repertoire_id=NULL WHERE repertoire_id=?', (repertoire_id,))


def delete_comparisons(database, repertoire_id):
    for table in ('repertoire_comparisons_legacy', 'repertoire_comparisons_staged') if hasattr(database, 'execute_native') else ('repertoire_comparisons',):
        database.execute(f'DELETE FROM {table} WHERE repertoire_id=?', (repertoire_id,))


def delete_repertoire(database, repertoire_id):
    database.execute('DELETE FROM repertoires WHERE id=?', (repertoire_id,))


def replacement_main(database, system_repertoire_ids):
    system_placeholders = ','.join(('?' for _ in system_repertoire_ids))
    return database.execute(f'SELECT id FROM repertoires WHERE id NOT IN ({system_placeholders}) ORDER BY created_at DESC,id LIMIT 1', system_repertoire_ids).fetchone()


def select_main(database, identifier):
    database.execute('UPDATE repertoires SET is_main=1 WHERE id=?', (identifier,))


def preview_card(database, identifier):
    return database.execute('SELECT id,revision FROM cards WHERE id=?', (identifier,)).fetchone()


def preview_repertoires(database, identifier):
    return database.execute('SELECT DISTINCT repertoire.id,repertoire.name FROM repertoires repertoire WHERE repertoire.id IN (SELECT repertoire_id FROM cards WHERE id=? UNION SELECT repertoire_id FROM repertoire_cards WHERE card_id=?) ORDER BY repertoire.name,repertoire.id', (identifier, identifier)).fetchall()


def lock_card(database, identifier):
    if hasattr(database, 'execute_native'):
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'tempo:card-edit:{identifier}',))


def locked_revision(database, identifier):
    return database.execute('SELECT revision FROM cards WHERE id=?' + (' FOR UPDATE' if hasattr(database, 'execute_native') else ''), (identifier,)).fetchone()


def delete_card_graph_steps(database, identifier):
    _delete_matching(database, 'opening_graph_steps', 'card_id', [identifier])


def graph_routes(database, identifier):
    return database.execute('SELECT DISTINCT repertoire_id,generation,line_id FROM opening_graph_steps WHERE card_id=? ORDER BY repertoire_id,generation,line_id', (identifier,)).fetchall()


def graph_dependencies(database, route):
    return database.execute('SELECT decision_index,card_id,parent_card_id FROM opening_graph_steps WHERE repertoire_id=? AND generation=? AND line_id=? ORDER BY decision_index', tuple(route))


def reparent_dependencies(database, updates):
    database.executemany('UPDATE opening_graph_steps SET parent_card_id=? WHERE repertoire_id=? AND generation=? AND line_id=? AND decision_index=?', updates)
