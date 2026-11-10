"""Existing published-opening route and admission projections/writes."""
_STUDY_OPENING_ORDER = '(repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index,c.id)'
_STUDY_OPENING_ROUTES = "FROM cards c\nJOIN opening_graph_steps step ON step.card_id=c.id\nJOIN opening_graph_publications publication ON step.repertoire_id=publication.repertoire_id\n    AND step.generation=publication.generation\nJOIN repertoires repertoire ON repertoire.id=step.repertoire_id\nJOIN repertoire_lines line ON line.id=step.line_id AND line.repertoire_id=step.repertoire_id\nWHERE publication.state='ready' AND c.content_type='opening'\n  AND c.state='new' AND c.introduced_at IS NULL\n  AND c.archived=0 AND COALESCE(c.pending_validation,0)=0\n  AND c.superseded_by IS NULL\n  AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)"

def route_occurrence(database, repertoire_id, generation, line_id, decision_index, card_id, queue_date):
    return database.execute_native('SELECT step.parent_card_id,c.state,c.due_date,c.start_fen,c.moves_json,COALESCE(c.trained_color,step.trained_color),step.starting_fen,step.moves_json ' + _STUDY_OPENING_ROUTES + ' AND step.repertoire_id=%s AND step.generation=%s AND step.line_id=%s AND step.decision_index=%s AND c.id=%s AND (c.repertoire_id=step.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=c.id AND link.repertoire_id=step.repertoire_id)) AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.repertoire_id=step.repertoire_id AND block.card_id=c.id) AND NOT EXISTS(SELECT 1 FROM daily_queue queued WHERE queued.card_id=c.id AND queued.queue_date=%s)', (repertoire_id, generation, line_id, decision_index, card_id, queue_date)).fetchone()

def parent_study_review(database, parent_card_id):
    return database.execute_native("SELECT 1 FROM cards parent JOIN reviews review ON review.card_id=parent.id WHERE parent.id=%s AND review.source_kind='study' AND review.invalidated_at IS NULL LIMIT 1", (parent_card_id,)).fetchone()

def remaining_allowance(database, queue_date, repertoire_id):
    return database.execute_native("SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) - (SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id) AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=r.id) FROM repertoires r CROSS JOIN settings s WHERE r.id=%s AND s.id=1", (queue_date, queue_date, repertoire_id)).fetchone()

def opening_limit(database, repertoire_id):
    return database.execute_native('SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) FROM repertoires r CROSS JOIN settings s WHERE r.id=%s AND s.id=1', (repertoire_id,)).fetchone()

def locked_opening_card(database, card_id):
    return database.execute_native('SELECT state,introduced_at,archived,pending_validation FROM cards WHERE id=%s FOR UPDATE', (card_id,)).fetchone()

def admitted_count(database, queue_date, repertoire_id):
    return database.execute_native("SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id)\n             AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s", (queue_date, queue_date, repertoire_id)).fetchone()[0]

def safe_route(database, repertoire_id, card_id):
    return database.execute_native('SELECT 1 FROM repertoires repertoire WHERE repertoire.id=%s AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.repertoire_id=repertoire.id AND block.card_id=%s)', (repertoire_id, card_id)).fetchone()

def append_opening(database, queue_date, card_id, position, reason, repertoire_id):
    return database.execute_native('INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,\n                                   admission_repertoire_id) VALUES(%s,%s,%s,%s,%s)', (queue_date, card_id, position, reason, repertoire_id))
