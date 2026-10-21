"""Complete fixed session/finding/attempt operations on caller transactions."""
from ...postgres_store import PostgresConnection


def begin_session_write(database):
    database.execute("BEGIN IMMEDIATE")


def game(database, game_id):
    return database.execute(
            "SELECT id,analysis_version FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()


def active_session(database, game_id, analysis_version):
    return database.execute(
            """SELECT id FROM guided_review_sessions
                WHERE game_id=? AND analysis_version=? AND status='active'
                ORDER BY updated_at DESC LIMIT 1""",
            (game_id, analysis_version),
        ).fetchone()


def actionable_findings(database, game_id, analysis_version):
    return database.execute(
            """SELECT * FROM current_game_findings game_findings WHERE game_id=?
                 AND analysis_version=? AND kind!='defensive tactical threat'
                 AND status NOT IN ('ignored','excluded') ORDER BY ply""",
            (game_id, analysis_version),
        ).fetchall()


def create_session_values(database, values):
    database.execute(
            """INSERT INTO guided_review_sessions(
                   id,game_id,analysis_version,finding_ids_json,current_index,status,created_at,updated_at
               ) VALUES(?,?,?,?,0,?,?,?)""",
            values,
        )


def published_findings(database, original_ids):
    return database.execute(
        "SELECT * FROM current_game_findings WHERE id IN ("
        + ",".join("?" for _ in original_ids) + ")",
        tuple(original_ids),
    ).fetchall()


def reconcile_session(database, finding_ids_json, current_index, status, updated_at, session_id):
    database.execute(
            "UPDATE guided_review_sessions SET finding_ids_json=?,current_index=?,status=?,updated_at=? WHERE id=?",
            (finding_ids_json, current_index, status, updated_at, session_id),
        )


def session_for_read(database, session_id):
    return database.execute(
        "SELECT * FROM guided_review_sessions WHERE id=?"
        + (" FOR UPDATE" if isinstance(database, PostgresConnection) else ""), (session_id,),
    ).fetchone()


def session_attempts(database, session_id):
    return database.execute(
        "SELECT finding_id,move_uci,correct,attempted_at FROM guided_review_attempts WHERE session_id=?",
        (session_id,),
    ).fetchall()


def session(database, session_id):
    return database.execute(
            "SELECT * FROM guided_review_sessions WHERE id=?", (session_id,)
        ).fetchone()


def insert_attempt_values(database, values):
    database.execute(
            """INSERT OR IGNORE INTO guided_review_attempts(
                   session_id,finding_id,move_uci,correct,attempted_at
               ) VALUES(?,?,?,?,?)""",
            values,
        )


def advance_session(database, next_index, status, now, session_id):
    database.execute(
            "UPDATE guided_review_sessions SET current_index=?,status=?,updated_at=? WHERE id=?",
            (next_index, status, now, session_id),
        )


def locked_game(database, game_id):
    return database.execute(
        "SELECT id,analysis_version FROM imported_games WHERE id=? FOR UPDATE", (game_id,),
    ).fetchone()


def locked_active_session(database, game_id, analysis_version):
    return database.execute(
        """SELECT * FROM guided_review_sessions
           WHERE game_id=? AND analysis_version=? AND status='active'
           ORDER BY updated_at DESC LIMIT 1 FOR UPDATE""",
        (game_id, analysis_version),
    ).fetchone()


def command_findings(database, game_id, analysis_version):
    return database.execute(
        """SELECT * FROM current_game_findings game_findings WHERE game_id=?
           AND analysis_version=? AND kind!='defensive tactical threat'
           AND status NOT IN ('ignored','excluded') ORDER BY ply""",
        (game_id, analysis_version),
    ).fetchall()


def create_command_session_values(database, values):
    database.execute(
        """INSERT INTO guided_review_sessions(
           id,game_id,analysis_version,finding_ids_json,current_index,status,created_at,updated_at)
           VALUES(?,?,?,?,0,?,?,?)""",
        values,
    )


def locked_session(database, session_id):
    return database.execute(
        "SELECT * FROM guided_review_sessions WHERE id=? FOR UPDATE", (session_id,),
    ).fetchone()


def insert_command_attempt_values(database, values):
    database.execute(
        """INSERT INTO guided_review_attempts(
           session_id,finding_id,move_uci,correct,attempted_at)
           VALUES(?,?,?,?,?) ON CONFLICT(session_id,finding_id) DO NOTHING""",
        values,
    )
