"""Read-only bounded evidence for one invocation-owned deferred split."""
import json
import os
import re
import sys
import uuid
import psycopg
from psycopg.rows import dict_row


def validate_ids(card, intent, acceptance, review):
    if not re.fullmatch(r'[0-9a-f]{64}', card):
        raise ValueError('Invalid card identity')
    for value in (intent, acceptance):
        if str(uuid.UUID(value)) != value:
            raise ValueError('Invalid operation identity')
    if not review.startswith('review-attempt:') or str(uuid.UUID(review.removeprefix('review-attempt:'))) != review.removeprefix('review-attempt:'):
        raise ValueError('Invalid review identity')
    return 'prefix-split-intent:' + intent


def main():
    if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Owned disposable PostgreSQL required')
    card, intent, acceptance, review = sys.argv[1:5]
    child = validate_ids(card, intent, acceptance, review)
    with psycopg.connect('postgresql://postgres@postgres:5432/tempo', row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='3s'")
        marker = db.execute("SELECT current_database() AS name,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=current_database()").fetchone()
        if marker != {'name':'tempo', 'marker':'tempo-disposable-postgres-test'}:
            raise RuntimeError('Foreign PostgreSQL target')
        intent_row = db.execute('''SELECT intent_id,card_id,source_revision,queue_entry_id,state,reason,
            review_attempt_id,review_id,child_operation_id,next_check_at,created_at,updated_at
            FROM study_deferred_prefix_splits WHERE intent_id=%s AND card_id=%s''', (intent, card)).fetchone()
        receipts = db.execute('''SELECT operation_id,command_name,state,background,attempt_count,retry_cycle,
            cycle_attempt_count,next_retry_at,lease_expires_at,(attempt_token IS NOT NULL) AS leased,
            left(error_json,1000) AS error_json,left(last_error_json,1000) AS last_error_json,created_at,updated_at
            FROM operation_receipts WHERE operation_id = ANY(%s) ORDER BY operation_id''',
            ([acceptance, review, child],)).fetchall()
        review_receipt = db.execute('''SELECT attempt_id,card_id,queue_entry_id,review_id FROM review_attempt_receipts
            WHERE attempt_id=%s AND card_id=%s''', (review.removeprefix('review-attempt:'), card)).fetchone()
        print(json.dumps({'intent':intent_row, 'operations':receipts, 'review_receipt':review_receipt,
            'expected_operations':[acceptance, review, child]}, default=str))


if __name__ == '__main__':
    main()
