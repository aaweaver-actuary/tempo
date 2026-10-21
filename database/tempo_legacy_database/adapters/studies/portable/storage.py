"""Existing seven-table authored Studies bundle storage on the caller transaction."""
from ...postgres_store import PostgresConnection


def study_header(database, study_id):
    return database.execute("SELECT * FROM studies WHERE id=?", (study_id,)).fetchone()


def chapter_rows(database, study_id):
    return database.execute("SELECT * FROM study_chapters WHERE study_id=? ORDER BY position", (study_id,))


def source_rows(database, study_id):
    return database.execute(
        "SELECT source.* FROM study_sources source JOIN study_chapters chapter ON chapter.id=source.chapter_id WHERE chapter.study_id=? ORDER BY source.created_at,source.record_index",
        (study_id,),
    )


def position_rows(database, study_id):
    return database.execute(
        """SELECT position.* FROM study_positions position
           JOIN study_sources source ON source.id=position.source_id
           JOIN study_chapters chapter ON chapter.id=source.chapter_id
           WHERE chapter.study_id=? ORDER BY length(position.node_path),position.node_path""",
        (study_id,),
    )


def exercise_rows(database, study_id):
    return database.execute("SELECT * FROM study_exercises WHERE study_id=? ORDER BY created_at,id", (study_id,))


def revision_rows(database, study_id):
    return database.execute(
        "SELECT revision.* FROM study_exercise_revisions revision JOIN study_exercises exercise ON exercise.id=revision.exercise_id WHERE exercise.study_id=? ORDER BY exercise_id,revision",
        (study_id,),
    )


def link_rows(database, study_id):
    return database.execute("SELECT * FROM study_links WHERE study_id=? ORDER BY id", (study_id,))


def table_columns(database, table_name):
    if isinstance(database, PostgresConnection):
        exported_columns = {column["column_name"] for column in database.execute_native(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s", (table_name,),
        )}
    else:
        exported_columns = {column["name"] for column in database.execute(f"PRAGMA table_info({table_name})")}
    return exported_columns


def study_exists(database, study_id):
    return database.execute("SELECT 1 FROM studies WHERE id=?", (study_id,)).fetchone()


def insert_bundle_row(database, name, row):
    columns = list(row)
    placeholders = ",".join("?" for _ in columns)
    database.execute(f"INSERT INTO {name}({','.join(columns)}) VALUES({placeholders})",
                     tuple(row[column] for column in columns))
