"""Existing published-opening route and admission projections/writes."""
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_sql


_STUDY_OPENING_ORDER = "(repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index,c.id)"


_STUDY_OPENING_ROUTES = """FROM (
    SELECT id FROM cards WHERE content_type='opening' AND state='new'
    UNION """ + outstanding_miss_sql(postgres=True, columns='event.card_id') + """
    UNION SELECT card_id FROM current_repertoire_opportunities
        WHERE kind='weak_known_decision' AND status='active'
          AND (evidence_json::jsonb ->> 'analysis_based') IS NULL
) eligible
JOIN cards c ON c.id=eligible.id
JOIN opening_graph_steps step ON step.card_id=c.id
JOIN opening_graph_publications publication ON step.repertoire_id=publication.repertoire_id
    AND step.generation=publication.generation
JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
JOIN repertoire_lines line ON line.id=step.line_id AND line.repertoire_id=step.repertoire_id
WHERE publication.state='ready' AND c.content_type='opening'
  AND c.state IN ('new','locked') AND c.introduced_at IS NULL
  AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
  AND c.superseded_by IS NULL
  AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)"""


_PRIORITY_OPENING_PAGE_SQL = """WITH active_miss AS MATERIALIZED (
    SELECT unnest(%s::text[]) AS card_id
), eligible_cards AS MATERIALIZED (
    SELECT * FROM cards WHERE id=ANY(%s::text[])
)
SELECT DISTINCT c.id,linked.id AS repertoire_id,c.moves_json,c.due_date,
       CASE WHEN active_miss.card_id IS NOT NULL THEN %s
         WHEN c.state='locked' AND opportunity.id IS NOT NULL THEN
         'Priority introduction · reached ' ||
         (opportunity.evidence_json::jsonb ->> 'encounter_count') ||
         ' times in games, missed ' ||
         (opportunity.evidence_json::jsonb ->> 'miss_count') || ' times'
         ELSE priority.reason END
         AS gameplay_priority_reason,
       CASE WHEN active_miss.card_id IS NOT NULL THEN %s ELSE priority.priority_date END
         AS priority_date,
       COALESCE(published.priority_score,legacy.priority_score) AS priority_score,
       COALESCE(published.completed_line_ids_json,legacy.completed_line_ids_json)
         AS completed_line_ids_json,
       COALESCE(published.frontier_decisions_json,legacy.frontier_decisions_json)
         AS frontier_decisions_json
FROM eligible_cards c
JOIN LATERAL (
    SELECT c.repertoire_id AS repertoire_id
    UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=c.id
) linked_ids ON TRUE
JOIN repertoires linked ON linked.id=linked_ids.repertoire_id
LEFT JOIN current_gameplay_card_priorities priority ON priority.card_id=c.id
    AND priority.priority_date<=%s
LEFT JOIN active_miss ON active_miss.card_id=c.id
LEFT JOIN current_repertoire_opportunities opportunity ON opportunity.repertoire_id=linked.id
    AND opportunity.card_id=c.id AND opportunity.kind='weak_known_decision'
    AND opportunity.status='active'
    AND (opportunity.evidence_json::jsonb ->> 'analysis_based') IS NULL
LEFT JOIN repertoire_priority_publications publication ON publication.repertoire_id=linked.id
LEFT JOIN current_repertoire_card_priority_generations published ON published.card_id=c.id
    AND published.repertoire_id=linked.id AND published.generation=publication.generation
LEFT JOIN current_repertoire_card_introduction_priorities legacy ON legacy.card_id=c.id
    AND legacy.repertoire_id=linked.id
WHERE c.content_type='opening'
  AND (c.due_date<=%s OR priority.card_id IS NOT NULL OR active_miss.card_id IS NOT NULL
       OR opportunity.id IS NOT NULL)
  AND (c.state='new' OR (c.state='locked' AND (opportunity.id IS NOT NULL OR active_miss.card_id IS NOT NULL)))
  AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
  AND EXISTS(
      SELECT 1 FROM (
          SELECT c.repertoire_id AS repertoire_id
          UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=c.id
      ) eligible_link JOIN repertoires allowed ON allowed.id=eligible_link.repertoire_id
      WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                       WHERE block.repertoire_id=allowed.id AND block.card_id=c.id)
  )
  AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=%s)"""


def route_occurrence(database, repertoire_id, generation, line_id, decision_index, card_id, queue_date):
    return database.execute_native(
        "SELECT step.parent_card_id,c.state,c.due_date,c.start_fen,c.moves_json,"
        "COALESCE(c.trained_color,step.trained_color),step.starting_fen,step.moves_json " + _STUDY_OPENING_ROUTES +
        " AND step.repertoire_id=%s AND step.generation=%s AND step.line_id=%s"
        " AND step.decision_index=%s AND c.id=%s"
        " AND (c.repertoire_id=step.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards link"
        " WHERE link.card_id=c.id AND link.repertoire_id=step.repertoire_id))"
        " AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block"
        " WHERE block.repertoire_id=step.repertoire_id AND block.card_id=c.id)"
        " AND NOT EXISTS(SELECT 1 FROM daily_queue queued"
        " WHERE queued.card_id=c.id AND queued.queue_date=%s)",
        (repertoire_id, generation, line_id,
         decision_index, card_id, queue_date),
    ).fetchone()


def current_opportunity(database, repertoire_id, card_id):
    return database.execute(
        "SELECT 1 FROM current_repertoire_opportunities WHERE repertoire_id=? AND card_id=? "
        "AND kind='weak_known_decision' AND status='active' "
        "AND json_extract(evidence_json,'$.analysis_based') IS NULL LIMIT 1",
        (repertoire_id, card_id),
    ).fetchone()


def parent_study_review(database, parent_card_id):
    return database.execute_native(
        "SELECT 1 FROM cards parent JOIN reviews review ON review.card_id=parent.id "
        "WHERE parent.id=%s AND review.source_kind='study' AND review.invalidated_at IS NULL LIMIT 1",
        (parent_card_id,),
    ).fetchone()


def remaining_allowance(database, queue_date, repertoire_id):
    return database.execute_native(
                "SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) - ("
                "SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id "
                "WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s "
                "AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=r.id) "
                "FROM repertoires r CROSS JOIN settings s WHERE r.id=%s AND s.id=1",
                (queue_date, queue_date, repertoire_id),
            ).fetchone()


def opening_limit(database, repertoire_id):
    return database.execute_native(
        "SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) "
        "FROM repertoires r CROSS JOIN settings s WHERE r.id=%s AND s.id=1",
        (repertoire_id,),
    ).fetchone()


def locked_opening_card(database, card_id):
    return database.execute_native(
        "SELECT state,introduced_at,archived,pending_validation FROM cards WHERE id=%s FOR UPDATE",
        (card_id,),
    ).fetchone()


def admitted_count(database, queue_date, repertoire_id):
    return database.execute_native(
        """SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s
             AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s""",
        (queue_date, queue_date, repertoire_id),
    ).fetchone()[0]


def safe_route(database, repertoire_id, card_id):
    return database.execute_native(
        "SELECT 1 FROM repertoires repertoire WHERE repertoire.id=%s "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
        "WHERE block.repertoire_id=repertoire.id AND block.card_id=%s)",
        (repertoire_id, card_id),
    ).fetchone()


def append_opening(database, queue_date, card_id, position, reason, repertoire_id):
    return database.execute_native(
        """INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,
                                   admission_repertoire_id) VALUES(%s,%s,%s,%s,%s)""",
        (queue_date, card_id, position, reason, repertoire_id),
    )


def resolve_opportunity(database, now, repertoire_id, card_id):
    return database.execute(
        """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
           WHERE repertoire_id=? AND card_id=? AND kind='weak_known_decision'
             AND json_extract(evidence_json,'$.analysis_based') IS NULL AND status='active'""",
        (now, now, repertoire_id, card_id),
    )
