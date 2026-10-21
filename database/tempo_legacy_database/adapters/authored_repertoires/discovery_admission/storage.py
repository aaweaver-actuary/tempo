"""Existing discovery recommendation/admission SQL; caller owns policy and transaction."""

from ... import postgres_store
from ...repertoire_scope.freshness import coverage_scope_predicate


def configured() -> bool:
    return postgres_store.configured()


def analyzed_source_game(database, values):
    return database.execute(
        """SELECT id,start_fen,moves_json,color,analysis_version FROM imported_games
               WHERE id=? AND adaptive_excluded=0 AND analysis_state='ready'""",
        values,
    ).fetchone()


def coverage_source_node(database, values):
    return database.execute(
        "SELECT node.fen_key,node.routes_json,run.settings_json FROM repertoire_coverage_nodes node JOIN repertoire_coverage_runs run ON run.id=node.run_id WHERE node.id=? AND node.repertoire_id=?",
        values,
    ).fetchone()


def coverage_source_lines(database, values):
    return database.execute(
        "SELECT start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
        values,
    ).fetchall()


def background_analyzed_source_game(database, values):
    return database.execute(
        """SELECT id,start_fen,moves_json,color,analysis_version FROM imported_games
                   WHERE id=? AND adaptive_excluded=0 AND analysis_state='ready'""",
        values,
    ).fetchone()


def recommendation_opportunity(database, values):
    return database.execute(
        """SELECT * FROM repertoire_opportunities WHERE id=? AND status='active'
               AND kind IN ('post_gap_weakness','missing_response') AND card_id IS NULL""",
        values,
    ).fetchone()


def task_lease(database, values):
    return database.execute(
        "SELECT generation,lease_token FROM background_tasks WHERE id=?",
        values,
    ).fetchone()


def opportunity(database, values):
    return database.execute(
        "SELECT * FROM repertoire_opportunities WHERE id=?",
        values,
    ).fetchone()


def insert_analysis_request(database, values):
    return database.execute(
        """INSERT OR IGNORE INTO threat_analysis_requests(
                 id,request_json,created_at,updated_at) VALUES(?,?,?,?)""",
        values,
    )


def upsert_coverage_request(database, values):
    return database.execute(
        """INSERT INTO coverage_discovery_recommendation_requests(
                     opportunity_id,request_id,coverage_node_id,route_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(opportunity_id) DO UPDATE SET request_id=excluded.request_id,
                     coverage_node_id=excluded.coverage_node_id,route_json=excluded.route_json,
                     updated_at=excluded.updated_at""",
        values,
    )


def upsert_game_request(database, values):
    return database.execute(
        """INSERT INTO discovery_recommendation_requests(
                 opportunity_id,request_id,source_game_id,source_ply,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(opportunity_id) DO UPDATE SET
                 request_id=excluded.request_id,source_game_id=excluded.source_game_id,
                 source_ply=excluded.source_ply,updated_at=excluded.updated_at""",
        values,
    )


def active_opportunity(database, values):
    return database.execute(
        """SELECT * FROM repertoire_opportunities WHERE id=? AND status='active'""",
        values,
    ).fetchone()


def repertoire_lines(database, values):
    return database.execute(
        "SELECT id,name,start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=?",
        values,
    )


def saved_recommendation(database, game, opportunity_id, request):
    relation_table = ("coverage_discovery_recommendation_requests"
                          if game.get("source_kind") == "coverage" else "discovery_recommendation_requests")
    source_constraint = ("recommendation.coverage_node_id=? AND recommendation.route_json=?"
                             if game.get("source_kind") == "coverage"
                             else "recommendation.source_game_id=? AND recommendation.source_ply=?")
    source_parameters = ((game["coverage_node_id"], game["moves_json"])
                             if game.get("source_kind") == "coverage" else (game["id"], game["ply"]))
    return database.execute(
        f"""SELECT request.state,request.report_json,request.last_error
               FROM {relation_table} recommendation
               JOIN threat_analysis_requests request ON request.id=recommendation.request_id
               WHERE recommendation.opportunity_id=? AND recommendation.request_id=?
                 AND {source_constraint}""",
        (opportunity_id, request.request_id, *source_parameters),
    ).fetchone()


def matching_intent(database, values):
    return database.execute(
        """SELECT * FROM discovery_admission_intents
               WHERE opportunity_id=? AND selected_move_uci=? AND evidence_fingerprint=?""",
        values,
    ).fetchone()


def admission_task_state(database, values):
    return database.execute(
        "SELECT state,priority FROM background_tasks WHERE kind='discovery_admission' AND deduplication_key=?",
        values,
    ).fetchone()


def promote_admission_task(database, values):
    return database.execute(
        """UPDATE background_tasks SET priority=? WHERE kind='discovery_admission'
                           AND deduplication_key=? AND state IN ('queued','retrying') AND priority>?""",
        values,
    )


def begin_write(database):
    return database.execute(
        "BEGIN IMMEDIATE",
    )


def insert_intent(database, values):
    return database.execute(
        """INSERT OR IGNORE INTO discovery_admission_intents(
                 id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,
                 selected_move_uci,preview_moves_json,recommendation_json,line_id,
                 created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        values,
    ).rowcount


def mark_preparing(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET admission_state='preparing',
                     seen_at=COALESCE(seen_at,?),updated_at=? WHERE id=? AND evidence_fingerprint=?""",
        values,
    )


def intent(database, values):
    return database.execute(
        "SELECT * FROM discovery_admission_intents WHERE id=?",
        values,
    ).fetchone()


def source_line_exists(database, values):
    return database.execute(
        "SELECT 1 FROM repertoire_lines WHERE id=?",
        values,
    ).fetchone()


def insert_source_line(database, values):
    return database.execute(
        """INSERT OR IGNORE INTO repertoire_lines(
                 id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
               VALUES(?,?,'Discovery continuation',?,?,?,?)""",
        values,
    ).rowcount


def initial_depth(database):
    return database.execute(
        "SELECT initial_depth FROM settings WHERE id=1",
    ).fetchone()[0]


def insert_training_depth(database, values):
    return database.execute(
        """INSERT OR IGNORE INTO repertoire_line_training_depths(
                     line_id,learner_decision_count) VALUES(?,?)""",
        values,
    )


def coverage_intent(database, values):
    return database.execute(
        "SELECT repertoire_id,line_id,state FROM discovery_admission_intents WHERE id=?",
        values,
    ).fetchone()


def source_line_created_at(database, values):
    return database.execute(
        "SELECT created_at FROM repertoire_lines WHERE id=?",
        values,
    ).fetchone()


def coverage_started_since_line(database, intent, line):
    return database.execute(
        f"""SELECT 1 FROM repertoire_coverage_runs r
               WHERE repertoire_id=? AND created_at>=? AND {coverage_scope_predicate(database, repertoire_id="r.repertoire_id")} LIMIT 1""",
        (intent["repertoire_id"], line["created_at"]),
    ).fetchone()


def published_line_card(database, values):
    return database.execute(
        """SELECT step.card_id FROM opening_graph_steps step
               JOIN opening_graph_publications publication
                 ON publication.repertoire_id=step.repertoire_id
                AND publication.generation=step.generation
               WHERE step.repertoire_id=? AND step.line_id=? AND step.card_id IS NOT NULL
               ORDER BY step.decision_index LIMIT 1""",
        values,
    ).fetchone()


def integrity_publication(database, values):
    return database.execute(
        """SELECT checked_at,scan_status,status FROM repertoire_integrity_state
               WHERE repertoire_id=?""",
        values,
    ).fetchone()


def available_card(database, values):
    return database.execute(
        "SELECT id,archived,pending_validation FROM cards WHERE id=?",
        values,
    ).fetchone()


def card_integrity_block(database, values):
    return database.execute(
        """SELECT 1 FROM repertoire_integrity_card_blocks
                   WHERE repertoire_id=? AND card_id=?""",
        values,
    ).fetchone()


def introduce_card(database, values):
    return database.execute(
        """UPDATE cards SET state=CASE WHEN state IN ('locked','new') THEN 'learning' ELSE state END,
                         introduced_at=COALESCE(introduced_at,?) WHERE id=?""",
        values,
    )


def mark_explicit_admission(database, values):
    return database.execute(
        """UPDATE daily_queue SET admission_kind='explicit',admission_source=?
                       WHERE queue_date=? AND card_id=? AND status='queued'""",
        values,
    )


def mark_intent_queued(database, values):
    return database.execute(
        """UPDATE discovery_admission_intents SET state='queued',card_id=?,updated_at=? WHERE id=?""",
        values,
    )


def complete_opportunity_admission(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET admission_state='queued',
                         handled_evidence_json=evidence_json,card_id=COALESCE(card_id,?),
                         admitted_card_id=?,updated_at=? WHERE id=? AND evidence_fingerprint=?""",
        values,
    )


def requeue_task(database, values):
    return database.execute(
        """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
                 payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    )
