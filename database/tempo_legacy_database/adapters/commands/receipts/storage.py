"""Existing receipt SQL, locks, savepoints and driver facts; no new transaction authority."""
from __future__ import annotations

import json
import psycopg
from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout

TRANSIENT_DATABASE_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError, DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout)


def _writer_connection(background: bool):
    if background:
        from tempo_legacy_database.connections import background_connection
        return background_connection()
    from tempo_legacy_database.adapters import postgres_store
    return postgres_store.connection(read_only=False)


def foreground_receipt_reader():
    from tempo_legacy_database.adapters import postgres_store
    return postgres_store.connection(read_only=True)


def receipt_writer(database):
    return database.raw


def insert_queued(raw, operation_id, command_name, request_hash, payload, background):
    raw.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,background) VALUES(%s,%s,%s,'queued',%s,%s) ON CONFLICT(operation_id) DO NOTHING", (operation_id, command_name, request_hash, json.dumps(payload, separators=(',', ':')), background))


def locked_attempt(raw, operation_id):
    return raw.execute('SELECT command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at,attempt_count,cycle_attempt_count,retry_cycle FROM operation_receipts WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()


def save_missing_payload(raw, saved_payload, background, operation_id):
    raw.execute('UPDATE operation_receipts SET payload_json=%s,background=%s WHERE operation_id=%s', (json.dumps(saved_payload, separators=(',', ':')), background, operation_id))


def restart_blocked(raw, operation_id):
    raw.execute("UPDATE operation_receipts SET state='queued',retry_cycle=retry_cycle+1,cycle_attempt_count=0,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=%s", (operation_id,))


def block_exhausted(raw, operation_id):
    raw.execute("UPDATE operation_receipts SET state='blocked',attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=%s", (operation_id,))


def start_attempt(raw, attempt_token, lease_factory, operation_id):
    raw.execute("UPDATE operation_receipts SET state='executing',attempt_count=attempt_count+1,cycle_attempt_count=cycle_attempt_count+1,attempt_token=%s,next_retry_at=NULL,lease_expires_at=%s,updated_at=NOW() WHERE operation_id=%s", (attempt_token, lease_factory(), operation_id))


def blocked_operation(database, operation_id):
    return database.raw.execute("SELECT command_name,payload_json,background,retry_cycle FROM operation_receipts WHERE operation_id=%s AND state='blocked'", (operation_id,)).fetchone()


def locked_retry(database, operation_id, attempt_token):
    return database.raw.execute("SELECT cycle_attempt_count FROM operation_receipts WHERE operation_id=%s AND state='executing' AND attempt_token=%s FOR UPDATE", (operation_id, attempt_token)).fetchone()


def save_retry(database, state, retry_at_factory, error_json_factory, operation_id, attempt_token):
    database.raw.execute("UPDATE operation_receipts SET state=%s,next_retry_at=%s,lease_expires_at=NULL,attempt_token=NULL,last_error_json=%s,updated_at=NOW() WHERE operation_id=%s AND state='executing' AND attempt_token=%s", (state, retry_at_factory(), error_json_factory(), operation_id, attempt_token))


def defer_for_foreground(database, operation_id, attempt_token):
    return bool(database.raw.execute("UPDATE operation_receipts SET state='retrying',attempt_count=GREATEST(0,attempt_count-1),cycle_attempt_count=GREATEST(0,cycle_attempt_count-1),next_retry_at=NOW()+INTERVAL '1 second',lease_expires_at=NULL,attempt_token=NULL,updated_at=NOW() WHERE operation_id=%s AND state='executing' AND attempt_token=%s", (operation_id, attempt_token)).rowcount)


def recoverable_receipt(database, allowed_background_commands, paused_commands):
    admission_clause = ''
    admission_parameters = ()
    if allowed_background_commands is not None:
        admission_clause = ' AND (NOT background OR command_name=ANY(%s))'
        admission_parameters = (sorted(allowed_background_commands),)
    if paused_commands:
        admission_clause += ' AND NOT (command_name=ANY(%s))'
        admission_parameters += (sorted(paused_commands),)
    # Lock at most one indexed due head per state, then choose the oldest head.
    # Terminal history and future leases are not materialized or sorted.
    streams = []
    for state, due_column, deadline in (('retrying', 'next_retry_at', 'NOW()'), ('executing', 'lease_expires_at', 'NOW()'), ('queued', 'updated_at', "NOW()-INTERVAL '30 seconds'")):
        streams.append(f"{state} AS MATERIALIZED (SELECT operation_id,updated_at FROM operation_receipts WHERE state='{state}' AND payload_json IS NOT NULL AND {due_column}<={deadline}" + admission_clause + f' ORDER BY {due_column},updated_at,operation_id LIMIT 1 FOR UPDATE SKIP LOCKED)')
    return database.raw.execute('WITH ' + ','.join(streams) + ' SELECT receipt.operation_id,receipt.command_name,receipt.payload_json,receipt.background FROM operation_receipts receipt JOIN (SELECT * FROM retrying UNION ALL SELECT * FROM executing UNION ALL SELECT * FROM queued) candidate ON candidate.operation_id=receipt.operation_id ORDER BY candidate.updated_at,candidate.operation_id LIMIT 1', admission_parameters * 3).fetchone()


def reserve_recovery(database, operation_id):
    database.raw.execute("UPDATE operation_receipts SET state='queued',next_retry_at=NOW()+INTERVAL '30 seconds',lease_expires_at=NULL,attempt_token=NULL,updated_at=NOW() WHERE operation_id=%s", (operation_id,))


def lock_operation(raw, operation_id):
    raw.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (operation_id,))


def insert_pending(raw, operation_id, command_name, request_hash):
    raw.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) VALUES (%s,%s,%s,'pending') ON CONFLICT(operation_id) DO NOTHING", (operation_id, command_name, request_hash))


def locked_publication(raw, operation_id):
    return raw.execute('SELECT command_name,request_hash,state,response_json,error_json,attempt_token FROM operation_receipts WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()


def begin_handler(raw):
    raw.execute('SAVEPOINT command_handler')


def rollback_handler(raw):
    raw.execute('ROLLBACK TO SAVEPOINT command_handler')


def block_transition(raw, error_json, operation_id):
    raw.execute("UPDATE operation_receipts SET state='blocked',last_error_json=%s,attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s", (error_json, operation_id))


def fail_operation(raw, error_json, operation_id):
    raw.execute("UPDATE operation_receipts SET state='failed',error_json=%s,attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s", (error_json, operation_id))


def release_handler(raw):
    raw.execute('RELEASE SAVEPOINT command_handler')


def keep_transition_pending(raw, operation_id):
    raw.execute("UPDATE operation_receipts SET state='pending',attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=%s", (operation_id,))


def complete_operation(raw, response_json, operation_id):
    raw.execute("UPDATE operation_receipts SET state='complete',response_json=%s,attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s", (response_json, operation_id))


def operation_status(database, operation_id):
    return database.raw.execute('SELECT command_name,request_hash,state,response_json,error_json,attempt_count,next_retry_at,last_error_json,lease_expires_at,payload_json,retry_cycle,cycle_attempt_count FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone()


def transition_progress(database, operation_id):
    return database.execute_native('SELECT state,graph_generation,staging_task_id,graph_task_id,integrity_task_id,integrity_generation,queue_task_id,queue_generation,queue_date,last_error FROM prefix_transition_applications WHERE operation_id=%s', (operation_id,)).fetchone()


def is_transition_in_progress(error):
    return isinstance(error, psycopg.Error) and error.sqlstate == 'P0080'


def primary_error_message(error):
    return error.diag.message_primary


def is_deleted_card_content(error):
    return getattr(getattr(error, 'diag', None), 'constraint_name', None) == 'deleted_card_content'
