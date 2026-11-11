"""Existing finite background progress/control/projection queries; caller owns policy."""

SOURCES = {
    "durable": ("background_tasks", "id"),
    "integrity": ("repertoire_integrity_jobs", "repertoire_id"),
}


def prepare_progress_read(source):
    generation_columns = {
        "durable": ("generation",), "integrity": ("run_id",),
    }
    table, id_column = SOURCES[source]
    columns = generation_columns.get(source)

    def read_current(database, work_id):
        return database.execute(
            f"SELECT {','.join(columns)} FROM {table} WHERE {id_column}=%s",
            (work_id,),
        ).fetchone()

    return columns, read_current


def prepare_control_read(source):
    table, id_column = SOURCES[source]
    state_column = 'state' if source == 'durable' else 'status'
    settings_admission = "(kind='daily_queue' OR (SELECT study_only_mode FROM settings WHERE id=1)=0)" if source == 'durable' else "(SELECT study_only_mode FROM settings WHERE id=1)=0"
    if source == 'durable':
        from tempo_storage.authored_repertoires.pgn_import.preparation import receipt_scope
        settings_admission = f"({settings_admission}) OR ({receipt_scope()})"

    def read_work(database, work_id):
        return database.execute(
            f"SELECT {state_column},({settings_admission}) AS settings_admitted "
            f"FROM {table} WHERE {id_column}=%s FOR UPDATE",
            (work_id,),
        ).fetchone()

    return read_work


def prepare_control_update(action):
    column = 'paused' if action in {'pause', 'resume'} else 'promoted'

    def write_control(database, values):
        return database.execute(
            f"UPDATE background_activity SET {column}=%s,updated_at=%s WHERE source=%s AND work_id=%s",
            values,
        )

    return write_control


def upsert_progress(database, values):
    return database.execute(
        'INSERT INTO background_activity(source,work_id,generation_key,phase,\n                   completed_units,total_units,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s)\n               ON CONFLICT(source,work_id) DO UPDATE SET\n                   generation_key=excluded.generation_key,phase=excluded.phase,\n                   completed_units=excluded.completed_units,total_units=excluded.total_units,\n                   updated_at=excluded.updated_at',
        values,
    )


def ensure_control(database, values):
    return database.execute(
        'INSERT INTO background_activity(source,work_id,updated_at)\n           VALUES(%s,%s,%s) ON CONFLICT(source,work_id) DO NOTHING',
        values,
    )
