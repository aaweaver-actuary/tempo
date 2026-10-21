"""Bounded next-opponent source projections and fenced account/snapshot storage."""

_GAME_FIELDS = ("id,provider,username,played_at,speed,rated,adaptive_excluded,"
                "player_rating,opponent_rating,rating_change,time_control")


def configured_account(database):
    return database.execute("SELECT lichess_username FROM settings WHERE id=1").fetchone()


def ensure_account(database, account: str):
    return database.execute("INSERT INTO next_opponent_accounts(account) VALUES(?) ON CONFLICT DO NOTHING", (account,))


def locked_refresh_state(database, now, account: str):
    return database.execute(
        "SELECT input_generation,published_generation,published_method,"
        "COALESCE(next_evidence_at<=?,FALSE) AS future_evidence_due "
        "FROM next_opponent_accounts WHERE account=? FOR UPDATE", (now, account),
    ).fetchone()


def existing_refresh_task(database, task_kind: str, account: str):
    return database.execute(
        "SELECT state,payload_json FROM background_tasks WHERE kind=? AND deduplication_key=?",
        (task_kind, account),
    ).fetchone()


def account_input_generation(database, account: str):
    return database.execute("SELECT input_generation FROM next_opponent_accounts WHERE account=?", (account,)).fetchone()


def sample_games(database, account: str, as_of, sample_limit: int):
    return database.execute_native(
                f"SELECT {_GAME_FIELDS} FROM imported_games WHERE provider='lichess' AND rated=1 "
                "AND adaptive_excluded=0 AND lower(trim(username))=%s "
                "AND next_opponent_game_time(played_at)<=%s "
                "ORDER BY next_opponent_game_time(played_at) DESC,id DESC LIMIT %s",
                (account, as_of, sample_limit),
            ).fetchall()


def latest_speed_rating(database, account: str, speed: str, as_of):
    return database.execute_native(
                    f"SELECT {_GAME_FIELDS} FROM imported_games WHERE provider='lichess' AND rated=1 "
                    "AND adaptive_excluded=0 AND player_rating>0 AND lower(trim(username))=%s "
                    "AND speed=%s AND next_opponent_game_time(played_at)<=%s "
                    "ORDER BY next_opponent_game_time(played_at) DESC,id DESC LIMIT 1",
                    (account, speed, as_of),
                ).fetchone()


def next_evidence(database, account: str, as_of):
    return database.execute_native(
                # PostgreSQL's timestamp range exceeds Python's datetime range.
                "SELECT CAST(next_opponent_game_time(played_at) AS TEXT) AS next_evidence_at FROM imported_games "
                "WHERE provider='lichess' AND rated=1 AND adaptive_excluded=0 AND lower(trim(username))=%s "
                "AND next_opponent_game_time(played_at)>%s "
                "ORDER BY next_opponent_game_time(played_at) ASC,id ASC LIMIT 1",
                (account, as_of),
            ).fetchone()


def locked_configured_account(database):
    return database.execute("SELECT lichess_username FROM settings WHERE id=1 FOR SHARE").fetchone()


def locked_account_input_generation(database, account: str):
    return database.execute("SELECT input_generation FROM next_opponent_accounts WHERE account=? FOR UPDATE",
                             (account,)).fetchone()


def store_profile_snapshot(database, version: str, account: str, method_version: str, encoded_profile: str, published_at: str):
    return database.execute(
        "INSERT INTO next_opponent_snapshots(version,account,method_version,profile_json,published_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(version) DO NOTHING",
        (version, account, method_version, encoded_profile, published_at),
    )


def mark_profile_published(database, input_generation: int, method_version: str, version: str, next_evidence_at: str | None, account: str):
    return database.execute(
        "UPDATE next_opponent_accounts SET published_generation=?,published_method=?,profile_version=?,"
        "next_evidence_at=? WHERE account=?",
        (input_generation, method_version, version, next_evidence_at, account),
    )


def published_profile_row(database, now, account: str):
    return database.execute(
        "SELECT a.input_generation,a.published_generation,a.published_method,"
        "COALESCE(a.next_evidence_at<=?,FALSE) AS future_evidence_due,s.profile_json,s.published_at "
        "FROM next_opponent_accounts a LEFT JOIN next_opponent_snapshots s ON s.version=a.profile_version "
        "WHERE a.account=?", (now, account),
    ).fetchone()


def account_sync_state(database):
    return database.execute("SELECT username,status,last_success_at FROM game_sync_state WHERE provider='lichess'").fetchone()


def profile_task_state(database, task_kind: str, account: str):
    return database.execute("SELECT state FROM background_tasks WHERE kind=? AND deduplication_key=?", (task_kind, account)).fetchone()
