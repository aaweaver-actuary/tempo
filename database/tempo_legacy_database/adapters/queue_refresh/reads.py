"""Existing bounded queue projections and timeout reader; no queue-planning authority."""
from psycopg.errors import TransactionTimeout
from tempo_legacy_database.connections import background_read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_sql
from tempo_legacy_database.adapters.tactic_admission import DAILY_TACTIC_COUNT_SQL
from .eligibility import _DUE_CARD_ELIGIBILITY, _STUDY_NEW_ELIGIBILITY
from .openings import _STUDY_OPENING_ORDER, _STUDY_OPENING_ROUTES, _PRIORITY_OPENING_PAGE_SQL
from .presentation import _QUARANTINE_OPENING_ELIGIBILITY, _PROJECTION_BLOCKED_COUNT_SQL
from .capacity import capacity_priority_statement

_ACTIVE_OPENING_MISS_SQL = outstanding_miss_sql(postgres=True)


def native_configured() -> bool:
    return postgres_store.configured()


def _bounded_read(statement: str, parameters: tuple = (), *, native: bool = False) -> list:
    """Retry a read-only section if PostgreSQL closes its 50 ms transaction."""

    for attempt in range(3):
        try:
            with background_read_connection() as database:
                execute = database.execute_native if native else database.execute
                return execute(statement, parameters).fetchall()
        except TransactionTimeout:
            if attempt == 2:
                raise
    raise AssertionError("Unreachable background read retry state")


def has_opening_allowance(queue_date):
    return _bounded_read(
        """SELECT EXISTS(SELECT 1 FROM repertoires r CROSS JOIN settings s
           WHERE s.id=1 AND EXISTS(SELECT 1 FROM opening_graph_publications publication
                                  WHERE publication.repertoire_id=r.id AND publication.state='ready')
             AND COALESCE(r.new_cards_per_day,s.new_cards_per_day) > (
             SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id
             WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening'
               AND c.introduced_at=%s AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=r.id
           )) OR EXISTS(""" + outstanding_miss_sql(postgres=True, columns='1') + ' LIMIT 1)',
        (queue_date, queue_date), native=True,
    )


def tactical_allowance():
    return _bounded_read("SELECT tactics_new_per_day FROM settings WHERE id=1")


def tactical_reserved(queue_date):
    return _bounded_read(
        DAILY_TACTIC_COUNT_SQL, (queue_date,),
    )


def active_tactical_packs():
    return _bounded_read(
        "SELECT pack_id FROM tactic_pack_activation WHERE active=1",
    )


def tactical_rotation():
    return _bounded_read(
        "SELECT last_pack_id FROM tactic_rotation WHERE id=1",
    )


def admitted_tactical_puzzles():
    return _bounded_read(
        "SELECT puzzle_id FROM tactic_progress WHERE admitted_at IS NOT NULL",
    )


def deleted_candidates(candidate_ids):
    return _bounded_read(
                "SELECT card_id FROM deleted_cards WHERE card_id=ANY(%s::text[])",
                (candidate_ids,), native=True,
            )


def unseen_candidate(queue_date, processed_ids):
    return _bounded_read(
        """SELECT q.id,q.card_id,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.status='queued' AND c.content_type='opening'
             AND c.repertoire_id<>'__retained_cards__'
             AND COALESCE(q.admission_kind,'')!='explicit'
             AND (c.introduced_at IS NULL OR c.introduced_at=%s)
             AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
             AND q.id <> ALL(%s::bigint[])
           ORDER BY q.position,q.id LIMIT 1""",
        (queue_date, queue_date, processed_ids), native=True,
    )


def reviewed_introductions(repertoire_id, queue_date):
    return _bounded_read(
        """SELECT COUNT(DISTINCT c.id) FROM cards c
           LEFT JOIN daily_queue q ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0
           WHERE COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s AND c.content_type='opening' AND c.introduced_at=%s
             AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)""",
        (repertoire_id, queue_date), native=True,
    )


def due_candidate(queue_date):
    return _bounded_read(
        "SELECT c.id FROM cards c WHERE " + _DUE_CARD_ELIGIBILITY +
        " ORDER BY c.due_date,c.id LIMIT 1",
        (queue_date, queue_date, queue_date), native=True,
    )


def active_opening_misses():
    return _bounded_read(_ACTIVE_OPENING_MISS_SQL)


def opening_candidate_ids(after_card_id, row_limit):
    return _bounded_read(
            "SELECT id FROM cards WHERE content_type='opening' "
            "AND state IN ('new','locked') AND introduced_at IS NULL "
            "AND archived=0 AND COALESCE(pending_validation,0)=0 "
            "AND id>%s ORDER BY id LIMIT %s",
            (after_card_id, row_limit), native=True,
        )


def priority_opening_rows(active_misses, card_ids, miss_reason, queue_date):
    return _bounded_read(
            _PRIORITY_OPENING_PAGE_SQL,
            (active_misses, card_ids, miss_reason, queue_date,
             queue_date, queue_date, queue_date), native=True,
        )


def introduced_opening_counts(queue_date):
    return _bounded_read(
        """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s
           GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)""",
        (queue_date, queue_date), native=True,
    )


def opening_daily_limits():
    return _bounded_read(
        "SELECT r.id,COALESCE(r.new_cards_per_day,s.new_cards_per_day) "
        "FROM repertoires r CROSS JOIN settings s WHERE s.id=1",
    )


def capacity_priority_rows(active_misses, card_ids, miss_reason, queue_date):
    return _bounded_read(
            capacity_priority_statement(_PRIORITY_OPENING_PAGE_SQL),
            (active_misses, card_ids, miss_reason,
             queue_date, queue_date, queue_date, queue_date), native=True,
        )


def study_allowance():
    return _bounded_read("SELECT study_new_per_day FROM settings WHERE id=1")


def study_admitted(queue_date):
    return _bounded_read(
        """SELECT COUNT(*) FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
           WHERE queue.queue_date=%s AND card.content_type='study_exercise'
             AND card.state='new' AND queue.status IN ('queued','complete','buried')""",
        (queue_date,), native=True,
    )


def study_candidate(queue_date):
    return _bounded_read(
        "SELECT c.id FROM cards c JOIN study_exercises exercise ON exercise.id=c.study_exercise_id "
        "JOIN studies study ON study.id=exercise.study_id WHERE " + _STUDY_NEW_ELIGIBILITY +
        " ORDER BY exercise.created_at,exercise.id LIMIT 1",
        (queue_date, queue_date, queue_date), native=True,
    )


def real_game_obligation(queue_date, miss_reason, after_card_id):
    obligation = outstanding_miss_sql(postgres=True,
        additional_where="AND card.state IN ('learning','mature')")
    return _bounded_read(
        f"WITH obligations AS ({obligation}) SELECT card_id FROM ("
        "SELECT card_id FROM obligations UNION SELECT card_id FROM daily_queue "
        "WHERE queue_date=%s AND status='queued' AND gameplay_priority_reason=%s) candidate "
        "WHERE card_id>%s ORDER BY card_id LIMIT 1",
        (queue_date, miss_reason, after_card_id), native=True,
    )


def quarantine_page(queue_date, after_entry_id, row_limit):
    return _bounded_read(
        """SELECT q.id queue_entry_id,c.id card_id,c.start_fen,c.moves_json,
                  COALESCE(c.trained_color,(SELECT line.trained_color FROM repertoire_lines line
                           WHERE line.repertoire_id=c.repertoire_id
                           ORDER BY line.created_at LIMIT 1)) trained_color
           FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE """
        + _QUARANTINE_OPENING_ELIGIBILITY +
        " AND q.id>%s ORDER BY q.id LIMIT %s",
        (queue_date, after_entry_id, row_limit), native=True,
    )


def projection_blocked_count(queue_date):
    return _bounded_read(
        _PROJECTION_BLOCKED_COUNT_SQL, (queue_date, queue_date), native=True,
    )


def study_opening_queries(frozen_active: bool):
    route_sql = _STUDY_OPENING_ROUTES
    occurrence_order = _STUDY_OPENING_ORDER
    order_columns = ''
    if frozen_active:
        route_sql = route_sql.replace("WHERE publication.state", "LEFT JOIN frozen_opening_activation foa ON foa.id=1 LEFT JOIN frozen_opening_ranks forank ON forank.run_id=foa.run_id AND forank.card_id=c.id WHERE publication.state")
        miss_key = "CASE WHEN c.id IN (" + outstanding_miss_sql(postgres=True, columns='event.card_id') + ") THEN 0 ELSE 1 END"
        rank_key = "COALESCE(forank.ordinal,9223372036854775807)"
        order_columns = ',' + miss_key + ',' + rank_key
        occurrence_order = '(' + miss_key + ',' + rank_key + ',' + _STUDY_OPENING_ORDER[1:]

    def last_study_occurrences():
        return _bounded_read("SELECT repertoire.created_at,repertoire.id,line.created_at,line.id,"
                             "step.decision_index,c.id" + order_columns + " " + route_sql +
                             " ORDER BY " + occurrence_order + " DESC LIMIT 1", native=True)

    def study_occurrence_page(after_occurrence_cursor, upper_occurrence_cursor, row_limit):
        cursor_placeholders = '(' + ','.join(['%s'] * (8 if frozen_active else 6)) + ')'
        cursor_filter = (" AND " + occurrence_order + ">" + cursor_placeholders if after_occurrence_cursor else "")
        return _bounded_read(
        "SELECT repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index,c.id,"
        "step.generation,c.start_fen,c.moves_json,COALESCE(c.trained_color,step.trained_color),step.starting_fen,step.moves_json" + order_columns + " "
        + route_sql + cursor_filter + " AND " + occurrence_order + "<=" + cursor_placeholders +
        " ORDER BY " + occurrence_order + " LIMIT %s",
        (*(tuple(after_occurrence_cursor) if after_occurrence_cursor else ()), *upper_occurrence_cursor,
         row_limit), native=True,
    )

    return last_study_occurrences, study_occurrence_page
