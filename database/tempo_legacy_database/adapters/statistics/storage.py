"""Fixed feature/statistics projections and daily-job writes on borrowed connections."""
from tempo_legacy_database.adapters.controls.claim_sql import claimable, control_order
from tempo_legacy_database.adapters.games.derivation.features import GAME_FEATURE_UPSERT_SQL, write_feature_values


def game_row(database, game_id: str):
    return database.execute("SELECT * FROM imported_games WHERE id=?", (game_id,)).fetchone()


def analysis_rows(database, game_id: str):
    return database.execute(
            "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply", (game_id,)
        ).fetchall()


def game_event_rows(database, game_id: str):
    return database.execute(
            "SELECT * FROM gameplay_events WHERE game_id=? AND classifier_version=1",
            (game_id,),
        ).fetchall()


def primary_match(database, game_id: str):
    return database.execute(
            "SELECT * FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=? AND is_primary=1",
            (game_id,),
        ).fetchone()


def configured_timezone(database):
    return database.execute("SELECT timezone FROM settings WHERE id=1").fetchone()[0]


def provider_rows(database):
    return database.execute(
            "SELECT provider FROM game_accounts WHERE trim(username)!=''"
        ).fetchall()


def provider_watermark(database, provider: str):
    return database.execute(
                "SELECT last_success_at FROM game_sync_state WHERE provider=?",
                (provider,),
            ).fetchone()


def eligible_daily_games(database, local_day: str):
    return database.execute(
            """SELECT g.id,g.analysis_state,g.adaptive_excluded,f.game_id AS feature_game_id
                 FROM imported_games g LEFT JOIN game_feature_rows f ON f.game_id=g.id
                WHERE substr(g.played_at,1,10)=?""",
            (local_day,),
        ).fetchall()


def store_waiting_snapshot(database, local_day: str, encoded_payload: str, status: str, now: str):
    return database.execute(
                """INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at)
                   VALUES(?,1,?,?,?) ON CONFLICT(local_day) DO UPDATE SET
                   metrics_json=excluded.metrics_json,status=excluded.status,updated_at=excluded.updated_at""",
                (local_day, encoded_payload, status, now),
            )


def daily_feature_rows(database, local_day: str):
    return database.execute(
            "SELECT * FROM game_feature_rows WHERE local_day=?", (local_day,)
        ).fetchall()


def store_complete_snapshot(database, local_day: str, encoded_payload: str, now: str):
    return database.execute(
            """INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at)
               VALUES(?,1,?,'complete',?) ON CONFLICT(local_day) DO UPDATE SET
               metrics_json=excluded.metrics_json,status='complete',updated_at=excluded.updated_at""",
            (local_day, encoded_payload, now),
        )


def insert_outcome_insight(database, insight_id: str, local_day: str, encoded_payload: str, now: str):
    return database.execute(
                """INSERT OR IGNORE INTO daily_chess_insights(
                       id,local_day,kind,evidence_json,status,created_at,updated_at
                   ) VALUES(?,?, 'outcome trend',?,'pending',?,?)""",
                (insight_id, local_day, encoded_payload, now, now),
            )


def insert_tactical_insight(database, insight_id: str, local_day: str, encoded_payload: str, now: str):
    return database.execute(
                """INSERT OR IGNORE INTO daily_chess_insights(
                       id,local_day,kind,evidence_json,status,created_at,updated_at
                   ) VALUES(?,?, 'tactical focus',?,'pending',?,?)""",
                (insight_id, local_day, encoded_payload, now, now),
            )


def enqueue_daily_job(database, local_day: str, now: str):
    return database.execute(
            """INSERT INTO daily_statistics_jobs(local_day,status,last_error,updated_at)
               VALUES(?,'queued',NULL,?) ON CONFLICT(local_day) DO UPDATE SET
               status='queued',last_error=NULL,updated_at=excluded.updated_at""",
            (local_day, now),
        )


def begin_daily_claim(database):
    return database.execute("BEGIN IMMEDIATE")


def pending_daily_job(database):
    return database.execute(
            f"SELECT local_day FROM daily_statistics_jobs WHERE status='queued' AND {claimable('statistics', 'daily_statistics_jobs.local_day')} ORDER BY {control_order('statistics', 'daily_statistics_jobs.local_day')}updated_at LIMIT 1"
        ).fetchone()


def mark_daily_running(database, now: str, local_day: str):
    return database.execute(
            "UPDATE daily_statistics_jobs SET status='running',updated_at=? WHERE local_day=? AND status='queued'",
            (now, local_day),
        ).rowcount


def mark_daily_complete(database, now: str, local_day: str):
    return database.execute(
                    "UPDATE daily_statistics_jobs SET status='complete',last_error=NULL,updated_at=? WHERE local_day=?",
                    (now, local_day),
                )


def mark_daily_failed(database, error: str, now: str, local_day: str):
    return database.execute(
                "UPDATE daily_statistics_jobs SET status='failed',last_error=?,updated_at=? WHERE local_day=?",
                (error, now, local_day),
            )
