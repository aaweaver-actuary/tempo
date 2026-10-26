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


def product_source_facts(database, repertoire_id, card_ids, line_ids, *, fence=False):
    """Bounded exact current source rows; no eligibility decision or new writer."""
    if len(card_ids) > 128 or len(line_ids) > 64:
        raise ValueError('product introduction source exceeds current-owner bound')
    suffix = ' FOR SHARE SKIP LOCKED' if fence else ''
    if fence and not database.execute_native(
        'SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
        ('tempo:opening-graph:' + repertoire_id,)).fetchone()[0]:
        return None, (), (), ()
    header = database.execute_native("""SELECT repertoire.scope_source_revision,
        repertoire.canonical_prefix_revision,repertoire.canonical_prefix_moves_json,scope.generation catalogue_generation,
        publication.generation graph_generation,publication.state graph_state,
        identity.target_id,identity.workspace_id,repertoire.new_cards_per_day legacy_repertoire_limit
        FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
        CROSS JOIN legacy_learning_identity identity
        JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
        WHERE repertoire.id=%s AND scope.id=1 AND identity.id=1""" + suffix,
        (repertoire_id,)).fetchone()
    lines = database.execute_native('SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines '
        'WHERE repertoire_id=%s AND id=ANY(%s) ORDER BY id' + suffix, (repertoire_id, list(line_ids))).fetchall()
    cards = database.execute_native("""SELECT card.*, EXISTS(
        SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.repertoire_id=%s AND block.card_id=card.id) blocked,
        EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id) deleted,
        (card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards membership
            WHERE membership.repertoire_id=%s AND membership.card_id=card.id)) member
        ,EXISTS(SELECT 1 FROM repertoire_cards membership
            WHERE membership.repertoire_id=%s AND membership.card_id=card.id) linked_member
        FROM cards card WHERE card.id=ANY(%s) ORDER BY card.id""" + suffix,
        (repertoire_id, repertoire_id, repertoire_id, repertoire_id, list(card_ids))).fetchall()
    graphs = database.execute_native('SELECT * FROM opening_graph_steps WHERE repertoire_id=%s '
        'AND generation=(SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s) '
        'AND card_id=ANY(%s) ORDER BY line_id,decision_index LIMIT 257' + suffix,
        (repertoire_id, repertoire_id, list(card_ids))).fetchall()
    return header, lines, cards, graphs


def product_current_queue_facts(database, day, *, fence=False):
    suffix = ' FOR SHARE SKIP LOCKED' if fence else ''
    controls = database.execute_native('SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1' + suffix, ()).fetchone()
    introduced = database.execute_native("""SELECT card.id,card.introduced_at,
        COALESCE((SELECT admission_repertoire_id FROM daily_queue WHERE card_id=card.id
            AND cycle=0 AND queue_date=card.introduced_at ORDER BY id LIMIT 1),card.repertoire_id)
        FROM cards card WHERE card.content_type='opening' AND card.introduced_at=%s""", (day,)).fetchall()
    required = database.execute_native("""SELECT c.id,q.gameplay_priority_reason
        FROM cards c LEFT JOIN daily_queue q ON q.card_id=c.id AND q.queue_date=%s AND q.cycle=0
        WHERE c.content_type='opening' AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
        AND c.introduced_at IS NOT NULL AND (q.gameplay_priority_reason IS NOT NULL
            OR (c.state IN ('learning','mature') AND c.due_date<=%s))
        ORDER BY CASE WHEN q.gameplay_priority_reason IS NOT NULL THEN 0 ELSE 1 END,c.due_date,c.id""", (day, day)).fetchall()
    queued = database.execute_native("""SELECT DISTINCT card_id FROM daily_queue WHERE queue_date=%s
        OR (status='queued' AND attempt_state NOT IN ('clean','unstarted'))""", (day,)).fetchall()
    return controls, introduced, required, frozenset(row[0] for row in queued)


def product_target_route_page(database, repertoire_id, after_line_id, *, limit=16):
    """Bounded discovery page, never a claimed complete prefix of the source.

    The caller retains the existing repertoire/publication fence and completes
    the stream before trusting relevant alternatives. Oversized rows return an
    explicit sentinel instead of unbounded raw text. No expected-response filter.
    """
    if not 1 <= limit <= 16:
        raise ValueError('target source page exceeds bound')
    return database.execute_native("""WITH page AS MATERIALIZED (
        SELECT id,repertoire_id,trained_color,start_fen,moves_json,
            octet_length(concat_ws('',id,repertoire_id,trained_color,start_fen,moves_json))>65536 oversized
        FROM repertoire_lines WHERE repertoire_id=%s AND id>%s ORDER BY id LIMIT %s
        ) SELECT CASE WHEN octet_length(id)<=200 THEN id END id,repertoire_id,trained_color,
            CASE WHEN NOT oversized THEN start_fen END start_fen,
            CASE WHEN NOT oversized THEN moves_json END moves_json,oversized
        FROM page ORDER BY id""", (repertoire_id,after_line_id,limit)).fetchall()


def lock_product_admission(database, day, card_id):
    from tempo_legacy_database.adapters.real_game_feedback import lock_queue_day
    # A bounded batch may already own the day; never wait backwards for a card
    # held by a foreground review waiting for that day. Retry leaves it intact.
    if not database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                   ('tempo:card-edit:' + card_id,)).fetchone()[0]:
        return False
    if not database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED', (card_id,)).fetchone():
        return False
    lock_queue_day(database, day)
    return True
