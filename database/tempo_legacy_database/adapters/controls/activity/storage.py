"""Existing finite background progress/control/projection queries; caller owns policy."""

from ... import postgres_store
from ...defensive_analysis import task_admission_sql


SOURCES = {
    "durable": ("background_tasks", "id"),
    "sync": ("game_sync_jobs", "id"),
    "derivation": ("game_derivation_jobs", "game_id"),
    "integrity": ("repertoire_integrity_jobs", "repertoire_id"),
    "coverage": ("repertoire_coverage_runs", "id"),
}


def prepare_progress_read(source):
    generation_columns = {
        "durable": ("generation",), "derivation": ("derivation_version",),
        "integrity": ("run_id",), "coverage": ("id",),
    }
    table, id_column = SOURCES[source]
    columns = generation_columns.get(source)

    def read_current(database, work_id):
        return database.execute(
            f"SELECT {','.join(columns)} FROM {table} WHERE {id_column}=?",
            (work_id,),
        ).fetchone()

    return columns, read_current


def prepare_control_read(source, defensive_kinds):
    table, id_column = SOURCES[source]
    state_column = 'state' if source == 'durable' else 'status'
    row_lock = ' FOR UPDATE' if postgres_store.configured() else ''
    settings_admission = task_admission_sql('kind', defensive_kinds) if source == 'durable' else '1=1'
    native_explicit = source == 'durable' and postgres_store.configured()
    if native_explicit:
        from ...authored_repertoires.pgn_import.preparation import receipt_scope
        settings_admission = f"({settings_admission}) OR ({receipt_scope()})"

    def read_work(database, work_id):
        if native_explicit:
            return database.execute_native(
                f"SELECT {state_column},({settings_admission}) AS settings_admitted "
                f"FROM {table} WHERE {id_column}=%s{row_lock}", (work_id,),
            ).fetchone()
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


def release_maia_lease(database, values):
    return database.execute(
        """UPDATE repertoire_coverage_nodes SET maia_status='queued',
               lease_id=NULL,lease_expires_at=NULL,updated_at=?
               WHERE run_id=? AND maia_status='leased'""",
        values,
    )
