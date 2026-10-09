"""Durable admission proof; daily_queue is a replaceable projection, not a receipt."""


def initialize_sqlite_origins(database) -> None:
    # Install the column and all triggers atomically, including direct callers
    # without an outer transaction. Avoid executescript's implicit commit.
    database.execute("SAVEPOINT queue_attempt_origins_schema")
    try:
        database.execute("""CREATE TABLE IF NOT EXISTS queue_attempt_origins (
            queue_entry_id INTEGER NOT NULL, card_id TEXT NOT NULL, revision INTEGER NOT NULL,
            queue_date TEXT NOT NULL, cycle INTEGER NOT NULL, admission_kind TEXT,
            admission_repertoire_id TEXT, attempt_failed INTEGER NOT NULL DEFAULT 0,
            last_status TEXT NOT NULL, review_result_json TEXT, legacy INTEGER NOT NULL DEFAULT 0,
            start_fen TEXT NOT NULL, moves_json TEXT NOT NULL, trained_color TEXT, content_type TEXT NOT NULL,
            issued_as_head INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(queue_entry_id,card_id,revision)
        )""")
        origin_columns = {column[1] for column in database.execute("PRAGMA table_info(queue_attempt_origins)")}
        if "issued_as_head" not in origin_columns:
            database.execute("ALTER TABLE queue_attempt_origins ADD COLUMN issued_as_head INTEGER NOT NULL DEFAULT 0")
        database.execute("CREATE INDEX IF NOT EXISTS idx_queue_attempt_origin_cycle ON queue_attempt_origins(queue_date,card_id,cycle)")
        # Backfill only surviving projection rows. Missing historical identities
        # cannot be inferred from a client's outbox and must remain explicit conflicts.
        if not database.execute("SELECT 1 FROM internal_migrations WHERE name='queue-attempt-origins-v1'").fetchone():
            database.execute("""INSERT OR IGNORE INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type)
                SELECT q.id,q.card_id,c.revision,q.queue_date,q.cycle,q.admission_kind,
                       q.admission_repertoire_id,q.attempt_failed,q.status,q.review_result_json,1,
                       c.start_fen,c.moves_json,c.trained_color,c.content_type
                FROM daily_queue q JOIN cards c ON c.id=q.card_id""")
            database.execute("INSERT INTO internal_migrations(name,applied_at) VALUES('queue-attempt-origins-v1',datetime('now'))")
        receipt_columns = {column[1] for column in database.execute("PRAGMA table_info(review_attempt_receipts)")}
        if "request_json" not in receipt_columns:
            database.execute("ALTER TABLE review_attempt_receipts ADD COLUMN request_json TEXT")
        for trigger_name, trigger_event, row_reference in (
            ("queue_attempt_origin_insert", "AFTER INSERT", "NEW"),
            ("queue_attempt_origin_update", "AFTER UPDATE OF card_id,attempt_failed,status,review_result_json,admission_kind,admission_repertoire_id", "NEW"),
            ("queue_attempt_origin_delete", "BEFORE DELETE", "OLD"),
        ):
            database.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            database.execute(f"""CREATE TRIGGER {trigger_name}
                {trigger_event} ON daily_queue BEGIN
                INSERT INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type)
                SELECT {row_reference}.id,{row_reference}.card_id,c.revision,
                       {row_reference}.queue_date,{row_reference}.cycle,{row_reference}.admission_kind,
                       {row_reference}.admission_repertoire_id,{row_reference}.attempt_failed,
                       {row_reference}.status,{row_reference}.review_result_json,0,
                       c.start_fen,c.moves_json,c.trained_color,c.content_type
                FROM cards c WHERE c.id={row_reference}.card_id
                ON CONFLICT(queue_entry_id,card_id,revision) DO UPDATE SET
                    attempt_failed=MAX(queue_attempt_origins.attempt_failed,excluded.attempt_failed),
                    admission_kind=COALESCE(queue_attempt_origins.admission_kind,excluded.admission_kind),
                    admission_repertoire_id=COALESCE(queue_attempt_origins.admission_repertoire_id,excluded.admission_repertoire_id),
                    last_status=excluded.last_status,
                    review_result_json=COALESCE(excluded.review_result_json,queue_attempt_origins.review_result_json);
                END""")
        database.execute("DROP TRIGGER IF EXISTS queue_attempt_origin_revision")
        database.execute("""CREATE TRIGGER queue_attempt_origin_revision
            AFTER UPDATE OF revision ON cards WHEN NEW.revision!=OLD.revision BEGIN
            UPDATE daily_queue SET attempt_failed=0
            WHERE card_id=NEW.id AND status!='complete' AND attempt_failed!=0;
            INSERT OR IGNORE INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type)
            SELECT q.id,NEW.id,NEW.revision,q.queue_date,q.cycle,q.admission_kind,
                   q.admission_repertoire_id,q.attempt_failed,q.status,q.review_result_json,0,
                   NEW.start_fen,NEW.moves_json,NEW.trained_color,NEW.content_type
            FROM daily_queue q WHERE q.card_id=NEW.id AND q.status='queued';
            END""")
    except Exception:
        database.execute("ROLLBACK TO queue_attempt_origins_schema")
        raise
    finally:
        database.execute("RELEASE queue_attempt_origins_schema")


def recover_queue_entry(database, card_id: str, request) -> dict:
    from .review_conflicts import ReviewConflict

    if not request.attempt_id or not request.queue_entry_id:
        raise ReviewConflict("queue_attempt_unprovable", "The original queue attempt cannot be verified. The completed result needs review.")
    contexts = database.execute(
        "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=?",
        (request.queue_entry_id, card_id),
    ).fetchall()
    if request.expected_revision is not None:
        contexts = [context for context in contexts if context["revision"] == request.expected_revision]
    if len(contexts) != 1:
        raise ReviewConflict("queue_attempt_unprovable", "The original queue attempt cannot be verified. The completed result needs review.")
    origin = dict(contexts[0])
    current = database.execute("SELECT revision,start_fen,moves_json,trained_color,content_type FROM cards WHERE id=?", (card_id,)).fetchone()
    if not current or any(current[column] != origin[column] for column in ("revision", "start_fen", "moves_json", "trained_color", "content_type")) or (origin["legacy"] and request.expected_revision is None and origin["revision"] != 1):
        raise ReviewConflict("card_revision_changed", "The card changed after this attempt. The completed result needs review.")
    if origin["last_status"] in {"buried", "complete"} and not origin["review_result_json"]:
        raise ReviewConflict("queue_attempt_retired", "This queue attempt was retired without a saved review. The completed result needs review.")
    return {**origin, "id": origin["queue_entry_id"],
            "status": "complete" if origin["review_result_json"] else "queued", "_recovered": True}


def retain_recovered_completion(database, entry: dict, result_json: str) -> None:
    """A removed projection still needs its canonical competing-review context."""
    database.execute(
        "UPDATE queue_attempt_origins SET last_status='complete',review_result_json=? "
        "WHERE queue_entry_id=? AND card_id=? AND revision=?",
        (result_json, entry["id"], entry["card_id"], entry["revision"]),
    )


def validate_failure_marker(database, entry_id: int, card_id=None, expected_revision=None) -> None:
    """A queue-only legacy marker cannot choose between replacement contexts."""
    from .review_conflicts import ReviewConflict

    contexts = database.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=?", (entry_id,)).fetchall()
    current = database.execute("""SELECT c.id,c.revision,c.start_fen,c.moves_json,c.trained_color,c.content_type
        FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=?""", (entry_id,)).fetchone()
    if card_id is not None:
        contexts = [origin for origin in contexts if origin["card_id"] == card_id
                    and (expected_revision is None or origin["revision"] == expected_revision)]
    if not current or len(contexts) != 1 or current["id"] != contexts[0]["card_id"] or any(
        current[column] != contexts[0][column]
        for column in ("revision", "start_fen", "moves_json", "trained_color", "content_type")
    ):
        raise ReviewConflict("queue_attempt_unprovable", "The guided attempt no longer matches this queue entry. Its completed review needs reconciliation.")
