"""Existing bounded repertoire handoff reads and derivation completion write."""


def game_color(database, game_id):
    return database.execute(
        "SELECT color FROM imported_games WHERE id=?", (game_id,),
    ).fetchone()


def next_repertoire(database, color, after_repertoire_id):
    return database.execute(
        "SELECT DISTINCT repertoire_id FROM repertoire_lines "
        "WHERE trained_color=? AND repertoire_id>? "
        "ORDER BY repertoire_id LIMIT 1",
        (color, after_repertoire_id),
    ).fetchone()


def complete_derivation(database, game_id, version, now):
    database.execute(
        "UPDATE game_derivation_jobs SET status='complete',phase=NULL,"
        "completed_phases=7,last_error=NULL,next_attempt_at=NULL,updated_at=? "
        "WHERE game_id=? AND derivation_version=?",
        (now, game_id, version),
    )
