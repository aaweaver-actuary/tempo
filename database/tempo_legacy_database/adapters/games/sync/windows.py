"""Existing bounded provider window storage, without fetch/dispatch policy."""


def insert_window(database, identifier, job_id, provider, kind, start_ms, end_ms,
                  source_url, now):
    database.execute(
        """INSERT INTO game_sync_windows(
               id,job_id,provider,window_kind,window_start_ms,window_end_ms,
               source_url,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
        (identifier, job_id, provider, kind, start_ms, end_ms,
         source_url, now, now),
    )


def load_window(database, window_id):
    return database.execute(
        "SELECT * FROM game_sync_windows WHERE id=?",
        (window_id,),
    ).fetchone()


def load_window_request(database, job_id):
    return database.execute(
        "SELECT request_json,status FROM game_sync_jobs WHERE id=?",
        (job_id,),
    ).fetchone()


def lock_window_status(database, window_id):
    return database.execute(
        "SELECT status FROM game_sync_windows WHERE id=? FOR UPDATE",
        (window_id,),
    ).fetchone()


def mark_window_split(database, window_id, now):
    database.execute(
        "UPDATE game_sync_windows SET status='split',updated_at=? WHERE id=?",
        (now, window_id),
    )


def stage_window_records(database, window_id, serialized, digest, total_records,
                         fetched, filtered, rejected, now):
    database.execute(
        """UPDATE game_sync_windows SET status='staged',records_json=?,source_digest=?,
           total_records=?,fetched_count=?,filtered_count=?,rejected_count=?,updated_at=?
           WHERE id=?""",
        (serialized, digest, total_records, fetched, filtered, rejected, now, window_id),
    )


def mark_window_complete(database, window_id):
    database.execute(
        "UPDATE game_sync_windows SET status='complete' WHERE id=?", (window_id,),
    )


def lock_window(database, window_id):
    return database.execute(
        "SELECT * FROM game_sync_windows WHERE id=? FOR UPDATE",
        (window_id,),
    ).fetchone()


def advance_record_cursor(database, window_id, next_index, now):
    database.execute(
        "UPDATE game_sync_windows SET next_record_index=?,updated_at=? WHERE id=?",
        (next_index, now, window_id),
    )
