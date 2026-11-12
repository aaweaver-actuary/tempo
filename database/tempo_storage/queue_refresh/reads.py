"""Existing bounded queue projections and timeout reader; no queue-planning authority."""
from psycopg.errors import TransactionTimeout
from tempo_storage.connections import background_read_connection, read_connection
from tempo_storage import postgres
from tempo_storage.tactic_admission import DAILY_TACTIC_COUNT_SQL
from tempo_storage.queue_refresh.eligibility import _DUE_CARD_ELIGIBILITY, _STUDY_NEW_ELIGIBILITY, _POSITION_DUE_EXCLUSION
from tempo_storage.queue_refresh.openings import _STUDY_OPENING_ORDER, _STUDY_OPENING_ROUTES
from tempo_storage.queue_refresh.presentation import _QUARANTINE_OPENING_ELIGIBILITY, _PROJECTION_BLOCKED_COUNT_SQL

def _bounded_read(statement: str, parameters: tuple=()) -> list:
    """Retry a read-only section if PostgreSQL closes its 50 ms transaction."""
    for attempt in range(3):
        try:
            with background_read_connection() as database:
                return database.execute_native(statement, parameters).fetchall()
        except TransactionTimeout:
            if attempt == 2:
                raise
    raise AssertionError('Unreachable background read retry state')

def has_opening_allowance(queue_date):
    return _bounded_read("SELECT EXISTS(SELECT 1 FROM repertoires r CROSS JOIN settings s\n           WHERE s.id=1 AND EXISTS(SELECT 1 FROM opening_graph_publications publication\n                                  WHERE publication.repertoire_id=r.id AND publication.state='ready')\n             AND COALESCE(r.new_cards_per_day,s.new_cards_per_day) > (\n             SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id\n             WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening'\n               AND c.introduced_at=%s AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id) AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=r.id\n           ))", (queue_date, queue_date))

def tactical_allowance():
    return _bounded_read('SELECT tactics_new_per_day FROM settings WHERE id=1')

def tactical_reserved(queue_date):
    return _bounded_read(DAILY_TACTIC_COUNT_SQL, (queue_date,))

def active_tactical_packs():
    return _bounded_read('SELECT pack_id FROM tactic_pack_activation WHERE active=1')

def tactical_rotation():
    return _bounded_read('SELECT last_pack_id FROM tactic_rotation WHERE id=1')

def admitted_tactical_puzzles():
    return _bounded_read('SELECT puzzle_id FROM tactic_progress WHERE admitted_at IS NOT NULL UNION SELECT puzzle_id FROM tactic_introductions')

def deleted_candidates(candidate_ids):
    return _bounded_read('SELECT card_id FROM deleted_cards WHERE card_id=ANY(%s::text[])', (candidate_ids,))

def unseen_candidate(queue_date, processed_ids):
    return _bounded_read("SELECT q.id,q.card_id,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND q.status='queued' AND c.content_type='opening'\n             AND c.repertoire_id<>'__retained_cards__'\n             AND COALESCE(q.admission_kind,'')!='explicit'\n             AND (c.introduced_at IS NULL OR c.introduced_at=%s AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id))\n             AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id) AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id)\n             AND q.id <> ALL(%s::bigint[])\n           ORDER BY q.position,q.id LIMIT 1", (queue_date, queue_date, processed_ids))

def reviewed_introductions(repertoire_id, queue_date):
    return _bounded_read("SELECT COUNT(DISTINCT c.id) FROM cards c\n           LEFT JOIN daily_queue q ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0\n           WHERE COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s AND c.content_type='opening' AND c.introduced_at=%s AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id)\n             AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)", (repertoire_id, queue_date))

def due_candidate(queue_date):
    predicate = _DUE_CARD_ELIGIBILITY + _POSITION_DUE_EXCLUSION
    return _bounded_read('SELECT c.id FROM cards c WHERE ' + predicate + ' ORDER BY c.due_date,c.id LIMIT 1', (queue_date, queue_date, queue_date))

def opening_candidate_ids(after_card_id, row_limit):
    return _bounded_read("SELECT id FROM cards WHERE content_type='opening' AND state IN ('new','locked') AND introduced_at IS NULL AND archived=0 AND COALESCE(pending_validation,0)=0 AND id>%s ORDER BY id LIMIT %s", (after_card_id, row_limit))

def introduced_opening_counts(queue_date):
    return _bounded_read("SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s AND NOT EXISTS(SELECT 1 FROM study_recombination_presentations history WHERE history.presentation_id=c.id)\n           GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)", (queue_date, queue_date))

def opening_daily_limits():
    return _bounded_read('SELECT r.id,COALESCE(r.new_cards_per_day,s.new_cards_per_day) FROM repertoires r CROSS JOIN settings s WHERE s.id=1')

def study_allowance():
    return _bounded_read('SELECT study_new_per_day FROM settings WHERE id=1')

def study_admitted(queue_date):
    return _bounded_read("SELECT COUNT(*) FROM daily_queue queue JOIN cards card ON card.id=queue.card_id\n           WHERE queue.queue_date=%s AND card.content_type='study_exercise'\n             AND card.state='new' AND queue.status IN ('queued','complete','buried')", (queue_date,))

def study_candidate(queue_date):
    return _bounded_read('SELECT c.id FROM cards c JOIN study_exercises exercise ON exercise.id=c.study_exercise_id JOIN studies study ON study.id=exercise.study_id WHERE ' + _STUDY_NEW_ELIGIBILITY + ' ORDER BY exercise.created_at,exercise.id LIMIT 1', (queue_date, queue_date, queue_date))

def quarantine_page(queue_date, after_entry_id, row_limit):
    return _bounded_read('SELECT q.id queue_entry_id,c.id card_id,c.start_fen,c.moves_json,\n                  COALESCE(c.trained_color,(SELECT line.trained_color FROM repertoire_lines line\n                           WHERE line.repertoire_id=c.repertoire_id\n                           ORDER BY line.created_at LIMIT 1)) trained_color\n           FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE ' + _QUARANTINE_OPENING_ELIGIBILITY + ' AND q.id>%s ORDER BY q.id LIMIT %s', (queue_date, after_entry_id, row_limit))

def projection_blocked_count(queue_date):
    return _bounded_read(_PROJECTION_BLOCKED_COUNT_SQL, (queue_date, queue_date))

def study_opening_queries(frozen_active: bool):
    route_sql = _STUDY_OPENING_ROUTES
    occurrence_order = _STUDY_OPENING_ORDER
    order_columns = ''
    if frozen_active:
        route_sql = route_sql.replace('WHERE publication.state', 'LEFT JOIN frozen_opening_activation foa ON foa.id=1 LEFT JOIN frozen_opening_ranks forank ON forank.run_id=foa.run_id AND forank.card_id=c.id WHERE publication.state')
        rank_key = 'COALESCE(forank.ordinal,9223372036854775807)'
        order_columns = ',' + rank_key
        occurrence_order = '(' + rank_key + ',' + _STUDY_OPENING_ORDER[1:]

    def read_occurrences(statement, parameters=()):
        with read_connection(repertoire_snapshot=True) as database:
            return database.execute_native(statement, parameters).fetchall()

    def last_study_occurrences():
        return read_occurrences('SELECT repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index,c.id' + order_columns + ' ' + route_sql + ' ORDER BY ' + occurrence_order + ' DESC LIMIT 1')

    def study_occurrence_page(after_occurrence_cursor, upper_occurrence_cursor, row_limit):
        cursor_placeholders = '(' + ','.join(['%s'] * (7 if frozen_active else 6)) + ')'
        cursor_filter = ' AND ' + occurrence_order + '>' + cursor_placeholders if after_occurrence_cursor else ''
        return read_occurrences('SELECT repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index,c.id,step.generation,c.start_fen,c.moves_json,COALESCE(c.trained_color,step.trained_color),step.starting_fen,step.moves_json' + order_columns + ' ' + route_sql + cursor_filter + ' AND ' + occurrence_order + '<=' + cursor_placeholders + ' ORDER BY ' + occurrence_order + ' LIMIT %s', (*(tuple(after_occurrence_cursor) if after_occurrence_cursor else ()), *upper_occurrence_cursor, row_limit))
    return (last_study_occurrences, study_occurrence_page)
