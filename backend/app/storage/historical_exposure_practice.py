"""Existing canonical Study practice can serve a pending scoped request.

No exposure-specific attempt history or review/settlement writer is created.
"""
from datetime import datetime
import json
from tempo_learning_review import historical_exposure as policy


def practice_from_row(scope,record,row,now):
    if row is None:return None
    try:
        issued=datetime.fromisoformat(row['first_issued_at']);reviewed=datetime.fromisoformat(row['reviewed_at'])
        request=json.loads(row['request_json']);origin=json.loads(row['origin_result'])
        if not (record.card_scope()==scope and row['card_id']==scope.card_id
            and row['revision']==record.request.encounter.card_revision==request.get('expected_revision')
            and row['source_kind']=='study' and row['invalidated_at'] is None
            and row['source_ref']=='attempt:'+row['attempt_id'] and row['scheduling_status']=='canonical'
            and row['issued_as_head']==1 and row['origin_status']=='complete'
            and row['review_id']==origin.get('review_id') and row['review_id'] is not None
            and issued.tzinfo is not None and reviewed.tzinfo is not None
            and record.accepted_at<=issued<=reviewed<=now):return None
        return policy.NormalPractice(scope,row['revision'],row['queue_entry_id'],str(row['review_id']),issued,reviewed)
    except (ValueError,TypeError,KeyError,AttributeError):return None


def completed_practices(database,scope,records,now):
    if len(records)>128 or any(record.card_scope()!=scope or record.state!='pending' for record in records):
        raise policy.ExposureRejected('complete_scoped_pending_inventory_required')
    if not records:return ()
    inputs=[dict(key=record.request.encounter.key(),revision=record.request.encounter.card_revision,
        accepted=record.accepted_at.isoformat()) for record in records]
    if len({item['key'] for item in inputs})!=len(inputs):raise policy.ExposureRejected('complete_scoped_pending_inventory_required')
    rows=database.execute_native('''SELECT requested.key,row_to_json(proof) AS proof
        FROM jsonb_to_recordset(%s::jsonb) AS requested(key text,revision bigint,accepted timestamptz)
        LEFT JOIN LATERAL (
            SELECT review.id review_id,review.card_id,review.reviewed_at,review.source_kind,review.source_ref,
                review.invalidated_at,receipt.attempt_id,receipt.queue_entry_id,receipt.request_json,receipt.scheduling_status,
                origin.revision,origin.issued_as_head,origin.first_issued_at,
                origin.last_status origin_status,origin.review_result_json origin_result
            FROM review_attempt_receipts receipt
            JOIN reviews review ON review.id=receipt.review_id AND review.card_id=receipt.card_id
            JOIN queue_attempt_origins origin ON origin.queue_entry_id=receipt.queue_entry_id
                AND origin.card_id=receipt.card_id AND origin.revision=requested.revision
            WHERE receipt.card_id=%s AND receipt.scheduling_status='canonical'
                AND review.source_kind='study' AND review.invalidated_at IS NULL
                AND review.source_ref='attempt:'||receipt.attempt_id
                AND origin.issued_as_head=1 AND origin.last_status='complete' AND origin.first_issued_at IS NOT NULL
                AND origin.first_issued_at::timestamptz>=requested.accepted
                AND review.reviewed_at::timestamptz>=origin.first_issued_at::timestamptz
                AND review.reviewed_at::timestamptz<=%s
                AND origin.review_result_json::jsonb->>'review_id'=review.id::text
                AND receipt.request_json::jsonb->>'expected_revision'=origin.revision::text
            ORDER BY review.reviewed_at::timestamptz,review.id LIMIT 1
        ) proof ON TRUE ORDER BY requested.key''',(json.dumps(inputs),scope.card_id,now)).fetchall()
    indexed={record.request.encounter.key():record for record in records}
    if len(rows)!=len(records) or {row['key'] for row in rows}!=set(indexed):
        raise policy.ExposureRejected('complete_practice_projection_required')
    return tuple(event for row in rows if (event:=practice_from_row(scope,indexed[row['key']],row['proof'],now)) is not None)
