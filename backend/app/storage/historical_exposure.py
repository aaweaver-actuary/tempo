"""Borrowed PostgreSQL storage for explicitly confirmed exposure intent.

No connection/transaction lifecycle and no queue policy lives here. The command
adapter must hold the target/workspace/card lock for the complete active set.
"""
from dataclasses import asdict
from datetime import datetime
import json
from tempo_learning_review import historical_exposure as policy

MAX_CARD_CONTRIBUTORS=128


def request_document(request):
    value=asdict(request)
    value['encounter']['occurred_at']=request.encounter.occurred_at.isoformat()
    return value


def read_request(value):
    data=dict(value);identity=dict(data.pop('encounter'))
    identity['occurred_at']=datetime.fromisoformat(identity['occurred_at'])
    for name in ('route_ids','expected_moves'):identity[name]=tuple(identity[name])
    return policy.Request(policy.EncounterIdentity(**identity),**data)


def record_document(record):
    return dict(schema_version=1,request=request_document(record.request),accepted_at=record.accepted_at.isoformat(),
        state=record.state,placement=asdict(record.placement),queue_day=record.queue_day,
        completion_review_id=record.completion_review_id,terminal_reason=record.terminal_reason)


def read_record(value):
    value=json.loads(value) if isinstance(value,str) else dict(value)
    if value.pop('schema_version')!=1:raise policy.ExposureRejected('stored_exposure_version_unsupported')
    value['request']=read_request(value['request'])
    value['accepted_at']=datetime.fromisoformat(value['accepted_at'])
    value['placement']=policy.Placement(**value['placement'])
    return policy.Record(**value)


class BorrowedExposureStore:
    def __init__(self,database,scope,proofs=None):
        if database.read_only:raise RuntimeError('Exposure storage requires the caller writer transaction')
        self.database,self.scope,self.proofs=database,scope,proofs or {}

    def find(self,encounter_key):
        row=self.database.execute_native('''SELECT record_json FROM historical_exposure_intents
            WHERE target_id=%s AND workspace_id=%s AND card_id=%s AND encounter_key=%s''',
            (self.scope.target_id,self.scope.workspace_id,self.scope.card_id,encounter_key)).fetchone()
        return read_record(row['record_json']) if row else None

    def save(self,record):
        if record.card_scope()!=self.scope:raise policy.ExposureRejected('exposure_store_scope_mismatch')
        key=record.request.encounter.key()
        proof=self.proofs.get(key)
        # Source proof is immutable and required for a new intent. An ordinary
        # lifecycle update must never try to insert a null proof or create a row.
        if proof is None:
            row=self.database.execute_native('''UPDATE historical_exposure_intents SET state=%s,record_json=%s::jsonb
                WHERE target_id=%s AND workspace_id=%s AND card_id=%s AND encounter_key=%s
                RETURNING encounter_key''', (record.state,json.dumps(record_document(record)),
                self.scope.target_id,self.scope.workspace_id,self.scope.card_id,key)).fetchone()
        else:
            row=self.database.execute_native('''INSERT INTO historical_exposure_intents
                (target_id,workspace_id,card_id,encounter_key,state,record_json,source_json)
                VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                ON CONFLICT(target_id,workspace_id,encounter_key) DO UPDATE SET
                    state=EXCLUDED.state,record_json=EXCLUDED.record_json
                WHERE historical_exposure_intents.card_id=EXCLUDED.card_id
                RETURNING encounter_key''',
                (self.scope.target_id,self.scope.workspace_id,self.scope.card_id,key,record.state,
                 json.dumps(record_document(record)),json.dumps(proof))).fetchone()
        if row is None:raise policy.ExposureRejected('retained_exposure_required')

    def active_for_card(self,scope):
        if scope!=self.scope:raise policy.ExposureRejected('exposure_store_scope_mismatch')
        rows=self.database.execute_native('''SELECT record_json FROM historical_exposure_intents
            WHERE target_id=%s AND workspace_id=%s AND card_id=%s AND state='active'
            ORDER BY encounter_key LIMIT 129 FOR UPDATE''',
            (scope.target_id,scope.workspace_id,scope.card_id)).fetchall()
        if len(rows)>MAX_CARD_CONTRIBUTORS:
            raise policy.ExposureRejected('complete_exposure_inventory_exceeds_budget')
        return tuple(read_record(row['record_json']) for row in rows)

    def source(self,request):
        key=request.encounter.key()
        if key in self.proofs:return self.proofs[key]
        row=self.database.execute_native('''SELECT source_json FROM historical_exposure_intents
            WHERE target_id=%s AND workspace_id=%s AND card_id=%s AND encounter_key=%s''',
            (self.scope.target_id,self.scope.workspace_id,self.scope.card_id,key)).fetchone()
        if row is None:raise policy.ExposureRejected('retained_exposure_proof_required')
        return json.loads(row['source_json']) if isinstance(row['source_json'],str) else row['source_json']
