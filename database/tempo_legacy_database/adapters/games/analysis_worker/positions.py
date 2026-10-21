"""Fixed legacy analysis position/report queries on the caller's connection."""

from ... import postgres_store


def evaluation_read_section(background_reader, reader):
    return background_reader if postgres_store.configured() else reader


def release_parent(database, state, error, now, game_id, parent_lease_id):
    database.execute(
        """UPDATE game_analysis_jobs SET status=?,lease_id=NULL,lease_expires_at=NULL,
                   last_error=?,updated_at=? WHERE game_id=? AND status='leased' AND lease_id=?""",
        (state, error, now, game_id, parent_lease_id),
    )


def set_game_analysis_state(database, state, game_id):
    database.execute("UPDATE imported_games SET analysis_state=? WHERE id=?",
                         (state, game_id))


def completed_position_reports(database, game_id, analysis_version):
    return database.execute(
        """SELECT scan_pass,position_index,report_json FROM game_analysis_position_reports
               WHERE game_id=? AND analysis_version=? AND state='complete'""",
        (game_id, analysis_version),
    ).fetchall()


def insert_terminal_report(database, values):
    database.execute(
        """INSERT OR IGNORE INTO game_analysis_position_reports
                   (id,game_id,analysis_version,scan_pass,position_index,request_json,report_json,state,updated_at)
                   VALUES(?,?,?,?,?,?,?,'complete',?)""",
        values,
    )


def position_lease_state(database, game_id, analysis_version, scan_pass, position_index):
    return database.execute(
        """SELECT id,state,attempts FROM game_analysis_position_reports
               WHERE game_id=? AND analysis_version=? AND scan_pass=? AND position_index=?""",
        (game_id, analysis_version, scan_pass, position_index),
    ).fetchone()


def lease_existing_report(database, lease_id, parent_lease_id, expires_at, request_json, now, report_id):
    database.execute(
        """UPDATE game_analysis_position_reports SET state='leased',lease_id=?,parent_lease_id=?,
                   lease_expires_at=?,request_json=?,attempts=attempts+1,updated_at=? WHERE id=?""",
        (lease_id, parent_lease_id, expires_at, request_json, now, report_id),
    )


def insert_leased_report(database, values):
    database.execute(
        """INSERT INTO game_analysis_position_reports
                   (id,game_id,analysis_version,scan_pass,position_index,request_json,state,
                    lease_id,parent_lease_id,lease_expires_at,attempts,updated_at)
                   VALUES(?,?,?,?,?,?,'leased',?,?,?,1,?)""",
        values,
    )


def position_report(database, report_id):
    return database.execute("SELECT * FROM game_analysis_position_reports WHERE id=?", (report_id,)).fetchone()


def complete_leased_report(database, report_json, now, report_id, lease_id, request_json):
    return database.execute(
        """UPDATE game_analysis_position_reports SET state='complete',report_json=?,lease_id=NULL,
                   parent_lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=?
                   WHERE id=? AND state='leased' AND lease_id=? AND request_json=?""",
        (report_json, now, report_id, lease_id, request_json),
    ).rowcount


def published_report(database, report_id):
    return database.execute("SELECT state,report_json FROM game_analysis_position_reports WHERE id=?", (report_id,)).fetchone()


def leased_position_release(database, report_id, lease_id):
    return database.execute(
        "SELECT game_id,parent_lease_id,attempts FROM game_analysis_position_reports WHERE id=? AND state='leased' AND lease_id=?",
        (report_id, lease_id),
    ).fetchone()


def release_report(database, state, error, now, report_id):
    database.execute(
        """UPDATE game_analysis_position_reports SET state=?,lease_id=NULL,parent_lease_id=NULL,
                   lease_expires_at=NULL,last_error=?,updated_at=? WHERE id=?""",
        (state, error, now, report_id),
    )


def record_position_error(database, report_id, game_id, error, now):
    database.execute(
        "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) VALUES(?,?,?,?)",
        (report_id, game_id, error, now),
    )
