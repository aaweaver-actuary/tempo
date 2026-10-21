"""Existing fixed ranking SQL preparation and borrowed read-only native operations."""
from ..real_game_miss_projection import outstanding_miss_sql


def bounded_rows(database, query, parameters, *, limit, remaining_bytes):
    return database.execute_native(
        "SELECT CASE WHEN SUM(octet_length(row_to_json(raw)::text)) OVER ()<=%s "
        "THEN row_to_json(raw)::text ELSE NULL END AS row_json, "
        "SUM(octet_length(row_to_json(raw)::text)) OVER () AS total_bytes "
        f"FROM ({query} LIMIT %s) raw",
        (remaining_bytes, *parameters, limit + 1),
    ).fetchall()


def capture_boundary(database, timezone_name):
    return database.execute_native(
        "SELECT transaction_timestamp() AS as_of, "
        "(transaction_timestamp() AT TIME ZONE %s)::date::text AS study_day", (timezone_name,),
    ).fetchone()


def prepare_capture_queries(repertoire_ids, priority_page_statement):
    candidate_query = priority_page_statement.replace(
        "SELECT unnest(%s::text[]) AS card_id", outstanding_miss_sql(postgres=True),
    ).replace("SELECT * FROM cards WHERE id=ANY(%s::text[])",
              "SELECT * FROM cards WHERE content_type='opening' AND state IN ('new','locked') "
              "AND introduced_at IS NULL AND archived=0 AND COALESCE(pending_validation,0)=0")
    candidate_query = candidate_query.replace(
        "SELECT DISTINCT c.id,linked.id AS repertoire_id,c.moves_json,c.due_date,",
        "SELECT DISTINCT c.id,linked.id AS repertoire_id,c.moves_json,c.due_date,"
        "c.revision,c.start_fen,c.state,c.introduced_at,c.archived,c.pending_validation,"
        "linked.name AS repertoire_name,publication.generation AS priority_generation,"
        "COALESCE(published.scoring_version,legacy.scoring_version) AS scoring_version,"
        "COALESCE(published.updated_at,legacy.updated_at) AS priority_updated_at,"
        "COALESCE(published.evidence_json,legacy.evidence_json) AS priority_evidence_json,"
        "COALESCE(published.completion_mass,legacy.completion_mass) AS completion_mass,"
        "COALESCE(published.frontier_reach,legacy.frontier_reach) AS frontier_reach,",
    )
    selection_parameters = ()
    if repertoire_ids:
        candidate_query += " AND linked.id=ANY(%s::text[])"
        selection_parameters = (list(sorted(set(repertoire_ids))),)
    candidate_query += " ORDER BY linked.id,c.id"
    eligible_cards = f"SELECT DISTINCT id FROM ({candidate_query}) eligible"
    eligible_repertoires = f"SELECT DISTINCT repertoire_id FROM ({candidate_query}) eligible"

    def bind_queries(miss_reason, current_day, max_route_rows):
        parameters = (miss_reason, current_day, current_day, current_day, current_day, *selection_parameters)
        queries = (
            ("candidates", candidate_query, parameters, max_route_rows),
            ("routes", "SELECT step.* FROM opening_graph_steps step JOIN opening_graph_publications publication "
             "ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation "
             f"WHERE step.card_id IN ({eligible_cards}) AND step.repertoire_id IN ({eligible_repertoires}) "
             "ORDER BY step.repertoire_id,step.line_id,step.decision_index", parameters + parameters, max_route_rows),
            ("repertoires", "SELECT repertoire.id,repertoire.name,COALESCE(repertoire.new_cards_per_day,settings.new_cards_per_day) AS daily_limit,"
             "graph.generation AS graph_generation,graph.state AS graph_state,graph.published_at AS graph_published_at,"
             "priority.generation AS priority_generation,priority.updated_at AS priority_published_at,"
             "preparation.source_version,preparation.calculated_at,preparation.scoring_version,epoch.version AS priority_source_epoch "
             "FROM repertoires repertoire CROSS JOIN settings LEFT JOIN opening_graph_publications graph ON graph.repertoire_id=repertoire.id "
             "LEFT JOIN repertoire_priority_publications priority ON priority.repertoire_id=repertoire.id "
             "LEFT JOIN repertoire_priority_preparations preparation ON preparation.repertoire_id=repertoire.id AND preparation.generation=priority.generation "
             "LEFT JOIN priority_repertoire_source_epochs epoch ON epoch.repertoire_id=repertoire.id "
             f"WHERE settings.id=1 AND repertoire.id IN ({eligible_repertoires}) ORDER BY repertoire.id", parameters, max_route_rows),
            ("introductions", "SELECT COALESCE(queue.admission_repertoire_id,card.repertoire_id) AS repertoire_id,COUNT(DISTINCT card.id) AS count "
             "FROM daily_queue queue JOIN cards card ON card.id=queue.card_id "
             "WHERE queue.queue_date=%s AND queue.cycle=0 AND card.content_type='opening' AND card.introduced_at=%s "
             "GROUP BY COALESCE(queue.admission_repertoire_id,card.repertoire_id)", (current_day, current_day), max_route_rows),
            ("global_source", "SELECT version FROM priority_source_epoch WHERE id=1", (), 1),
        )
        return queries

    return bind_queries
