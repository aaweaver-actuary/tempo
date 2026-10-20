"""Trusted canonical Study receipts for a complete locked exposure card scope."""
from datetime import datetime
import json
from tempo_learning_review import historical_exposure as policy


def practice_from_row(scope, record, row, now) -> policy.PracticeCompletion | None:
    """Validate retained owner records; no caller completion flags are accepted."""
    if row is None:return None
    identity=record.request.encounter
    try:
        reviewed_at=datetime.fromisoformat(row['reviewed_at'])
        request=json.loads(row['request_json'])
        origin_result=json.loads(row['origin_result'])
        valid=(record.card_scope()==scope and row['card_id']==scope.card_id==identity.card_id
            and row['queue_entry_id']==record.placement.queue_entry_id
            and row['revision']==identity.card_revision==request.get('expected_revision')
            and row['source_kind']=='study' and row['invalidated_at'] is None
            and row['source_ref']=='attempt:'+row['attempt_id']
            and row['scheduling_status']=='canonical' and row['issued_as_head']==1
            and row['origin_status']=='complete' and row['review_id']==origin_result.get('review_id')
            and row['review_id'] is not None and reviewed_at.tzinfo is not None
            and record.accepted_at<=reviewed_at<=now)
        if not valid:return None
        return policy.PracticeCompletion(scope.card_id,identity.card_revision,row['queue_entry_id'],
            str(row['review_id']),reviewed_at,'study',True,scope)
    except (ValueError,TypeError,KeyError,AttributeError):return None


def completed_practices(database,scope,records,now) -> tuple[policy.PracticeCompletion,...]:
    """One bounded projection, at most one verified receipt per active intent."""
    if len(records)>128 or any(record.card_scope()!=scope or record.state!='active' for record in records):
        raise policy.ExposureRejected('complete_scoped_active_inventory_required')
    if not records:return ()
    inputs=[dict(key=record.request.encounter.key(),entry=record.placement.queue_entry_id,
        revision=record.request.encounter.card_revision,accepted=record.accepted_at.isoformat()) for record in records]
    if len({item['key'] for item in inputs})!=len(inputs):
        raise policy.ExposureRejected('complete_scoped_active_inventory_required')
    rows=database.execute_native('''SELECT requested.key,row_to_json(proof) AS proof
        FROM jsonb_to_recordset(%s::jsonb) AS requested(key text,entry bigint,revision bigint,accepted timestamptz)
        LEFT JOIN LATERAL (
            SELECT review.id AS review_id,review.card_id,review.reviewed_at,review.source_kind,
                review.source_ref,review.invalidated_at,receipt.attempt_id,receipt.queue_entry_id,
                receipt.request_json,receipt.scheduling_status,origin.revision,origin.issued_as_head,
                origin.last_status AS origin_status,origin.review_result_json AS origin_result
            FROM review_attempt_receipts receipt
            JOIN reviews review ON review.id=receipt.review_id AND review.card_id=receipt.card_id
            JOIN queue_attempt_origins origin ON origin.queue_entry_id=receipt.queue_entry_id
                AND origin.card_id=receipt.card_id AND origin.revision=requested.revision
            WHERE receipt.card_id=%s AND receipt.queue_entry_id=requested.entry
                AND receipt.scheduling_status='canonical' AND review.source_kind='study'
                AND review.invalidated_at IS NULL AND review.source_ref='attempt:'||receipt.attempt_id
                AND origin.issued_as_head=1 AND origin.last_status='complete'
                AND origin.review_result_json::jsonb->>'review_id'=review.id::text
                AND receipt.request_json::jsonb->>'expected_revision'=origin.revision::text
                AND review.reviewed_at::timestamptz>=requested.accepted
                AND review.reviewed_at::timestamptz<=%s
            ORDER BY review.reviewed_at::timestamptz,review.id LIMIT 1
        ) proof ON TRUE ORDER BY requested.key''',(json.dumps(inputs),scope.card_id,now)).fetchall()
    indexed={record.request.encounter.key():record for record in records}
    if len(rows)!=len(records) or {row['key'] for row in rows}!=set(indexed):
        raise policy.ExposureRejected('complete_practice_projection_required')
    completions=[]
    for row in rows:
        event=practice_from_row(scope,indexed[row['key']],row['proof'],now)
        if event is not None:completions.append(event)
    return tuple(completions)
