"""Imported-game record queries, without normalization or task decisions."""

GAME_COLUMNS = (
    "provider_game_id", "content_hash", "username", "played_at", "speed",
    "rated", "color", "result", "start_fen", "moves_json", "game_url",
    "opening_name", "player_rating", "opponent_rating", "rating_change",
    "time_control",
)


def lock_import_identity(database, identity):
    database.raw.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:imported-game:{identity}",),
    )


def imported_provider_game(database, provider, provider_game_id):
    return database.execute(
        "SELECT * FROM imported_games WHERE provider=? AND provider_game_id=? FOR UPDATE",
        (provider, provider_game_id),
    ).fetchone()


def imported_legacy_game(database, provider, username, content_hash):
    return database.execute(
        """SELECT * FROM imported_games WHERE provider=?
               AND lower(username)=lower(?) AND content_hash=?
               AND provider_game_id IS NULL FOR UPDATE""",
        (provider, username, content_hash),
    ).fetchone()


def update_imported_game(database, game_id, values):
    database.execute(
        "UPDATE imported_games SET "
        + ",".join(f"{column_name}=?" for column_name in GAME_COLUMNS)
        + " WHERE id=?",
        (*values, game_id),
    )


def insert_imported_game(database, game_id, provider, values):
    database.execute(
        "INSERT INTO imported_games(id,provider," + ",".join(GAME_COLUMNS)
        + ") VALUES(" + ",".join("?" for _ in range(len(GAME_COLUMNS) + 2)) + ")",
        (game_id, provider, *values),
    )


def ensure_analysis_job(database, game_id, now):
    database.execute(
        "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?) "
        "ON CONFLICT(game_id) DO NOTHING",
        (game_id, now),
    )






def lock_sync_progress(database, job_id):
    return database.execute(
        "SELECT status,result_json FROM game_sync_jobs WHERE id=? FOR UPDATE",
        (job_id,),
    ).fetchone()


def save_sync_progress(database, job_id, result_json, now):
    database.execute(
        "UPDATE game_sync_jobs SET status='running',result_json=?,updated_at=? WHERE id=?",
        (result_json, now, job_id),
    )
