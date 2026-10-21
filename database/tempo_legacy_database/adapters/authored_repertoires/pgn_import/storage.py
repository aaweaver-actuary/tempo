"""Existing PostgreSQL import statements, batch cursor and receipt locks."""
from ...postgres_store import PostgresConnection


def lock_main_repertoire(database):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )


def matching_repertoire(database, source_name, trained_color):
    return database.execute_native(
        "SELECT repertoire.id FROM repertoires repertoire "
        "WHERE repertoire.source_name=%s "
        "AND repertoire.id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') "
        "AND EXISTS(SELECT 1 FROM repertoire_lines line "
        "WHERE line.repertoire_id=repertoire.id AND line.trained_color=%s) "
        "ORDER BY repertoire.created_at DESC LIMIT 1",
        (source_name, trained_color),
    ).fetchone()


def clear_main_repertoire(database):
    database.execute_native(
        "UPDATE repertoires SET is_main=0 "
        "WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')"
    )


def upsert_main_repertoire(database, repertoire_id, name, source_name, now):
    database.execute_native(
        "INSERT INTO repertoires(id,name,source_name,created_at,is_main) "
        "VALUES(%s,%s,%s,%s,1) ON CONFLICT(id) DO UPDATE SET "
        "is_main=1,source_name=excluded.source_name",
        (repertoire_id, name, source_name, now),
    )


def existing_lines(database, repertoire_id):
    return database.execute_native(
        "SELECT id,start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=%s",
        (repertoire_id,),
    )


def insert_import_rows(database, lines_to_insert, depths_to_upsert, annotations_to_upsert):
    with database.raw.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id",
            lines_to_insert, returning=True,
        )
        inserted_line_ids = set()
        if lines_to_insert:
            while True:
                inserted_line_ids.update(row[0] for row in cursor.fetchall())
                if not cursor.nextset():
                    break
        cursor.executemany(
            "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) "
            "VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET "
            "learner_decision_count=excluded.learner_decision_count",
            depths_to_upsert,
        )
        cursor.executemany(
            "INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET "
            "comment=excluded.comment,arrows_json=excluded.arrows_json,"
            "squares_json=excluded.squares_json,updated_at=excluded.updated_at",
            annotations_to_upsert,
        )
    return inserted_line_ids


def existing_card_ids(database, segment_ids):
    return database.execute_native(
        "SELECT id FROM cards WHERE id=ANY(%s::text[])", (segment_ids,),
    )


def deleted_card_ids(database, segment_ids):
    return database.execute_native(
        "SELECT card_id FROM deleted_cards WHERE card_id=ANY(%s::text[])", (segment_ids,),
    )


def lock_discard_operation(database, operation_id):
    database.raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))


def insert_discard_receipt(database, operation_id, request_hash, discarded_error_json):
    database.raw.execute(
        "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,error_json) "
        "VALUES(%s,'imports.pgn.admit',%s,'failed',%s) ON CONFLICT(operation_id) DO NOTHING",
        (operation_id, request_hash, discarded_error_json),
    )


def discard_receipt(database, operation_id):
    return database.raw.execute(
        "SELECT command_name,state,response_json FROM operation_receipts WHERE operation_id=%s FOR UPDATE",
        (operation_id,),
    ).fetchone()


def mark_discarded(database, discarded_error_json, operation_id):
    database.raw.execute(
        "UPDATE operation_receipts SET state='failed',payload_json=NULL,response_json=NULL,"
        "error_json=%s,last_error_json=NULL,next_retry_at=NULL,lease_expires_at=NULL,"
        "attempt_token=NULL,updated_at=NOW() WHERE operation_id=%s",
        (discarded_error_json, operation_id),
    )
