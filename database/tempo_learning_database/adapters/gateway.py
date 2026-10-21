"""Existing command-receipt SQL and savepoint statements; caller owns lifecycle."""
import psycopg


def insert_operation_intent(database, operation_id, name, digest, payload_json):
    database.execute_native(
        "INSERT INTO operation_receipts(operation_id,command_name,request_hash,payload_json) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (operation_id, name, digest, payload_json),
    )


def intent_identity(database, operation_id):
    return database.execute_native(
        "SELECT command_name,request_hash FROM operation_receipts WHERE operation_id=%s",
        (operation_id,),
    ).fetchone()


def lock_operation(database, operation_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (operation_id,)
    )


def operation_for_update(database, operation_id):
    return database.execute_native(
        "SELECT * FROM operation_receipts WHERE operation_id=%s FOR UPDATE",
        (operation_id,),
    ).fetchone()


def begin_command_handler(database):
    database.execute_native("SAVEPOINT command_handler")


def bind_logical_request(database, name, attempt_id, digest):
    database.execute_native(
        "INSERT INTO logical_request_bindings VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
        (name, attempt_id, digest),
    )


def logical_request_identity(database, name, attempt_id):
    return database.execute_native(
        "SELECT request_hash FROM logical_request_bindings WHERE family=%s AND attempt_id=%s FOR UPDATE",
        (name, attempt_id),
    ).fetchone()


def rollback_command_handler(database):
    database.execute_native("ROLLBACK TO SAVEPOINT command_handler")


def fail_operation(database, error_json, operation_id):
    database.execute_native(
        "UPDATE operation_receipts SET state='failed',error_json=%s,updated_at=NOW() WHERE operation_id=%s",
        (error_json, operation_id),
    )


def release_command_handler(database):
    database.execute_native("RELEASE SAVEPOINT command_handler")


def complete_operation(database, response_json, operation_id):
    database.execute_native(
        "UPDATE operation_receipts SET state='complete',response_json=%s,updated_at=NOW() WHERE operation_id=%s",
        (response_json, operation_id),
    )


def read_operation(database, operation_id):
    return database.execute_native(
        "SELECT * FROM operation_receipts WHERE operation_id=%s", (operation_id,)
    ).fetchone()
