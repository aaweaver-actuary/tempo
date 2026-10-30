"""Existing SQLite admission-proof schema and trigger installation."""

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
            issuance_sequence INTEGER, issued_at TEXT,
            PRIMARY KEY(queue_entry_id,card_id,revision)
        )""")
        origin_columns = {column[1] for column in database.execute("PRAGMA table_info(queue_attempt_origins)")}
        if "issued_as_head" not in origin_columns:
            database.execute("ALTER TABLE queue_attempt_origins ADD COLUMN issued_as_head INTEGER NOT NULL DEFAULT 0")
        for column, kind in (("issuance_sequence", "INTEGER"), ("issued_at", "TEXT")):
            if column not in origin_columns:
                database.execute(f"ALTER TABLE queue_attempt_origins ADD COLUMN {column} {kind}")
        database.execute("CREATE UNIQUE INDEX IF NOT EXISTS study_attempt_issuance_order ON queue_attempt_origins(issuance_sequence) WHERE issuance_sequence IS NOT NULL")
        database.execute("CREATE INDEX IF NOT EXISTS study_attempt_issuance_time ON queue_attempt_origins(issued_at,issuance_sequence) WHERE issued_at IS NOT NULL")
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
