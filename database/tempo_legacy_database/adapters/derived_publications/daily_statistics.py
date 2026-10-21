"""Daily readiness facts and accepted snapshot/insight writes on the caller transaction."""


def provider_names(database):
    return [row["provider"] for row in database.execute(
            "SELECT provider FROM game_accounts WHERE trim(username)!=''",
        )]


def provider_watermark(database, provider: str):
    return database.execute(
                "SELECT last_success_at FROM game_sync_state WHERE provider=?", (provider,),
            ).fetchone()


def daily_game_rows(database, local_day: str):
    return database.execute(
            """SELECT g.id,g.analysis_state,g.adaptive_excluded,f.game_id AS feature_game_id
                 FROM imported_games g LEFT JOIN game_feature_rows f ON f.game_id=g.id
                WHERE substr(g.played_at,1,10)=?""",
            (local_day,),
        ).fetchall()


def daily_feature_rows(database, local_day: str):
    return database.execute(
            "SELECT outcome_score,tactical_opportunities,tactical_found "
            "FROM game_feature_rows WHERE local_day=?", (local_day,),
        ).fetchall()


def store_snapshot(database, local_day: str, encoded_snapshot: str, status: str, now: str):
    return database.execute(
            """INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at)
               VALUES(?,1,?,?,?) ON CONFLICT(local_day) DO UPDATE SET
               metrics_json=excluded.metrics_json,status=excluded.status,
               updated_at=excluded.updated_at""",
            (local_day, encoded_snapshot, status, now),
        )


def insert_insight(database, identifier: str, local_day: str, kind: str, encoded_snapshot: str, now: str):
    return database.execute(
                    """INSERT OR IGNORE INTO daily_chess_insights(
                         id,local_day,kind,evidence_json,status,created_at,updated_at)
                       VALUES(?,?,?,?,'pending',?,?)""",
                    (identifier, local_day, kind, encoded_snapshot, now, now),
                )


def complete_daily_job(database, now: str, local_day: str):
    return database.execute(
            "UPDATE daily_statistics_jobs SET status='complete',last_error=NULL,updated_at=? "
            "WHERE local_day=?", (now, local_day),
        )
