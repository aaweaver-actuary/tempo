"""Existing complete opportunity queries/dialect mechanics; caller owns policy/transaction."""

import sqlite3

from ... import postgres_store
from ...repertoire_scope.freshness import opportunity_scope_predicate


def configured() -> bool:
    return postgres_store.configured()


def is_sqlite_connection(database) -> bool:
    return isinstance(database, sqlite3.Connection)


def requires_write_start(database) -> bool:
    return isinstance(database, sqlite3.Connection) and not database.in_transaction


def supports_native_execution(database) -> bool:
    return hasattr(database, "execute_native")


def publication_previous_reader(database):
    row_lock = " FOR UPDATE" if isinstance(database, postgres_store.PostgresConnection) else ""

    def read_previous(values):
        return database.execute(
            "SELECT status,dismissed_evidence_json,handled_evidence_json,evidence_fingerprint,admission_state "
        f"FROM repertoire_opportunities WHERE id=?{row_lock}",
            values,
        ).fetchone()

    return read_previous


def related_gap_reader(scoped):
    scoped_findings = (" AND EXISTS(SELECT 1 FROM current_game_repertoire_matches scoped_match WHERE scoped_match.game_id=game_findings.game_id AND scoped_match.repertoire_id=game_findings.repertoire_id)"
                       if scoped else "")

    def read_related_findings(database, values):
        return database.execute(
            f"""SELECT id,game_id,analysis_version,card_id,evidence_json FROM current_game_findings game_findings
           WHERE repertoire_id=? AND kind='repertoire gap' AND status!='ignored'
             AND json_extract(evidence_json,'$.opponent_gap_fen_key')=?
             AND json_extract(evidence_json,'$.opponent_gap_move_uci')=? {scoped_findings}
           ORDER BY updated_at DESC,id LIMIT 200""",
            values,
        ).fetchall()

    return read_related_findings


def active_feed_reader(opportunity_ids):
    identifier_clause = (
        f" AND opportunity.id IN ({','.join('?' for _ in opportunity_ids)})"
        if opportunity_ids is not None else ""
    )
    limit_clause = "" if opportunity_ids is not None else " LIMIT 100"

    def read_active_feed(database, repertoire_id):
        return database.execute(
            f"""SELECT opportunity.*,card.trained_color card_trained_color
                   FROM repertoire_opportunities opportunity
                   LEFT JOIN cards card ON card.id=opportunity.card_id
                   WHERE opportunity.repertoire_id=? AND opportunity.status='active'
                     AND {opportunity_scope_predicate()}
                     AND opportunity.handled_evidence_json IS NULL
                   {identifier_clause}
                   ORDER BY opportunity.score DESC,opportunity.id{limit_clause}""",
            (repertoire_id, *(opportunity_ids or [])),
        )

    return read_active_feed


def discovery_window(database):
    return database.execute(
        "SELECT discovery_window_days FROM settings WHERE id=1",
    ).fetchone()


def decision_game_summary(database, values):
    return database.execute(
        """SELECT COUNT(DISTINCT event.game_id) encounter_count,
                  COUNT(DISTINCT CASE WHEN event.outcome='success' THEN event.game_id END) success_count,
                  COUNT(DISTINCT CASE WHEN event.outcome='miss' THEN event.game_id END) miss_count,
                  COUNT(DISTINCT CASE WHEN event.played_at>=? THEN event.game_id END) recent_encounter_count,
                  COUNT(DISTINCT CASE WHEN event.played_at>=? AND event.outcome='success' THEN event.game_id END) recent_success_count,
                  COUNT(DISTINCT CASE WHEN event.played_at>=? AND event.outcome='miss' THEN event.game_id END) recent_miss_count,
                  MAX(event.played_at) last_encountered
           FROM current_repertoire_decision_events event JOIN imported_games game ON game.id=event.game_id
           WHERE event.repertoire_id=? AND event.fen_key=? AND event.expected_uci=?
             AND game.adaptive_excluded=0""",
        values,
    ).fetchone()


def upsert_decision_summary(database, values):
    return database.execute(
        """INSERT INTO repertoire_decision_gameplay_summaries(
            repertoire_id,fen_key,expected_uci,encounter_count,success_count,miss_count,
            recent_encounter_count,recent_success_count,recent_miss_count,last_encountered,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key,expected_uci)
            DO UPDATE SET encounter_count=excluded.encounter_count,
             success_count=excluded.success_count,miss_count=excluded.miss_count,
             recent_encounter_count=excluded.recent_encounter_count,
             recent_success_count=excluded.recent_success_count,
             recent_miss_count=excluded.recent_miss_count,
             last_encountered=excluded.last_encountered,updated_at=excluded.updated_at""",
        values,
    )


def begin_write(database):
    return database.execute(
        "BEGIN IMMEDIATE",
    )


def upsert_opportunity(database, values):
    return database.execute(
        """INSERT INTO repertoire_opportunities(
             id,repertoire_id,kind,fen_key,card_id,opponent_move_uci,status,score,
             evidence_json,evidence_fingerprint,dismissed_evidence_json,handled_evidence_json,created_at,updated_at,resolved_at,canonical_prefix_revision,canonical_scope_source_revision,canonical_scope_preview_id,game_scope_generation
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET card_id=COALESCE(excluded.card_id,
               repertoire_opportunities.card_id,repertoire_opportunities.admitted_card_id),
             opponent_move_uci=excluded.opponent_move_uci,status=excluded.status,
             score=excluded.score,evidence_json=excluded.evidence_json,canonical_prefix_revision=excluded.canonical_prefix_revision,
             canonical_scope_source_revision=excluded.canonical_scope_source_revision,
             canonical_scope_preview_id=excluded.canonical_scope_preview_id,game_scope_generation=excluded.game_scope_generation,
             evidence_fingerprint=excluded.evidence_fingerprint,
             dismissed_evidence_json=CASE WHEN excluded.status='active' THEN NULL
               ELSE repertoire_opportunities.dismissed_evidence_json END,
             handled_evidence_json=excluded.handled_evidence_json,
             admission_state=CASE WHEN ? THEN NULL ELSE repertoire_opportunities.admission_state END,
             admitted_card_id=CASE WHEN ? THEN NULL ELSE repertoire_opportunities.admitted_card_id END,
             snoozed_until=CASE WHEN ? THEN NULL ELSE repertoire_opportunities.snoozed_until END,
             seen_at=CASE WHEN ? THEN NULL ELSE repertoire_opportunities.seen_at END,
             updated_at=excluded.updated_at,resolved_at=NULL""",
        values,
    )


def resolve_opportunity(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
           WHERE id=? AND status!='resolved'""",
        values,
    )


def card_graph_routes(database, values):
    return database.execute(
        """SELECT step.line_id,step.card_id,step.decision_fen_keys_json,step.parent_card_id
           FROM opening_graph_steps step JOIN opening_graph_publications publication
             ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
           WHERE step.repertoire_id=? AND step.card_id=?""",
        values,
    ).fetchall()


def parent_graph_route(database, values):
    return database.execute(
        """SELECT step.decision_fen_keys_json,step.parent_card_id
                   FROM opening_graph_steps step JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
                   WHERE step.repertoire_id=? AND step.line_id=? AND step.card_id=?""",
        values,
    ).fetchone()


def available_card(database, values):
    return database.execute(
        """SELECT id,state,introduced_at,archived,pending_validation FROM cards
           WHERE id=? AND content_type='opening'""",
        values,
    ).fetchone()


def recent_card_games(database, values):
    return database.execute(
        """SELECT event.game_id FROM current_repertoire_decision_events event
           JOIN imported_games game ON game.id=event.game_id
           WHERE event.repertoire_id=? AND event.card_id=? AND event.played_at>=?
             AND game.adaptive_excluded=0
           GROUP BY event.game_id ORDER BY MAX(event.played_at) DESC,event.game_id
           LIMIT ?""",
        values,
    ).fetchall()


def card_game_events(database, repertoire_id, game_ids):
    return database.execute(
        f"""SELECT event.game_id,event.fen_key,event.outcome,event.played_at,event.ply,event.card_id
            FROM current_repertoire_decision_events event
            WHERE event.repertoire_id=? AND event.game_id IN ({','.join('?' for _ in game_ids)})
            ORDER BY event.game_id,event.ply""",
        (repertoire_id, *game_ids),
    ).fetchall()


def recurring_decision_rows(database, repertoire_id, card_id, cutoff):
    postgres = postgres_store.configured()
    execute = database.execute_native if postgres else database.execute
    placeholder = "%s" if postgres else "?"
    even_ply_gap = ("MOD(event.ply-earlier.ply,2)=0" if postgres
                    else "(event.ply-earlier.ply)%2=0")
    analysis_joins = (
        """LEFT JOIN LATERAL (
             SELECT loss_cp,eval_after_cp,mate_before,mate_after
             FROM game_move_analysis
             WHERE game_id=event.game_id AND ply=event.ply LIMIT 1
           ) analysis ON TRUE
           LEFT JOIN LATERAL (
             SELECT eval_after_cp,mate_after FROM game_move_analysis
             WHERE game_id=event.game_id AND ply=event.ply-2 LIMIT 1
           ) previous ON TRUE
           LEFT JOIN LATERAL (
             SELECT eval_after_cp,mate_after FROM game_move_analysis
             WHERE game_id=event.game_id AND ply=event.ply+6 LIMIT 1
           ) following ON TRUE""" if postgres else
        """LEFT JOIN game_move_analysis analysis
             ON analysis.game_id=event.game_id AND analysis.ply=event.ply
           LEFT JOIN game_move_analysis previous
             ON previous.game_id=event.game_id AND previous.ply=event.ply-2
           LEFT JOIN game_move_analysis following
             ON following.game_id=event.game_id AND following.ply=event.ply+6"""
    )
    return execute(
        f"""SELECT event.game_id,event.fen_key,event.expected_uci,event.ply,
                  game.color,analysis.loss_cp,analysis.eval_after_cp,
                  analysis.mate_before,analysis.mate_after,
                  previous.eval_after_cp previous_eval_cp,previous.mate_after previous_mate,
                  following.eval_after_cp following_eval_cp,following.mate_after following_mate,
                  (SELECT COUNT(*) FROM game_move_analysis earlier
                   WHERE earlier.game_id=event.game_id AND earlier.ply<event.ply
                     AND {even_ply_gap} AND earlier.loss_cp IS NOT NULL)
                     previous_analyzed_count,
                  (SELECT COUNT(*) FROM game_move_analysis earlier
                   WHERE earlier.game_id=event.game_id AND earlier.ply<event.ply
                     AND {even_ply_gap} AND earlier.loss_cp>30)
                     previous_weak_count
           FROM current_repertoire_decision_events event
           JOIN imported_games game ON game.id=event.game_id
           {analysis_joins}
           WHERE event.repertoire_id={placeholder} AND event.card_id={placeholder}
             AND event.played_at>={placeholder}
             AND game.adaptive_excluded=0
           ORDER BY event.fen_key,event.game_id,event.ply""",
        (repertoire_id, card_id, cutoff),
    )


def active_analysis_opportunities(database, values):
    return database.execute(
        """SELECT id FROM repertoire_opportunities WHERE repertoire_id=?
           AND kind='weak_known_decision' AND card_id=? AND status='active'
           AND CAST(json_extract(evidence_json,'$.analysis_based') AS TEXT) IN ('1','true')""",
        values,
    ).fetchall()


def latest_card_decision(database, values):
    return database.execute(
        """SELECT fen_key,expected_uci FROM current_repertoire_decision_events repertoire_decision_events
           WHERE repertoire_id=? AND card_id=? ORDER BY played_at DESC LIMIT 1""",
        values,
    ).fetchone()


def update_route_success(database, values):
    return database.execute(
        """UPDATE repertoire_decision_gameplay_summaries SET route_success_count=?,updated_at=?
               WHERE repertoire_id=? AND fen_key=? AND expected_uci=?""",
        values,
    )


def card_route_opportunities(database, values):
    return database.execute(
        """SELECT id FROM repertoire_opportunities WHERE repertoire_id=?
           AND kind='weak_known_decision' AND card_id=?
           AND json_extract(evidence_json,'$.analysis_based') IS NULL""",
        values,
    ).fetchall()


def active_opportunity_exists(database, values):
    return database.execute(
        """SELECT 1 FROM repertoire_opportunities WHERE id=? AND status='active'""",
        values,
    ).fetchone()


def latest_coverage_node(database, values):
    return database.execute(
        """SELECT n.*,r.settings_json,r.status run_status,r.created_at run_created_at
           FROM repertoire_coverage_nodes n JOIN repertoire_coverage_runs r ON r.id=n.run_id
           WHERE n.repertoire_id=? AND n.fen_key=? AND n.run_id=?""",
        values,
    ).fetchone()


def coverage_candidates(database, values):
    return database.execute(
        "SELECT * FROM repertoire_coverage_candidates WHERE node_id=? ORDER BY move_uci",
        values,
    ).fetchall()


def coverage_node_key(database, values):
    return database.execute(
        "SELECT fen_key FROM repertoire_coverage_nodes WHERE id=? AND repertoire_id=?",
        values,
    ).fetchone()


def personal_position_moves(database, values):
    return database.execute(
        """SELECT occurrence.move_uci,COUNT(DISTINCT occurrence.game_id) encounters
           FROM game_position_occurrences occurrence
           JOIN imported_games game ON game.id=occurrence.game_id
           JOIN current_game_repertoire_matches match ON match.game_id=game.id
             AND match.repertoire_id=?
           WHERE occurrence.fen_key=? AND occurrence.move_uci IS NOT NULL
             AND game.color=? AND game.adaptive_excluded=0 AND game.played_at>=?
           GROUP BY occurrence.move_uci""",
        values,
    ).fetchall()


def personal_repertoire_games(database, values):
    return database.execute(
        """SELECT COUNT(DISTINCT game.id) FROM current_game_repertoire_matches match
           JOIN imported_games game ON game.id=match.game_id
           WHERE match.repertoire_id=? AND game.color=? AND game.adaptive_excluded=0
             AND game.played_at>=?""",
        values,
    ).fetchone()[0]


def gap_consequences(database, values):
    return database.execute(
        """SELECT json_extract(evidence_json,'$.opponent_gap_move_uci') move_uci,
                  COUNT(DISTINCT game_id) game_count,
                  MAX(CAST(json_extract(evidence_json,'$.mistake_loss_cp') AS INTEGER)) max_loss_cp
           FROM current_game_findings game_findings WHERE repertoire_id=? AND kind='repertoire gap'
             AND status!='ignored' AND json_extract(evidence_json,'$.opponent_gap_fen_key')=?
           GROUP BY move_uci""",
        values,
    ).fetchall()


def existing_missing_moves(database, values):
    return database.execute(
        "SELECT opponent_move_uci FROM repertoire_opportunities WHERE repertoire_id=? AND fen_key=? AND kind='missing_response'",
        values,
    ).fetchall()


def gap_finding(database, values):
    return database.execute(
        "SELECT * FROM current_game_findings game_findings WHERE id=? AND repertoire_id=? AND kind='repertoire gap'",
        values,
    ).fetchone()


def game_matches_repertoire(database, values):
    return database.execute(
        "SELECT 1 FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=? AND repertoire_id=?",
        values,
    ).fetchone()


def advance_slice(database, values):
    return database.execute(
        """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
             payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
             lease_expires_at=NULL,updated_at=?
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    )


def opportunity(database, values):
    return database.execute(
        "SELECT * FROM repertoire_opportunities WHERE id=? AND repertoire_id=?",
        values,
    ).fetchone()


def card_state(database, values):
    return database.execute(
        "SELECT state,archived FROM cards WHERE id=?",
        values,
    ).fetchone()


def card_introduced_at(database, values):
    return database.execute(
        "SELECT introduced_at FROM cards WHERE id=?",
        values,
    ).fetchone()


def gap_finding_exists(database, values):
    return database.execute(
        """SELECT 1 FROM current_game_findings game_findings WHERE repertoire_id=? AND kind='repertoire gap'
               AND status!='ignored' AND json_extract(evidence_json,'$.opponent_gap_fen_key')=?
               AND json_extract(evidence_json,'$.opponent_gap_move_uci')=? LIMIT 1""",
        values,
    ).fetchone()


def next_decision_summary(database, values):
    return database.execute(
        """SELECT fen_key,expected_uci FROM current_repertoire_decision_events repertoire_decision_events
                   WHERE repertoire_id=? AND (fen_key>? OR (fen_key=? AND expected_uci>?))
                   GROUP BY fen_key,expected_uci ORDER BY fen_key,expected_uci LIMIT 1""",
        values,
    ).fetchone()


def next_graph_card(database, values):
    return database.execute(
        """SELECT DISTINCT step.card_id id FROM opening_graph_steps step
                   JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.repertoire_id=? AND step.card_id>? ORDER BY step.card_id LIMIT 1""",
        values,
    ).fetchone()


def next_coverage_node(database, values):
    return database.execute(
        """SELECT id FROM repertoire_coverage_nodes WHERE repertoire_id=? AND run_id=?
                   AND id>? ORDER BY id LIMIT 1""",
        values,
    ).fetchone()


def next_gap_finding(database, values):
    return database.execute(
        """SELECT id FROM current_game_findings game_findings WHERE repertoire_id=? AND kind='repertoire gap'
                   AND id>? ORDER BY id LIMIT 1""",
        values,
    ).fetchone()


def next_unresolved_opportunity(database, values):
    return database.execute(
        """SELECT id FROM repertoire_opportunities WHERE repertoire_id=? AND id>?
                   AND status!='resolved' ORDER BY id LIMIT 1""",
        values,
    ).fetchone()


def next_stored_summary(database, values):
    return database.execute(
        """SELECT fen_key,expected_uci FROM repertoire_decision_gameplay_summaries
                   WHERE repertoire_id=? AND (fen_key>? OR (fen_key=? AND expected_uci>?))
                   ORDER BY fen_key,expected_uci LIMIT 1""",
        values,
    ).fetchone()


def cleanup_opportunity(database, values):
    return database.execute(
        "SELECT kind,fen_key FROM repertoire_opportunities WHERE id=? AND repertoire_id=?",
        values,
    ).fetchone()


def slice_lease(database, values):
    return database.execute(
        "SELECT generation,lease_token FROM background_tasks WHERE id=?",
        values,
    ).fetchone()


def delete_obsolete_summary(database, values):
    return database.execute(
        """DELETE FROM repertoire_decision_gameplay_summaries WHERE repertoire_id=?
                   AND fen_key=? AND expected_uci=? AND NOT EXISTS(
                       SELECT 1 FROM current_repertoire_decision_events event
                       WHERE event.repertoire_id=? AND event.fen_key=? AND event.expected_uci=?)""",
        values,
    )


def accepted_line_snapshot(database, values):
    return database.execute(
        "SELECT start_fen,moves_json,trained_color FROM repertoire_lines "
            "WHERE repertoire_id=? ORDER BY id",
        values,
    )


def source_game(database, values):
    return database.execute(
        "SELECT id,game_url,start_fen,moves_json FROM imported_games WHERE id=?",
        values,
    ).fetchone()


def source_decision_ply(database, values):
    return database.execute(
        """SELECT ply FROM current_repertoire_decision_events repertoire_decision_events WHERE repertoire_id=?
                   AND game_id=? AND fen_key=? ORDER BY ply LIMIT 1""",
        values,
    ).fetchone()


def source_coverage_node(database, values):
    return database.execute(
        "SELECT fen,routes_json,trained_color FROM repertoire_coverage_nodes WHERE id=?",
        values,
    ).fetchone()


def source_line_routes(database, values):
    return database.execute(
        "SELECT start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
        values,
    )


def game_route(database, values):
    return database.execute(
        "SELECT start_fen,moves_json FROM imported_games WHERE id=?",
        values,
    ).fetchone()


def active_state_opportunity(database, values):
    row_lock = " FOR UPDATE" if hasattr(database, "execute_native") else ""
    return database.execute(
        "SELECT * FROM repertoire_opportunities "
        f"WHERE id=? AND repertoire_id=? AND status='active'{row_lock}",
        values,
    ).fetchone()


def dismiss_opportunity(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET status='dismissed',dismissed_evidence_json=?,updated_at=?
           WHERE id=?""",
        values,
    )


def acknowledge_opportunity(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET seen_at=?,snoozed_until=NULL,updated_at=?
           WHERE id=? AND repertoire_id=? AND status='active'""",
        values,
    ).rowcount


def snooze_opportunity(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET seen_at=COALESCE(seen_at,?),
             snoozed_until=?,updated_at=?
           WHERE id=? AND repertoire_id=? AND status='active'""",
        values,
    ).rowcount


def training_opportunity(database, values, lock_for_write):
    row_lock = " FOR UPDATE" if lock_for_write and isinstance(database, postgres_store.PostgresConnection) else ""
    return database.execute(
        f"SELECT * FROM repertoire_opportunities WHERE id=? AND repertoire_id=?{row_lock}",
        values,
    ).fetchone()


def trainable_card(database, values):
    return database.execute(
        """SELECT * FROM cards WHERE id=? AND content_type='opening'
           AND archived=0 AND COALESCE(pending_validation,0)=0""",
        values,
    ).fetchone()


def card_integrity_block(database, values):
    return database.execute(
        """SELECT 1 FROM repertoire_integrity_card_blocks
           WHERE repertoire_id=? AND card_id=?""",
        values,
    ).fetchone()


def card_membership(database, values):
    return database.execute(
        """SELECT 1 FROM repertoire_cards WHERE repertoire_id=? AND card_id=?""",
        values,
    ).fetchone()


def introduce_card(database, values):
    return database.execute(
        """UPDATE cards SET state=CASE WHEN state IN ('locked','new') THEN 'learning' ELSE state END,
             introduced_at=COALESCE(introduced_at,?) WHERE id=?""",
        values,
    )


def mark_explicit_queue_admission(database, values):
    return database.execute(
        """UPDATE daily_queue SET admission_kind='explicit',admission_source=?
           WHERE queue_date=? AND card_id=? AND status='queued'""",
        values,
    )


def complete_opportunity_admission(database, values):
    return database.execute(
        """UPDATE repertoire_opportunities SET admission_state='queued',
             handled_evidence_json=evidence_json,admitted_card_id=?,seen_at=COALESCE(seen_at,?),updated_at=?
             WHERE id=? AND evidence_fingerprint=?""",
        values,
    )
