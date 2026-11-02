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
