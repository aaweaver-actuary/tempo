"""Complete legacy defensive-threat facts, claims and writes on caller scopes.

Application detection/validation/seed ranking, clocks, leases and follow-up
choices stay outside this module. No connection, commit/rollback or transaction owner is created here; the original
explicit claim BEGIN remains a fixed operation within its caller-owned scope.
"""
from ..controls.claim_sql import claimable, control_order
from ..defensive_analysis import search_admission_sql, recommendation_request_ids_sql


THREAT_CANDIDATE_UPSERT_SQL = """INSERT INTO threat_training_candidates(
       id,finding_id,game_id,analysis_version,incident_id,player_ply,
       evidence_json,source_fingerprint,detector_version,policy_json,
       created_at,updated_at)
   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(id) DO UPDATE SET
       analysis_version=excluded.analysis_version,
       evidence_json=excluded.evidence_json,
       source_fingerprint=excluded.source_fingerprint,
       superseded_at=NULL,
       validation_state=CASE WHEN threat_training_candidates.source_fingerprint!=excluded.source_fingerprint
           THEN 'needs_analysis' ELSE threat_training_candidates.validation_state END,
       validation_json=CASE WHEN threat_training_candidates.source_fingerprint!=excluded.source_fingerprint
           THEN '{}' ELSE threat_training_candidates.validation_json END,
       dismissed_at=CASE WHEN threat_training_candidates.source_fingerprint!=excluded.source_fingerprint
           THEN NULL ELSE threat_training_candidates.dismissed_at END,
       dismissed_evidence_fingerprint=CASE WHEN threat_training_candidates.source_fingerprint!=excluded.source_fingerprint
           THEN NULL ELSE threat_training_candidates.dismissed_evidence_fingerprint END,
       approved_at=CASE WHEN threat_training_candidates.source_fingerprint!=excluded.source_fingerprint
           THEN NULL ELSE threat_training_candidates.approved_at END,
       exercise_revision=threat_training_candidates.exercise_revision+CASE WHEN threat_training_candidates.source_fingerprint!=excluded.source_fingerprint
           THEN 1 ELSE 0 END,
       updated_at=excluded.updated_at"""


def next_saved_report(database, after_id):
    return database.execute("""SELECT id,request_json,report_json FROM threat_analysis_requests
               WHERE id>? AND state='complete' ORDER BY id LIMIT 1""", (after_id,)).fetchone()


def task_lease(database, task_id):
    return database.execute("SELECT generation,lease_token FROM background_tasks WHERE id=?", (task_id,)).fetchone()


def saved_report(database, request_id):
    return database.execute("SELECT report_json,state FROM threat_analysis_requests WHERE id=?", (request_id,)).fetchone()


def archive_report(database, request_id, report_json, reason, archived_at):
    database.execute("""INSERT INTO threat_analysis_report_history(
                     request_id,report_json,rejection_reason,archived_at) VALUES(?,?,?,?)""", (request_id, report_json, reason, archived_at))


def queue_report_repair(database, message, updated_at, request_id):
    database.execute("""UPDATE threat_analysis_requests SET state='queued',report_json=NULL,
                     last_error=?,updated_at=? WHERE id=?""", (message, updated_at, request_id))


def reset_linked_candidates(database, updated_at, request_id):
    database.execute("""UPDATE threat_training_candidates SET validation_state='needs_analysis',
                     validation_json='{}',approved_at=NULL,updated_at=?
                   WHERE id IN (SELECT candidate_id FROM threat_candidate_requests WHERE request_id=?)""", (updated_at, request_id))


def block_linked_cards(database, request_id):
    database.execute("""UPDATE cards SET pending_validation=1 WHERE id IN (
                     SELECT candidate.card_id FROM threat_training_candidates candidate
                     JOIN threat_candidate_requests relation ON relation.candidate_id=candidate.id
                     WHERE relation.request_id=? AND candidate.card_id IS NOT NULL)""", (request_id,))


def advance_audit(database, payload_json, next_attempt_at, updated_at, task_id, generation, lease_token):
    database.execute("""UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
                 payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""", (payload_json, next_attempt_at, updated_at, task_id, generation, lease_token))


def next_analyzed_game(database, after_id):
    return database.execute("""SELECT id,analysis_version FROM imported_games
                   WHERE id>? AND analysis_version>0
                     AND analysis_state IN ('ready','complete')
                   ORDER BY id LIMIT 1""", (after_id,)).fetchone()


def next_repertoire(database, after_id, excluded_id):
    return database.execute("SELECT id FROM repertoires WHERE id>? AND id!=? ORDER BY id LIMIT 1", (after_id, excluded_id)).fetchone()


def advance_backfill(database, payload_json, next_attempt_at, updated_at, task_id, generation, lease_token):
    database.execute("""UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
                 payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""", (payload_json, next_attempt_at, updated_at, task_id, generation, lease_token))


def advance_scan(database, payload_json, next_attempt_at, updated_at, task_id, generation, lease_token):
    database.execute("""UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
              payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
              lease_expires_at=NULL,updated_at=?
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'""", (payload_json, next_attempt_at, updated_at, task_id, generation, lease_token))


def existing_finding(database, finding_id):
    return database.execute("SELECT evidence_json FROM current_game_findings game_findings WHERE id=?", (finding_id,)).fetchone()


def upsert_finding(database, finding_id, game_id, analysis_version, ply, evidence_json, created_at, updated_at):
    database.execute("""INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,
                  evidence_json,motif,created_at,updated_at)
           VALUES(?,?,?,?, 'defensive tactical threat',0,?,'fork',?,?)
           ON CONFLICT(id) DO UPDATE SET analysis_version=excluded.analysis_version,
                  evidence_json=excluded.evidence_json,
                  updated_at=excluded.updated_at""", (finding_id, game_id, analysis_version, ply, evidence_json, created_at, updated_at))


def active_incident_candidates(database, incident_id):
    return database.execute("""SELECT id,card_id,player_ply FROM threat_training_candidates
           WHERE incident_id=? AND superseded_at IS NULL""", (incident_id,)).fetchall()


def supersede_candidate(database, superseded_at, updated_at, candidate_id):
    database.execute("UPDATE threat_training_candidates SET superseded_at=?,updated_at=? WHERE id=?", (superseded_at, updated_at, candidate_id))


def block_card(database, card_id):
    database.execute("UPDATE cards SET pending_validation=1 WHERE id=?", (card_id,))


def candidate_source(database, candidate_id):
    return database.execute("SELECT source_fingerprint,card_id FROM threat_training_candidates WHERE id=?", (candidate_id,)).fetchone()


def upsert_candidate(
    database, candidate_id, finding_id, game_id, analysis_version, incident_id, player_ply, evidence_json,
    source_fingerprint, detector_version, policy_json, created_at, updated_at,
):
    database.execute(THREAT_CANDIDATE_UPSERT_SQL, (candidate_id, finding_id, game_id, analysis_version, incident_id, player_ply, evidence_json, source_fingerprint, detector_version, policy_json, created_at, updated_at))


def clear_candidate_requests(database, candidate_id):
    database.execute("DELETE FROM threat_candidate_requests WHERE candidate_id=?", (candidate_id,))


def retain_analysis_request(database, request_id, request_json, created_at, updated_at):
    database.execute("""INSERT OR IGNORE INTO threat_analysis_requests(
                       id,request_json,created_at,updated_at) VALUES(?,?,?,?)""", (request_id, request_json, created_at, updated_at))


def retain_candidate_request(database, candidate_id, request_id, role):
    database.execute("""INSERT OR IGNORE INTO threat_candidate_requests(candidate_id,request_id,role)
                   VALUES(?,?,?)""", (candidate_id, request_id, role))


def scan_game(database, game_id):
    return database.execute("SELECT id,analysis_version,start_fen,moves_json,color FROM imported_games WHERE id=?", (game_id,)).fetchone()


def scan_candidate_lines(database, game_id, ply):
    rows = database.execute("""SELECT principal_variation_json FROM game_move_analysis_candidates
               WHERE game_id=? AND ply=? ORDER BY rank LIMIT 5""", (game_id, ply))
    return (row for row in rows)


def current_game_version(database, game_id):
    return database.execute("SELECT analysis_version FROM imported_games WHERE id=?", (game_id,)).fetchone()


def supersede_old_generation(database, superseded_at, game_id, analysis_version):
    database.execute("""UPDATE threat_training_candidates SET superseded_at=COALESCE(superseded_at,?)
                   WHERE game_id=? AND analysis_version!=? AND superseded_at IS NULL""", (superseded_at, game_id, analysis_version))


def validation_candidate(database, candidate_id):
    return database.execute("""SELECT c.*,g.analysis_version current_version FROM threat_training_candidates c
               JOIN imported_games g ON g.id=c.game_id WHERE c.id=?""", (candidate_id,)).fetchone()


def validation_reports(database, candidate_id):
    rows = database.execute("""SELECT relation.role,request.request_json,request.report_json FROM threat_candidate_requests relation
               JOIN threat_analysis_requests request ON request.id=relation.request_id
               WHERE relation.candidate_id=? AND relation.role IN ('best','historical')""", (candidate_id,))
    return (row for row in rows)


def locked_validation_candidate(database, candidate_id):
    return database.execute("SELECT candidate.source_fingerprint,candidate.analysis_version,"
                "candidate.superseded_at,game.analysis_version current_version "
                "FROM threat_training_candidates candidate "
                "JOIN imported_games game ON game.id=candidate.game_id "
                "WHERE candidate.id=? FOR UPDATE OF candidate", (candidate_id,)).fetchone()


def save_validation(database, state, diagnostic, validation_json, updated_at, candidate_id, source_fingerprint):
    database.execute("""UPDATE threat_training_candidates
               SET validation_state=?,diagnostic=?,validation_json=?,updated_at=?
               WHERE id=? AND source_fingerprint=? AND superseded_at IS NULL""", (state, diagnostic, validation_json, updated_at, candidate_id, source_fingerprint))


def begin_claim(database):
    database.execute("BEGIN IMMEDIATE")


def requeue_expired_requests(database, updated_at, as_of):
    database.execute("""UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,
                  lease_expires_at=NULL,updated_at=?
               WHERE state='leased' AND lease_expires_at<=?""", (updated_at, as_of))


def interactive_request(database, request_id):
    return database.execute("SELECT 1 FROM threat_candidate_requests WHERE request_id=? AND role='attempt' LIMIT 1", (request_id,)).fetchone()


def claim_request(database, lease_id, expires_at, updated_at, request_id):
    return database.execute("""UPDATE threat_analysis_requests SET state='leased',lease_id=?,
                  lease_expires_at=?,attempts=attempts+1,updated_at=? WHERE id=? AND state='queued'""", (lease_id, expires_at, updated_at, request_id)).rowcount


def saved_request_json(database, request_id):
    return database.execute("SELECT request_json FROM threat_analysis_requests WHERE id=?", (request_id,)).fetchone()


def saved_request_state(database, request_id):
    return database.execute("SELECT state,lease_id,request_json FROM threat_analysis_requests WHERE id=?", (request_id,)).fetchone()


def complete_report(database, report_json, updated_at, request_id):
    database.execute("""UPDATE threat_analysis_requests SET state='complete',report_json=?,
                  lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE id=?""", (report_json, updated_at, request_id))


def report_candidate_ids(database, request_id):
    rows = database.execute("SELECT DISTINCT candidate_id FROM threat_candidate_requests WHERE request_id=?", (request_id,))
    return (row for row in rows)


def request_selector(enabled):
    recommendation_filter = ("" if enabled else
        "AND request.id IN (" + recommendation_request_ids_sql() + ")")

    def select(database):
        return database.execute(f"""SELECT request.id,request.request_json FROM threat_analysis_requests request
               WHERE request.state='queued' AND {claimable('threat_analysis', 'request.id')}
                 AND {search_admission_sql('request.id')} {recommendation_filter}
                 AND (EXISTS(
                   SELECT 1 FROM threat_candidate_requests relation
                   JOIN threat_training_candidates candidate ON candidate.id=relation.candidate_id
                   JOIN imported_games game ON game.id=candidate.game_id
                   WHERE relation.request_id=request.id AND candidate.superseded_at IS NULL
                     AND candidate.analysis_version=game.analysis_version
                     AND (candidate.validation_state='needs_analysis' OR relation.role='attempt'))
                   OR EXISTS(
                     SELECT 1 FROM discovery_recommendation_requests recommendation
                     JOIN current_repertoire_opportunities opportunity
                       ON opportunity.id=recommendation.opportunity_id
                     WHERE recommendation.request_id=request.id
                       AND opportunity.status='active' AND opportunity.card_id IS NULL)
                   OR EXISTS(
                     SELECT 1 FROM coverage_discovery_recommendation_requests recommendation
                     JOIN current_repertoire_opportunities opportunity
                       ON opportunity.id=recommendation.opportunity_id
                     WHERE recommendation.request_id=request.id
                       AND opportunity.status='active' AND opportunity.card_id IS NULL))
               ORDER BY CASE WHEN EXISTS(SELECT 1 FROM threat_candidate_requests foreground
                   WHERE foreground.request_id=request.id AND foreground.role='attempt')
                   THEN 0 ELSE 1 END,
                   {control_order('threat_analysis', 'request.id')}
                   request.created_at,request.id LIMIT 1""").fetchone()

    return select
