"""Fixed legacy admission queries on the caller connection; no admission policy."""

from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query
from tempo_legacy_database.adapters.queue_refresh.capacity import capacity_priority_statement, read_capacity_route_rows


_PRIORITY_OPENING_CANDIDATE_BODY = """SELECT DISTINCT c.id,linked.id repertoire_id,c.moves_json,c.due_date,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ?
                    WHEN c.state='locked' AND opportunity.id IS NOT NULL THEN
                    'Priority introduction · reached ' || json_extract(opportunity.evidence_json,'$.encounter_count') ||
                    ' times in games, missed ' || json_extract(opportunity.evidence_json,'$.miss_count') || ' times'
                    ELSE p.reason END gameplay_priority_reason,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ? ELSE p.priority_date END priority_date,
                  COALESCE(published_priority.priority_score,legacy_priority.priority_score) priority_score,
                  COALESCE(published_priority.completed_line_ids_json,legacy_priority.completed_line_ids_json) completed_line_ids_json,
                  COALESCE(published_priority.frontier_decisions_json,legacy_priority.frontier_decisions_json) frontier_decisions_json
           FROM cards c
           JOIN repertoires linked ON (
               linked.id=c.repertoire_id OR EXISTS(
                   SELECT 1 FROM repertoire_cards candidate_link
                   WHERE candidate_link.card_id=c.id
                     AND candidate_link.repertoire_id=linked.id
               )
           )
           LEFT JOIN current_gameplay_card_priorities p ON p.card_id=c.id AND p.priority_date<=?
           LEFT JOIN active_miss ON active_miss.card_id=c.id
           LEFT JOIN current_repertoire_opportunities opportunity ON opportunity.repertoire_id=linked.id
             AND opportunity.card_id=c.id AND opportunity.kind='weak_known_decision'
             AND opportunity.status='active'
             AND json_extract(opportunity.evidence_json,'$.analysis_based') IS NULL
           LEFT JOIN repertoire_priority_publications publication
             ON publication.repertoire_id=linked.id
           LEFT JOIN current_repertoire_card_priority_generations published_priority
             ON published_priority.card_id=c.id
            AND published_priority.repertoire_id=linked.id
            AND published_priority.generation=publication.generation
           LEFT JOIN current_repertoire_card_introduction_priorities legacy_priority
             ON legacy_priority.card_id=c.id AND legacy_priority.repertoire_id=linked.id
           WHERE c.content_type='opening' AND (c.due_date<=? OR p.card_id IS NOT NULL OR active_miss.card_id IS NOT NULL OR opportunity.id IS NOT NULL)
             AND (c.state='new' OR (c.state='locked' AND (opportunity.id IS NOT NULL OR active_miss.card_id IS NOT NULL)))
             AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
             AND EXISTS(SELECT 1 FROM repertoires r_ok
                        WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))
                          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                         WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id))
             AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?)"""


def unseen_opening_page(database, day, after, batch_size):
    cursor = "" if after is None else " AND (q.position>? OR (q.position=? AND q.id>?))"
    return database.execute(
                """SELECT q.id,q.card_id,q.position,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id
                   FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.status='queued' AND c.content_type='opening'
                     AND c.repertoire_id<>'__retained_cards__'
                     AND COALESCE(q.admission_kind,'')!='explicit'
                     AND (c.introduced_at IS NULL OR c.introduced_at=?)
                     AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)"""
                + cursor + " ORDER BY q.position,q.id LIMIT ?",
                (day, day, *((after[0], after[0], after[1]) if after is not None else ()), batch_size),
            ).fetchall()


def introduction_count_rows(database, day, after_repertoire_id, reviewed_only, batch_size):
    statement = ("""SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
            FROM cards c LEFT JOIN daily_queue q
              ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0
            WHERE c.content_type='opening' AND c.introduced_at=?
              AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)""" if reviewed_only else
            """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
            FROM daily_queue q JOIN cards c ON c.id=q.card_id
            WHERE q.queue_date=? AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=?""")
    parameters = (day,) if reviewed_only else (day, day)
    if after_repertoire_id is not None:
        statement += " AND COALESCE(q.admission_repertoire_id,c.repertoire_id)>?"
        parameters += (after_repertoire_id,)
    return database.execute(
            statement + " GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id) "
            "ORDER BY COALESCE(q.admission_repertoire_id,c.repertoire_id) LIMIT ?",
            (*parameters, batch_size),
        ).fetchall()


def retain_unseen(database, day, card_id):
    database.execute("UPDATE cards SET introduced_at=?,state='learning' WHERE id=?", (day, card_id))


def remove_unseen(database, entry_id, card_id):
    database.execute("DELETE FROM daily_queue WHERE id=?", (entry_id,))
    database.execute("UPDATE cards SET introduced_at=NULL,state='new' WHERE id=?", (card_id,))


def priority_rows(database, day, card_ids, miss_reason, *, capacity=False):
    body = _PRIORITY_OPENING_CANDIDATE_BODY
    if capacity:
        body = capacity_priority_statement(body)
    return database.execute(
            f"WITH active_miss AS ({outstanding_miss_query(database)}) " + body
            + ' AND c.id IN (' + ','.join('?' for _ in card_ids) + ')',
            (miss_reason, day, day, day, day, *card_ids),
        ).fetchall()


def candidate_identifiers(database, after_card_id, batch_size):
    return database.execute(
            "SELECT id FROM cards WHERE content_type='opening' AND state IN ('new','locked') "
            "AND introduced_at IS NULL AND archived=0 AND COALESCE(pending_validation,0)=0 "
            "AND id>? ORDER BY id LIMIT ?", (after_card_id, batch_size),
        ).fetchall()


def repertoire_identifiers(database, after, batch_size):
    return database.execute('SELECT id FROM repertoires WHERE id>? ORDER BY id LIMIT ?',
                                         (after, batch_size)).fetchall()


def introduced_count(database, day, repertoire_id):
    return database.execute(
            """SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id
               WHERE q.queue_date=? AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=?
                 AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=?""", (day, day, repertoire_id),
        ).fetchone()[0]


def current_limit(database, repertoire_id):
    return database.execute(
            'SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
            'FROM repertoires r CROSS JOIN settings s WHERE r.id=? AND s.id=1', (repertoire_id,),
        ).fetchone()


def borrowed_capacity_route_rows(database, repertoire_id, parent_ids, batch_size):
    return read_capacity_route_rows(
        lambda statement, parameters: database.execute(statement, parameters).fetchall(),
        repertoire_id, parent_ids, batch_size,
    )


def append_opening(database, day, card_id, position, reason, repertoire_id):
    database.execute(
            """INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,admission_repertoire_id)
               VALUES(?,?,?,?,?)""",
            (day, card_id, position, reason, repertoire_id),
        )


def resolve_opportunity(database, resolved_at, updated_at, repertoire_id, card_id):
    database.execute(
            """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
               WHERE repertoire_id=? AND card_id=? AND kind='weak_known_decision'
                 AND json_extract(evidence_json,'$.analysis_based') IS NULL AND status='active'""",
            (resolved_at, updated_at,
             repertoire_id, card_id),
        )
