"""Legacy local game/state queries, borrowing the caller's transaction."""


def imported_provider_game(database, provider, provider_game_id):
    return database.execute(
        "SELECT * FROM imported_games WHERE provider=? AND provider_game_id=?",
        (provider, provider_game_id),
    ).fetchone()


def imported_legacy_game(database, provider, username, content_hash):
    return database.execute(
        "SELECT * FROM imported_games WHERE provider=? AND lower(username)=lower(?) AND content_hash=? AND provider_game_id IS NULL",
        (provider, username, content_hash),
    ).fetchone()


def update_imported_game(database, game_id, values):
    database.execute(
        """UPDATE imported_games SET provider_game_id=?,content_hash=?,username=?,played_at=?,speed=?,rated=?,color=?,result=?,start_fen=?,moves_json=?,game_url=?,opening_name=?,player_rating=?,opponent_rating=?,rating_change=?,time_control=? WHERE id=?""",
        (*values, game_id),
    )


def ensure_analysis_job(database, game_id, now):
    database.execute(
        "INSERT OR IGNORE INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
        (game_id, now),
    )


def insert_imported_game(database, game_id, provider, values):
    database.execute(
        """INSERT INTO imported_games(id,provider,provider_game_id,content_hash,username,played_at,speed,rated,color,result,start_fen,moves_json,game_url,opening_name,player_rating,opponent_rating,rating_change,time_control)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (game_id, provider, *values),
    )


def insert_analysis_job(database, game_id, now):
    database.execute(
        "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
        (game_id, now),
    )


def newest_provider_game_time(database, provider, username):
    return database.execute(
        "SELECT MAX(played_at) FROM imported_games WHERE provider=? AND lower(username)=lower(?)",
        (provider, username),
    ).fetchone()[0]


def provider_retry_state(database, provider):
    return database.execute(
        "SELECT username,retry_after FROM game_sync_state WHERE provider=?", (provider,)
    ).fetchone()


def reset_provider_cursor(database, provider):
    database.execute(
        "UPDATE game_sync_state SET cursor=NULL,last_success_at=NULL,retry_after=NULL WHERE provider=?",
        (provider,),
    )


def mark_provider_syncing(database, provider, username, started_at):
    database.execute(
        """INSERT INTO game_sync_state(provider,username,status,last_started_at,last_error)
               VALUES(?,?,'syncing',?,NULL)
               ON CONFLICT(provider) DO UPDATE SET username=excluded.username,status='syncing',last_started_at=excluded.last_started_at,last_error=NULL""",
        (provider, username, started_at),
    )


def save_provider_success(database, provider, cursor, completed_at, result_json):
    database.execute(
        """UPDATE game_sync_state SET status='idle',cursor=?,last_success_at=?,last_error=NULL,retry_after=NULL,last_result_json=? WHERE provider=?""",
        (cursor, completed_at, result_json, provider),
    )


def save_provider_failure(database, provider, error, retry_after, result_json):
    database.execute(
        "UPDATE game_sync_state SET status='error',last_error=?,retry_after=?,last_result_json=? WHERE provider=?",
        (error, retry_after, result_json, provider),
    )
