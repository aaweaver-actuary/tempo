"""Add study-owned cards without changing existing scheduling identities."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import database


STUDY_TABLES = (
    """CREATE TABLE IF NOT EXISTS studies (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        source_json TEXT NOT NULL DEFAULT '{}', archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS study_chapters (
        id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
        title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0, UNIQUE(study_id, position)
    )""",
    """CREATE TABLE IF NOT EXISTS study_sources (
        id TEXT PRIMARY KEY, chapter_id TEXT NOT NULL REFERENCES study_chapters(id),
        source_group_id TEXT NOT NULL, version INTEGER NOT NULL,
        raw_pgn TEXT NOT NULL, sha256 TEXT NOT NULL, filename TEXT NOT NULL,
        record_index INTEGER NOT NULL, headers_json TEXT NOT NULL,
        diagnostics_json TEXT NOT NULL, valid INTEGER NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(chapter_id, source_group_id, version, record_index)
    )""",
    """CREATE TABLE IF NOT EXISTS study_positions (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES study_sources(id),
        parent_id TEXT REFERENCES study_positions(id), child_index INTEGER NOT NULL,
        move_uci TEXT, fen TEXT NOT NULL, history_json TEXT NOT NULL,
        comment TEXT NOT NULL DEFAULT '', starting_comment TEXT NOT NULL DEFAULT '',
        nags_json TEXT NOT NULL DEFAULT '[]', arrows_json TEXT NOT NULL DEFAULT '[]',
        squares_json TEXT NOT NULL DEFAULT '[]', node_path TEXT NOT NULL,
        valid INTEGER NOT NULL DEFAULT 1, UNIQUE(source_id, node_path)
    )""",
    """CREATE TABLE IF NOT EXISTS study_exercises (
        id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
        position_id TEXT NOT NULL REFERENCES study_positions(id),
        current_revision INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','archived')),
        sibling_group TEXT, source_json TEXT NOT NULL DEFAULT '{}',
        point_value REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS study_exercise_revisions (
        exercise_id TEXT NOT NULL REFERENCES study_exercises(id),
        revision INTEGER NOT NULL, specification_json TEXT NOT NULL,
        specification_hash TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(exercise_id, revision)
    )""",
    """CREATE TABLE IF NOT EXISTS study_links (
        id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
        source_position_id TEXT NOT NULL REFERENCES study_positions(id),
        target_position_id TEXT REFERENCES study_positions(id),
        target_exercise_id TEXT REFERENCES study_exercises(id),
        relation TEXT NOT NULL CHECK(relation IN ('illustrates','contrasts','follow_up')),
        CHECK((target_position_id IS NULL) != (target_exercise_id IS NULL))
    )""",
    """CREATE TABLE IF NOT EXISTS study_attempts (
        id TEXT PRIMARY KEY, exercise_id TEXT NOT NULL REFERENCES study_exercises(id),
        revision INTEGER NOT NULL, card_id TEXT REFERENCES cards(id),
        queue_entry_id INTEGER REFERENCES daily_queue(id), cycle INTEGER,
        context TEXT NOT NULL CHECK(context IN ('practice','review')),
        answer_json TEXT NOT NULL, answer_hash TEXT NOT NULL,
        assessment_json TEXT NOT NULL, assessment_method TEXT NOT NULL,
        grader_version INTEGER NOT NULL, hint_seen INTEGER NOT NULL DEFAULT 0,
        solution_seen_before_answer INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL, committed_at TEXT NOT NULL, finalized_at TEXT,
        result_json TEXT, UNIQUE(queue_entry_id)
    )""",
    """CREATE TABLE IF NOT EXISTS study_sibling_burials (
        exercise_id TEXT NOT NULL REFERENCES study_exercises(id),
        study_day TEXT NOT NULL, reason TEXT NOT NULL,
        PRIMARY KEY(exercise_id, study_day)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_study_chapters_study ON study_chapters(study_id,position)",
    "CREATE INDEX IF NOT EXISTS idx_study_sources_chapter ON study_sources(chapter_id,source_group_id,version)",
    "CREATE INDEX IF NOT EXISTS idx_study_positions_parent ON study_positions(parent_id,child_index)",
    "CREATE INDEX IF NOT EXISTS idx_study_exercises_position ON study_exercises(position_id,status)",
    "CREATE INDEX IF NOT EXISTS idx_study_attempts_exercise ON study_attempts(exercise_id,committed_at)",
)


def _card_column_definition(column: sqlite3.Row) -> str:
    name = column["name"]
    if name == "id":
        return "id TEXT PRIMARY KEY"
    if name == "repertoire_id":
        return "repertoire_id TEXT REFERENCES repertoires(id) ON DELETE CASCADE"
    if name == "kind":
        return "kind TEXT NOT NULL CHECK(kind IN ('prefix','response','checkpoint','exercise'))"
    if name == "state":
        return "state TEXT NOT NULL DEFAULT 'new' CHECK(state IN ('locked','new','learning','mature'))"
    if name == "unlock_after_card_id":
        return "unlock_after_card_id TEXT REFERENCES cards(id) ON DELETE SET NULL"
    definition = f'{name} {column["type"] or "TEXT"}'
    if column["notnull"]:
        definition += " NOT NULL"
    if column["dflt_value"] is not None:
        definition += f' DEFAULT {column["dflt_value"]}'
    return definition


def migrate_studies() -> None:
    """Back up once, then rebuild cards atomically with all dependent IDs intact."""
    path = database.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("SELECT 1 FROM internal_migrations WHERE name='studies-v1'").fetchone():
            return
        backup_path = path.with_name(path.name + ".before-studies-v1.bak")
        if not backup_path.exists():
            with sqlite3.connect(backup_path) as backup:
                connection.backup(backup)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in STUDY_TABLES:
                connection.execute(statement)
            columns = connection.execute("PRAGMA table_info(cards)").fetchall()
            old_indexes = [row["sql"] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='cards' AND sql IS NOT NULL"
            )]
            old_triggers = [row["sql"] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='cards' AND sql IS NOT NULL"
            )]
            queue_origin_triggers = connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN "
                "('queue_attempt_origin_insert','queue_attempt_origin_update','queue_attempt_origin_delete')"
            ).fetchall()
            card_columns = ",".join(_card_column_definition(column) for column in columns)
            connection.execute(f"""CREATE TABLE cards_study_migration (
                {card_columns},
                study_exercise_id TEXT REFERENCES study_exercises(id),
                CHECK((study_exercise_id IS NULL AND repertoire_id IS NOT NULL
                       AND content_type!='study_exercise' AND kind!='exercise')
                   OR (study_exercise_id IS NOT NULL AND repertoire_id IS NULL
                       AND content_type='study_exercise' AND kind='exercise'))
            )""")
            names = ",".join(column["name"] for column in columns)
            connection.execute(f"INSERT INTO cards_study_migration({names}) SELECT {names} FROM cards")
            # SQLite validates triggers on other tables during RENAME. Detach
            # these card references only inside the atomic rebuild; rollback
            # restores them and the admission ledger stays intact throughout.
            for trigger in queue_origin_triggers:
                connection.execute(f'DROP TRIGGER "{trigger["name"]}"')
            connection.execute("DROP TABLE cards")
            connection.execute("ALTER TABLE cards_study_migration RENAME TO cards")
            for statement in old_indexes + old_triggers + [trigger["sql"] for trigger in queue_origin_triggers]:
                connection.execute(statement)
            connection.execute("""CREATE UNIQUE INDEX idx_study_active_card
                ON cards(study_exercise_id) WHERE study_exercise_id IS NOT NULL AND archived=0""")
            existing_settings = {row["name"] for row in connection.execute("PRAGMA table_info(settings)")}
            if "study_new_per_day" not in existing_settings:
                connection.execute("ALTER TABLE settings ADD COLUMN study_new_per_day INTEGER NOT NULL DEFAULT 2")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"Study migration foreign-key violations: {violations[:3]}")
            connection.execute(
                "INSERT INTO internal_migrations(name,applied_at) VALUES('studies-v1',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
