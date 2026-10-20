"""Borrowed exposure ports; canonical Study owns every actual queue write."""
from dataclasses import asdict
from datetime import date
import json
from tempo_learning_review import historical_exposure as policy
from ..storage.historical_exposure import BorrowedExposureStore
from ..storage.historical_exposure_facts import current_source_rows,lock_source_inputs,queued_entry,buried_today
from ..storage.legacy_game_source import CARD_SOURCE_FIELDS,GAME_SOURCE_FIELDS
from .historical_exposure_source import chronology
from .legacy_game_evidence_scope import BRIDGE_MISS_REASON
from .queue_ordering import MISS_REASON


def proof_document(verified):
    return {key:value for key,value in asdict(verified).items() if key!='request'}


class CurrentFactReader:
    def __init__(self,proofs,rows,queue_facts,certificates):
        self.proofs,self.rows,self.queue_facts,self.certificates=proofs,rows,queue_facts,certificates

    def current(self,request):
        identity=request.encounter;key=identity.key();proof=self.proofs[key];row=self.rows[key]
        token=proof['authority'];actual_card={name:row['card_'+name] for name in CARD_SOURCE_FIELDS}
        actual_game={name:row['game_'+name] for name in GAME_SOURCE_FIELDS}
        expected=proof['card_source']
        epochs=('scope_source_revision','canonical_prefix_revision','catalogue_generation','graph_generation','graph_state')
        source_current=(all(row[name]==token[name] for name in epochs)
            and actual_game==proof['game_header'] and not actual_game['adaptive_excluded']
            and row['canonical_prefix_moves_json'] is not None and json.loads(row['canonical_prefix_moves_json'])==[]
            and all(actual_card[name]==expected[name] for name in CARD_SOURCE_FIELDS
                if name!='introduced_at'))
        certificate=self.certificates[identity.repertoire_id]
        integrity_current=certificate is not None and certificate==proof['integrity_certificate']
        return policy.CurrentFacts(identity if source_current else None,'verified' if source_current else 'unknown',
            tuple(row['memberships']),integrity_current,
            bool(actual_card['id'] and row['card_state'] in ('learning','mature')
                and not row['deleted'] and not row['blocked'] and not actual_card['archived']
                and not actual_card['pending_validation'] and not actual_card['superseded_by']),
            actual_card['introduced_at'] is not None,chronology(actual_card['introduced_at'],identity.occurred_at),
            **self.queue_facts)


class ProtectedHeadWriter:
    def __init__(self,database,scope,queue_day):
        self.database,self.scope,self.queue_day=database,scope,queue_day
    def prioritize(self,identity,queue_day,reason):
        if policy.CardScope(identity.target_id,identity.workspace_id,identity.card_id)!=self.scope or reason!=policy.REASON or queue_day!=self.queue_day:
            raise policy.ExposureRejected('exposure_writer_scope_mismatch')
        existing=queued_entry(self.database,identity.card_id,queue_day)
        if existing and existing['gameplay_priority_reason'] not in (None,policy.REASON):
            return policy.Placement('deferred','unrelated_priority_preserved',existing['id'])
        from .review_service import ensure_card_queued_after
        # The policy and adapter inspect current priority under the same card/day
        # locks. This invokes the existing writer, never a second queue authority.
        ensure_card_queued_after(self.database,identity.card_id,0,priority_reason=reason,
            queue_date=queue_day,protect_presented=True,preserve_order=False)
        row=queued_entry(self.database,identity.card_id,queue_day)
        return policy.Placement('applied',queue_entry_id=row['id']) if row else policy.Placement('deferred','queue_unavailable')
    def clear_priority(self,scope,reason):
        if scope!=self.scope or reason!=policy.REASON:raise policy.ExposureRejected('exposure_writer_scope_mismatch')
        from .review_service import clear_queued_card_priority
        clear_queued_card_priority(self.database,scope.card_id,queue_date=self.queue_day,reason=reason)



def locked_ports(database,scope,verified=None,day=None):
    """Caller holds card/edit + row; source locks precede the day lock."""
    from .postgres_integrity import current_integrity_certificate
    from .queue_application import presentation_rows,protected_queue_entry_ids
    day=day or date.today().isoformat()
    initial={verified.request.encounter.key():proof_document(verified)} if verified else {}
    store=BorrowedExposureStore(database,scope,initial)
    records=store.active_for_card(scope)
    requests={record.request.encounter.key():record.request for record in records}
    if verified:requests[verified.request.encounter.key()]=verified.request
    proofs={key:store.source(request) for key,request in requests.items()}
    lock_source_inputs(database,tuple(requests.values()))
    from ..queue_position_lock import lock_queue_date_for_position
    lock_queue_date_for_position(database,day)
    rows=current_source_rows(database,scope,tuple(requests.values())) if requests else {}
    certificates={request.encounter.repertoire_id:current_integrity_certificate(database,
        request.encounter.repertoire_id,proofs[key]['authority']['graph_generation']) for key,request in requests.items()}
    queue_rows=presentation_rows(database,day)
    raw_target=queued_entry(database,scope.card_id,day)
    target=next((row for row in queue_rows if raw_target and row['id']==raw_target['id']),None)
    protected=protected_queue_entry_ids(queue_rows)
    reason=raw_target['gameplay_priority_reason'] if raw_target else None
    priority_class=('recall' if reason in {MISS_REASON,BRIDGE_MISS_REASON} else 'exposure' if reason==policy.REASON
                    else 'other' if reason else 'none')
    buried=buried_today(database,scope.card_id,day)
    queue_facts=dict(buried=buried,protected=bool(target and target['id'] in protected),queue_day=day,
        queue_entry_id=target['id'] if target else None,priority_class=priority_class)
    return store,ProtectedHeadWriter(database,scope,day),CurrentFactReader(proofs,rows,queue_facts,certificates)
