"""Exact legacy queue-origin abandonment identities and receipt lookup.

The caller owns the transaction and all locks. Missing optional schema is safe
for pre-migration readers: it cannot contain an abandonment receipt.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date, datetime, timezone
_ORIGIN_FIELDS = ('queue_entry_id', 'card_id', 'revision', 'queue_date', 'cycle', 'admission_kind', 'admission_repertoire_id', 'attempt_failed', 'last_status', 'review_result_json', 'legacy', 'start_fen', 'moves_json', 'trained_color', 'content_type', 'issued_as_head', 'issued_at', 'issuance_sequence')

def _digest(value):

    def encode(item):
        if isinstance(item, datetime):
            if item.utcoffset() is None:
                raise ValueError('origin_timestamp_not_utc_aware')
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, date):
            return item.isoformat()
        raise TypeError('unsupported origin value')
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=encode)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def origin_fingerprint(origin):
    return _digest({field: origin[field] for field in _ORIGIN_FIELDS})

def validate_request(payload, *, plan=False):
    expected = {'queue_entry_id', 'card_id', 'revision', 'operation_id', 'actor', 'reason'}
    if not plan:
        expected.add('expected_origin_sha256')
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError('abandonment_request_invalid')
    if any((type(payload[key]) is not int or payload[key] < 1 for key in ('queue_entry_id', 'revision'))):
        raise ValueError('abandonment_identity_invalid')
    if any((not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 500 for key in ('card_id', 'operation_id', 'actor', 'reason'))):
        raise ValueError('abandonment_request_invalid')
    if not plan and (not isinstance(payload['expected_origin_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', payload['expected_origin_sha256'])):
        raise ValueError('abandonment_fingerprint_invalid')
    return dict(payload)

def request_fingerprint(payload):
    return _digest(validate_request(payload))

def installed(database):
    row = database.execute_native("SELECT to_regclass('public.queue_attempt_abandonments') IS NOT NULL").fetchone()
    return bool(row and row[0])

def is_abandoned(database, entry_id, card_id, revision):
    if entry_id is None or card_id is None or revision is None or (not installed(database)):
        return False
    return bool(database.execute_native('SELECT 1 FROM queue_attempt_abandonments\n        WHERE queue_entry_id=%s AND card_id=%s AND revision=%s', (entry_id, card_id, revision)).fetchone())

def _lock_target(database, card_id):
    database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:card-edit:' + card_id,))

def _locked_origin(database, payload):
    """Serialize with reviews/checkpoints without inferring any repertoire owner."""
    from tempo_storage.queue_position_lock import lock_queue_date_for_position
    card_id, entry_id = (payload['card_id'], payload['queue_entry_id'])
    _lock_target(database, card_id)
    target_card = database.execute_native('SELECT id,revision,start_fen,moves_json,trained_color,content_type FROM cards WHERE id=%s FOR UPDATE', (card_id,)).fetchone()
    initial = database.execute_native('SELECT queue_date,card_id FROM daily_queue WHERE id=%s', (entry_id,)).fetchone()
    queue = None
    current_card = target_card
    if initial:
        if initial['card_id'] != card_id:
            acquired = database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:card-edit:' + initial['card_id'],)).fetchone()[0]
            if not acquired:
                raise ValueError('abandonment_queue_changed')
            current_card = database.execute_native('SELECT id,revision,start_fen,moves_json,trained_color,content_type FROM cards WHERE id=%s FOR UPDATE', (initial['card_id'],)).fetchone()
        if not current_card:
            raise ValueError('abandonment_queue_changed')
        lock_queue_date_for_position(database, initial['queue_date'])
        queue = database.execute_native('SELECT * FROM daily_queue WHERE id=%s FOR UPDATE', (entry_id,)).fetchone()
        if not queue or (queue['queue_date'], queue['card_id']) != (initial['queue_date'], initial['card_id']):
            raise ValueError('abandonment_queue_changed')
    origins = database.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s ORDER BY card_id,revision FOR UPDATE', (entry_id,)).fetchall()
    target = [row for row in origins if row['card_id'] == card_id and row['revision'] == payload['revision']]
    if len(target) != 1:
        raise ValueError('abandonment_origin_missing_or_ambiguous')
    if queue:
        current = [row for row in origins if row['card_id'] == queue['card_id'] and row['revision'] == current_card['revision']]
        if len(current) != 1 or any((current[0][origin_key] != queue[queue_key] for origin_key, queue_key in (('queue_date', 'queue_date'), ('cycle', 'cycle'), ('last_status', 'status'), ('review_result_json', 'review_result_json')))) or any((current[0][key] != current_card[key] for key in ('start_fen', 'moves_json', 'trained_color', 'content_type'))):
            raise ValueError('abandonment_queue_changed')
        if (queue['card_id'], current_card['revision']) != (card_id, payload['revision']):
            queue = None
    return (target[0], queue)

def _check_unaccepted(database, origin, queue):
    entry_id, card_id, revision = (origin['queue_entry_id'], origin['card_id'], origin['revision'])
    if origin['issued_as_head'] != 1 or origin['content_type'] != 'opening' or origin['last_status'] == 'complete' or (origin['review_result_json'] is not None) or (queue and (queue['status'] == 'complete' or queue['review_result_json'] is not None)):
        raise ValueError('abandonment_origin_not_unfinished_issued_opening')
    if database.execute_native('SELECT 1 FROM review_attempt_receipts\n        WHERE queue_entry_id=%s AND card_id=%s LIMIT 1', (entry_id, card_id)).fetchone():
        raise ValueError('abandonment_review_already_accepted')
    if database.execute_native("SELECT 1 FROM opening_evidence_attempts\n        WHERE queue_entry_id=%s AND card_id=%s AND (state='complete' OR review_id IS NOT NULL OR review_result_json IS NOT NULL)\n        LIMIT 1", (entry_id, card_id)).fetchone():
        raise ValueError('abandonment_review_already_accepted')
    if database.execute_native('SELECT 1 FROM study_position_issued_plans\n        WHERE queue_entry_id=%s AND card_id=%s AND card_revision=%s LIMIT 1', (entry_id, card_id, revision)).fetchone():
        raise ValueError('abandonment_position_attempt_not_legacy')

def _require_schema(database):
    from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION
    if not installed(database):
        raise ValueError('abandonment_schema_unavailable')
    versions = [row[0] for row in database.execute_native('SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()]
    if versions != list(range(1, POSTGRES_SCHEMA_VERSION + 1)):
        raise ValueError('abandonment_schema_unavailable')

def plan_in_writer(database, payload):
    payload = validate_request(payload, plan=True)
    _require_schema(database)
    origin, queue = _locked_origin(database, payload)
    _check_unaccepted(database, origin, queue)
    if is_abandoned(database, payload['queue_entry_id'], payload['card_id'], payload['revision']):
        raise ValueError('abandonment_already_exists')
    return {'plan': True, 'queue_entry_id': payload['queue_entry_id'], 'card_id': payload['card_id'], 'revision': payload['revision'], 'origin_sha256': origin_fingerprint(origin)}

def abandon_in_writer(database, payload, *, now):
    payload = validate_request(payload)
    _require_schema(database)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('abandonment_timestamp_invalid')
    request_hash = request_fingerprint(payload)
    database.execute_native('SAVEPOINT queue_attempt_abandonment')
    try:
        _lock_target(database, payload['card_id'])
        prior = database.execute_native('SELECT * FROM queue_attempt_abandonments\n            WHERE operation_id=%s OR (queue_entry_id=%s AND card_id=%s AND revision=%s)\n            FOR UPDATE', (payload['operation_id'], payload['queue_entry_id'], payload['card_id'], payload['revision'])).fetchall()
        if prior:
            if len(prior) != 1 or prior[0]['operation_id'] != payload['operation_id'] or prior[0]['request_sha256'] != request_hash:
                raise ValueError('abandonment_receipt_conflict')
            result = {'abandoned': True, 'idempotent': True, 'operation_id': payload['operation_id'], 'origin_sha256': prior[0]['origin_sha256']}
        else:
            origin, queue = _locked_origin(database, payload)
            _check_unaccepted(database, origin, queue)
            fingerprint = origin_fingerprint(origin)
            if fingerprint != payload['expected_origin_sha256']:
                raise ValueError('abandonment_origin_changed')
            inserted = database.execute_native('INSERT INTO queue_attempt_abandonments\n                (queue_entry_id,card_id,revision,operation_id,request_sha256,origin_sha256,actor,reason,abandoned_at)\n                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING operation_id', (payload['queue_entry_id'], payload['card_id'], payload['revision'], payload['operation_id'], request_hash, fingerprint, payload['actor'], payload['reason'], now)).fetchone()
            if not inserted:
                raise ValueError('abandonment_receipt_conflict')
            result = {'abandoned': True, 'idempotent': False, 'operation_id': payload['operation_id'], 'origin_sha256': fingerprint}
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT queue_attempt_abandonment')
        database.execute_native('RELEASE SAVEPOINT queue_attempt_abandonment')
        raise
    database.execute_native('RELEASE SAVEPOINT queue_attempt_abandonment')
    return result

def main():
    """Private transport for the registered target-checked operator."""
    import argparse
    import base64
    from datetime import timezone
    from tempo_storage.postgres import connection
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-base64', required=True)
    args = parser.parse_args()
    try:
        raw = base64.b64decode(args.request_base64, validate=True)
        if len(raw) > 10000:
            raise ValueError('abandonment_request_too_large')
        wire = json.loads(raw)
        if not isinstance(wire, dict) or set(wire) != {'version', 'plan', 'request'} or wire['version'] != 1 or (type(wire['plan']) is not bool):
            raise ValueError('abandonment_request_invalid')
        with connection() as database:
            result = plan_in_writer(database, wire['request']) if wire['plan'] else abandon_in_writer(database, wire['request'], now=datetime.now(timezone.utc))
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit('queue abandonment held: ' + str(error)) from error
if __name__ == '__main__':
    main()
