"""Bounded receipt operations on a caller-owned Study transaction.

The composition owner fences the issuance, card/queue, source and owner before
calling these functions. This adapter never opens or commits a connection.
"""
from __future__ import annotations


def issued_record(database, attempt_id):
    return database.execute_native(
        "SELECT * FROM study_position_issued_plans WHERE attempt_id=%s", (attempt_id,)
    ).fetchone()


def receipt(database, event_id, attempt_id, decision_index):
    return database.execute_native(
        "SELECT * FROM study_position_first_responses WHERE event_id=%s "
        "OR (attempt_id=%s AND decision_index=%s) FOR UPDATE",
        (event_id, attempt_id, decision_index),
    ).fetchall()


def insert(database, envelope, digest, payload_json, accepted_at):
    return database.execute_native(
        "INSERT INTO study_position_first_responses "
        "(event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,"
        "payload_digest,payload_json,observed_at,accepted_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING event_id",
        (envelope['event_id'], envelope['attempt_id'], envelope['decision_index'],
         envelope['repertoire_id'], envelope['variant'], envelope['position'],
         envelope['owner_epoch'], digest, payload_json, envelope['observed_at'], accepted_at),
    ).fetchone()


def has_position_tables(database):
    row = database.execute_native("SELECT to_regclass('public.study_position_issued_plans') IS NOT NULL", ()).fetchone()
    return bool(row and row[0])


def lock_original_queue(database, plan):
    """Card identity is already locked; lock its original day and queue next."""
    day = database.execute_native(
        "SELECT queue_date FROM daily_queue_days WHERE queue_date=%s FOR UPDATE SKIP LOCKED",
        (plan.local_day.isoformat(),),
    ).fetchone()
    queue = database.execute_native(
        "SELECT card_id,queue_date FROM daily_queue WHERE id=%s FOR UPDATE SKIP LOCKED",
        (plan.queue_entry_id,),
    ).fetchone()
    return bool(day and queue and queue['card_id'] == plan.original_card_id
                and str(queue['queue_date']) == plan.local_day.isoformat())


def lock_attempt(database, attempt_id):
    row = database.execute_native(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:position-attempt:" + attempt_id,),
    ).fetchone()
    return bool(row and row[0])


def acceptance_clock(database):
    row = database.execute_native("SELECT statement_timestamp(),timezone FROM settings WHERE id=1", ()).fetchone()
    if not row:
        raise ValueError("study_timezone_unavailable")
    return row[0], row[1]
