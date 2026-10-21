"""Existing complete defensive exercise/admission/recognition/review persistence.

Application rubric/grading/admission/replay/scheduling/clock/callback decisions
remain outside. All operations retain the original caller-owned transaction.
"""
from .pipeline import task_lease, advance_audit as advance_task


_ADMISSION_SELECTION_SQL = """SELECT c.*,game.analysis_version current_analysis_version,
                      COALESCE((SELECT COUNT(DISTINCT recurring.game_id)
                          FROM game_position_occurrences anchor
                          JOIN game_position_occurrences recurring ON recurring.fen_key=anchor.fen_key
                          JOIN imported_games recent_game ON recent_game.id=recurring.game_id
                          WHERE anchor.game_id=c.game_id AND anchor.ply=c.player_ply
                            AND recent_game.played_at>=? AND recent_game.adaptive_excluded=0),0)
                        recent_position_games
               FROM threat_training_candidates c JOIN imported_games game ON game.id=c.game_id
               WHERE c.validation_state=?
                 AND c.approved_at IS NULL AND c.dismissed_at IS NULL AND c.paused_at IS NULL
                 AND c.superseded_at IS NULL AND c.analysis_version=game.analysis_version
                 AND NOT EXISTS(SELECT 1 FROM threat_training_candidates earlier
                     WHERE earlier.incident_id=c.incident_id AND earlier.player_ply<c.player_ply
                       AND earlier.superseded_at IS NULL AND earlier.dismissed_at IS NULL
                       AND earlier.validation_state IN ('needs_analysis','engine_supported','validated_control'))
               ORDER BY recent_position_games DESC,game.played_at DESC,c.id LIMIT 1 OFFSET ?"""


def persist_rubric_validation(database, state, diagnostic, validation_json, eligible, updated_at, candidate_id):
    database.execute("""UPDATE threat_training_candidates SET validation_state=?,diagnostic=?,
           validation_json=?,approved_at=CASE WHEN ? THEN approved_at ELSE NULL END,
           exercise_revision=exercise_revision+1,updated_at=? WHERE id=?""", (state, diagnostic, validation_json, eligible, updated_at, candidate_id))


def next_rubric_candidate(database, after_id):
    return database.execute("""SELECT candidate.* FROM threat_training_candidates candidate
               JOIN imported_games game ON game.id=candidate.game_id
               WHERE candidate.id>? AND candidate.superseded_at IS NULL
                 AND candidate.analysis_version=game.analysis_version
                 AND (candidate.validation_state IN ('engine_supported','validated_control')
                      OR candidate.approved_at IS NOT NULL)
               ORDER BY candidate.id LIMIT 1""", (after_id,)).fetchone()


def rubric_reports(database, candidate_id):
    rows = database.execute("""SELECT relation.role,request.request_json,request.report_json
               FROM threat_candidate_requests relation JOIN threat_analysis_requests request
                 ON request.id=relation.request_id
               WHERE relation.candidate_id=? AND relation.role IN ('best','historical')""", (candidate_id,))
    return (row for row in rows)


def rubric_candidate_source(database, candidate_id):
    return database.execute("SELECT source_fingerprint,exercise_revision,card_id FROM threat_training_candidates WHERE id=?", (candidate_id,)).fetchone()


def revise_card_validation(database, pending_validation, card_id):
    database.execute("UPDATE cards SET pending_validation=?,revision=revision+1 WHERE id=?", (pending_validation, card_id))


def block_queued_card(database, card_id):
    database.execute("UPDATE daily_queue SET status='blocked' WHERE card_id=? AND status='queued'", (card_id,))


def invalidate_rubric_reviews(database, invalidated_at, reason, card_id, candidate_id, exercise_revision):
    database.execute("""UPDATE reviews SET invalidated_at=?,invalidation_reason=?
                           WHERE card_id=? AND invalidated_at IS NULL AND source_kind='study'
                             AND source_ref IN (
                               SELECT 'defense:' || attempt_id FROM defense_attempts
                               WHERE candidate_id=? AND exercise_revision<=?)""", (invalidated_at, reason, card_id, candidate_id, exercise_revision))


def light_interval(database):
    return database.execute("SELECT light_first_interval_days FROM settings WHERE id=1").fetchone()


def invalidated_review_exists(database, card_id):
    return database.execute("SELECT 1 FROM reviews WHERE card_id=? AND invalidated_at IS NOT NULL LIMIT 1", (card_id,)).fetchone()


def candidate(database, candidate_id):
    return database.execute("""SELECT c.*,g.analysis_version AS current_analysis_version
           FROM threat_training_candidates c JOIN imported_games g ON g.id=c.game_id
           WHERE c.id=?""", (candidate_id,)).fetchone()


def dismiss_candidate(database, dismissed_at, updated_at, candidate_id):
    database.execute("""UPDATE threat_training_candidates SET dismissed_at=?,
                  dismissed_evidence_fingerprint=source_fingerprint,updated_at=? WHERE id=?""", (dismissed_at, updated_at, candidate_id))


def clear_card_validation(database, revision, card_id):
    database.execute("UPDATE cards SET pending_validation=0,revision=? WHERE id=?", (revision, card_id))


def retain_approval(database, approved_at, admission_mode, updated_at, candidate_id):
    database.execute("""UPDATE threat_training_candidates SET approved_at=COALESCE(approved_at,?),
               admission_mode=COALESCE(admission_mode,?),paused_at=NULL,
               updated_at=? WHERE id=?""", (approved_at, admission_mode, updated_at, candidate_id))


def ensure_defense_repertoire(database, repertoire_id, name, source_name, created_at):
    database.execute("""INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at)
           VALUES(?,?,?,?)""", (repertoire_id, name, source_name, created_at))


def insert_defense_card(
    database, card_id, repertoire_id, start_fen, due_date, source_ref, source_fen, trained_color, introduced_at,
    revision,
):
    database.execute("""INSERT OR IGNORE INTO cards(
             id,repertoire_id,kind,start_fen,moves_json,state,due_date,
             content_type,scheduling_mode,source_ref,source_fen,trained_color,
             introduced_at,revision)
           VALUES(?,?, 'checkpoint',?,'[]','learning',?,'defense','normal',?,?,?,?,?)""", (card_id, repertoire_id, start_fen, due_date, source_ref, source_fen, trained_color, introduced_at, revision))


def link_approved_card(database, approved_at, card_id, admission_mode, updated_at, candidate_id):
    database.execute("""UPDATE threat_training_candidates SET approved_at=COALESCE(approved_at,?),
              card_id=?,admission_mode=COALESCE(admission_mode,?),paused_at=NULL,
              updated_at=? WHERE id=?""", (approved_at, card_id, admission_mode, updated_at, candidate_id))


def pause_candidate(database, paused_at, updated_at, candidate_id):
    database.execute("UPDATE threat_training_candidates SET paused_at=?,updated_at=? WHERE id=?", (paused_at, updated_at, candidate_id))


def stamp_explicit_admission(database, source, queue_date, card_id):
    database.execute("""UPDATE daily_queue SET admission_kind='explicit',admission_source=?
               WHERE queue_date=? AND card_id=? AND status='queued'""", (source, queue_date, card_id))


def daily_limit(database):
    return database.execute("SELECT defense_new_cards_per_day FROM settings WHERE id=1").fetchone()


def admitted_today(database, queue_date):
    return database.execute("""SELECT COUNT(*) FROM threat_training_candidates admitted
               JOIN cards card ON card.id=admitted.card_id
               WHERE admitted.admission_mode='automatic' AND card.introduced_at=?""", (queue_date,)).fetchone()


def admitted_controls_today(database, queue_date):
    return database.execute("""SELECT COUNT(*) FROM threat_training_candidates admitted
               JOIN cards card ON card.id=admitted.card_id
               WHERE admitted.admission_mode='automatic' AND card.introduced_at=?
                 AND admitted.validation_state='validated_control'""", (queue_date,)).fetchone()


def admission_candidate(database, recent_cutoff, validation_state, offset):
    return database.execute(_ADMISSION_SELECTION_SQL, (recent_cutoff, validation_state, offset)).fetchone()


def admission_reports(database, candidate_id):
    rows = database.execute("""SELECT relation.role,request.request_json,request.report_json
               FROM threat_candidate_requests relation
               JOIN threat_analysis_requests request ON request.id=relation.request_id
               WHERE relation.candidate_id=? AND relation.role IN ('best','historical')""", (candidate_id,))
    return (row for row in rows)


def approved_incident(database, incident_id):
    return database.execute("""SELECT 1 FROM threat_training_candidates
               WHERE incident_id=? AND approved_at IS NOT NULL LIMIT 1""", (incident_id,)).fetchone()


def existing_defense_position(database, prefix, same_prefix):
    return database.execute("""SELECT 1 FROM cards WHERE content_type='defense' AND archived=0
               AND substr(start_fen,1,length(?))=? LIMIT 1""", (prefix, same_prefix)).fetchone()


def completed_reports(database, candidate_id):
    rows = database.execute("""SELECT relation.role,request.request_json,request.report_json FROM threat_candidate_requests relation
           JOIN threat_analysis_requests request ON request.id=relation.request_id
           WHERE relation.candidate_id=? AND request.state='complete'""", (candidate_id,))
    return (row for row in rows)


def lock_card(database, card_id):
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))


def queue_day(database, queue_entry_id):
    return database.execute("SELECT queue_date FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone()


def attempt(database, attempt_id):
    return database.execute("SELECT * FROM defense_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()


def queued_entry(database, queue_entry_id, card_id):
    return database.execute("""SELECT * FROM daily_queue WHERE id=? AND card_id=? AND status='queued'""", (queue_entry_id, card_id)).fetchone()


def complete_queue_entry(database, attempt_state, result_json, queue_entry_id):
    database.execute("""UPDATE daily_queue SET status='complete',attempt_state=?,review_result_json=?
           WHERE id=?""", (attempt_state, result_json, queue_entry_id))


def next_cycle(database, queue_date, card_id):
    return database.execute("SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?", (queue_date, card_id)).fetchone()


def final_position(database, queue_date):
    return database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (queue_date,)).fetchone()


def append_reinforcement(database, queue_date, card_id, cycle, position, attempt_state):
    database.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,
                     card_bucket,admission_kind) VALUES(?,?,?,?,?,'defense','review')""", (queue_date, card_id, cycle, position, attempt_state))


def record_attempt(
    database, attempt_id, candidate_id, card_id, queue_entry_id, exercise_revision, move_uci, grade_json,
    reviewed_at,
):
    database.execute("""INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,
              queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at)
           VALUES(?,?,?,?,?,?,?,?)""", (attempt_id, candidate_id, card_id, queue_entry_id, exercise_revision, move_uci, grade_json, reviewed_at))


def recognition_submission(database, attempt_id):
    return database.execute("""SELECT candidate_id,queue_entry_id,exercise_revision,answer_json
               FROM defense_recognition_submissions WHERE attempt_id=?""", (attempt_id,)).fetchone()


def completed_grade(database, attempt_id):
    return database.execute("SELECT grade_json FROM defense_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()


def source_game_url(database, game_id):
    return database.execute("SELECT game_url FROM imported_games WHERE id=?", (game_id,)).fetchone()


def recognition_correct(database, attempt_id):
    return database.execute("SELECT recognition_correct FROM defense_recognition_submissions WHERE attempt_id=?", (attempt_id,)).fetchone()


def active_queue_entry(database, queue_entry_id, card_id):
    return database.execute("SELECT 1 FROM daily_queue WHERE id=? AND card_id=? AND status='queued'", (queue_entry_id, card_id)).fetchone()


def record_recognition(
    database, attempt_id, candidate_id, queue_entry_id, exercise_revision, answer_json, recognition_correct,
    created_at,
):
    database.execute("""INSERT INTO defense_recognition_submissions(
                 attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,
                 recognition_correct,created_at) VALUES(?,?,?,?,?,?,?)""", (attempt_id, candidate_id, queue_entry_id, exercise_revision, answer_json, recognition_correct, created_at))


def matching_recognition(database, attempt_id, candidate_id, queue_entry_id, exercise_revision):
    return database.execute("""SELECT * FROM defense_recognition_submissions WHERE attempt_id=?
                   AND candidate_id=? AND queue_entry_id=? AND exercise_revision=?""", (attempt_id, candidate_id, queue_entry_id, exercise_revision)).fetchone()


def retain_analysis_request(database, request_id, request_json, created_at, updated_at):
    database.execute("""INSERT OR IGNORE INTO threat_analysis_requests(
                     id,request_json,created_at,updated_at) VALUES(?,?,?,?)""", (request_id, request_json, created_at, updated_at))


def retain_attempt_request(database, candidate_id, request_id):
    database.execute("""INSERT OR IGNORE INTO threat_candidate_requests(candidate_id,request_id,role)
                   VALUES(?,?,'attempt')""", (candidate_id, request_id))
