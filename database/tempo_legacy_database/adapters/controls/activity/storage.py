"""Existing finite background progress/control/projection queries; caller owns policy."""

from ... import postgres_store
from ...defensive_analysis import task_admission_sql, recommendation_sql, search_admission_sql


SOURCES = {
    "durable": ("background_tasks", "id"),
    "sync": ("game_sync_jobs", "id"),
    "derivation": ("game_derivation_jobs", "game_id"),
    "integrity": ("repertoire_integrity_jobs", "repertoire_id"),
    "coverage": ("repertoire_coverage_runs", "id"),
    "game_analysis": ("game_analysis_jobs", "game_id"),
    "threat_analysis": ("threat_analysis_requests", "id"),
}


def prepare_progress_read(source):
    generation_columns = {
        "durable": ("generation",), "derivation": ("derivation_version",),
        "integrity": ("run_id",), "coverage": ("id",),
        "game_analysis": ("analysis_version", "analysis_evidence_version"),
    }
    table, id_column = SOURCES[source]
    columns = generation_columns.get(source)

    def read_current(database, work_id):
        return database.execute(
            f"SELECT {','.join(columns)}{',status,lease_id' if source == 'game_analysis' else ''} FROM {table} WHERE {id_column}=?",
            (work_id,),
        ).fetchone()

    return columns, read_current


def prepare_control_read(source, defensive_kinds):
    table, id_column = SOURCES[source]
    state_column = 'state' if source in {'durable', 'threat_analysis'} else 'status'
    row_lock = ' FOR UPDATE' if postgres_store.configured() else ''
    settings_admission = (task_admission_sql('kind', defensive_kinds) if source == 'durable' else
                          search_admission_sql('threat_analysis_requests.id') if source == 'threat_analysis' else '1=1')

    def read_work(database, work_id):
        return database.execute(
            f"SELECT {state_column},({settings_admission}) AS settings_admitted "
            f"FROM {table} WHERE {id_column}=?{row_lock}",
            (work_id,),
        ).fetchone()

    return read_work


def prepare_control_update(action):
    column = 'paused' if action in {'pause', 'resume'} else 'promoted'

    def write_control(database, values):
        return database.execute(
            f"UPDATE background_activity SET {column}=?,updated_at=? WHERE source=? AND work_id=?",
            values,
        )

    return write_control


def upsert_progress(database, values):
    return database.execute(
        """INSERT INTO background_activity(source,work_id,generation_key,phase,
                   completed_units,total_units,updated_at) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(source,work_id) DO UPDATE SET
                   generation_key=excluded.generation_key,phase=excluded.phase,
                   completed_units=excluded.completed_units,total_units=excluded.total_units,
                   updated_at=excluded.updated_at""",
        values,
    )


def unfinished_coverage(database, values):
    return database.execute(
        """SELECT 1 FROM repertoire_coverage_nodes WHERE run_id=?
               AND (explorer_status!='complete' OR maia_status!='complete') LIMIT 1""",
        values,
    ).fetchone()


def ensure_control(database, values):
    return database.execute(
        """INSERT INTO background_activity(source,work_id,updated_at)
           VALUES(?,?,?) ON CONFLICT(source,work_id) DO NOTHING""",
        values,
    )


def release_analysis_lease(database, values):
    return database.execute(
        """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,
               lease_expires_at=NULL,updated_at=? WHERE game_id=? AND status='leased'""",
        values,
    )


def mark_analysis_pending(database, values):
    return database.execute(
        "UPDATE imported_games SET analysis_state='pending' WHERE id=?",
        values,
    )


def release_maia_lease(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_nodes SET maia_status='queued',
               lease_id=NULL,lease_expires_at=NULL,updated_at=?
               WHERE run_id=? AND maia_status='leased'""",
        values,
    )


def release_threat_lease(database, values):
    return database.execute(
        """UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND state='leased'""",
        values,
    )


def durable_activity(database, values, defensive_kinds):
    return database.execute(
        f"SELECT *,NOT {task_admission_sql('kind', defensive_kinds)} AS paused_by_settings "
            "FROM background_tasks WHERE state!='complete' OR updated_at>=?",
        values,
    )


def threat_activity(database, values):
    return database.execute(
        f"""SELECT request.id,state,attempts,last_error,updated_at,
                      {recommendation_sql('request.id')} AS is_recommendation,
                      NOT {search_admission_sql('request.id')} AS paused_by_settings
                FROM threat_analysis_requests request
                WHERE state!='complete' OR updated_at>=?""",
        values,
    )


def sync_activity(database, values):
    return database.execute(
        "SELECT * FROM game_sync_jobs WHERE status!='complete' OR updated_at>=?",
        values,
    )


def derivation_activity(database, values):
    return database.execute(
        """SELECT j.*,g.provider,g.played_at FROM game_derivation_jobs j
                                      JOIN imported_games g ON g.id=j.game_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""",
        values,
    )


def integrity_activity(database, values):
    return database.execute(
        """SELECT j.*,r.name AS repertoire_name FROM repertoire_integrity_jobs j
                                      JOIN repertoires r ON r.id=j.repertoire_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""",
        values,
    )


def coverage_counts(database):
    return database.execute(
        """SELECT run_id,COUNT(*) AS total,
                   SUM(CASE WHEN explorer_status='complete' THEN 1 ELSE 0 END) AS explorer_done,
                   SUM(CASE WHEN maia_status='complete' THEN 1 ELSE 0 END) AS maia_done,
                   SUM(CASE WHEN explorer_status='running' OR maia_status='leased' THEN 1 ELSE 0 END) AS active
                   FROM repertoire_coverage_nodes GROUP BY run_id""",
    )


def coverage_activity(database):
    return database.execute(
        """SELECT runs.*,r.name AS repertoire_name FROM repertoire_coverage_runs runs
                                      JOIN repertoires r ON r.id=runs.repertoire_id""",
    )






def analysis_position_counts(database):
    return database.execute(
        """SELECT game_id,analysis_version,
                      SUM(CASE WHEN state='complete' THEN 1 ELSE 0 END) AS completed,
                      COUNT(*) AS started,
                      MAX(CASE WHEN scan_pass='confirmed' THEN 1 ELSE 0 END) AS confirming
               FROM game_analysis_position_reports GROUP BY game_id,analysis_version""",
    )


def analysis_activity(database, values):
    return database.execute(
        """SELECT j.*,g.provider,g.played_at,
                                             json_array_length(g.moves_json)+1 AS position_total
                                      FROM game_analysis_jobs j
                                      JOIN imported_games g ON g.id=j.game_id
                                      WHERE j.status!='complete' OR j.updated_at>=?""",
        values,
    )


def activity_controls(database):
    return database.execute(
        "SELECT * FROM background_activity",
    )
