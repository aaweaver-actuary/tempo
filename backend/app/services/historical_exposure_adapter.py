"""Verified facts and ordinary queue admission on the caller's transaction."""
from dataclasses import asdict
from datetime import date
import json
from tempo_learning_review import historical_exposure as policy
from ..storage.historical_exposure import BorrowedExposureStore
from ..storage.historical_exposure_facts import current_source_rows,lock_source_inputs,buried_today,queued_entry
from ..storage.legacy_game_source import CARD_SOURCE_FIELDS,GAME_SOURCE_FIELDS
from .historical_exposure_source import chronology


def proof_document(verified):
    return {key:value for key,value in asdict(verified).items() if key!='request'}


class CurrentFactReader:
    def __init__(self,proofs,rows,queue_facts,certificates):
        self.proofs,self.rows,self.queue_facts,self.certificates=proofs,rows,queue_facts,certificates
    def current(self,request):
        identity=request.encounter;key=identity.key();proof=self.proofs[key];row=self.rows[key]
        token=proof['authority'];card={name:row['card_'+name] for name in CARD_SOURCE_FIELDS}
        game={name:row['game_'+name] for name in GAME_SOURCE_FIELDS}
        source_current=(all(row[name]==token[name] for name in
            ('scope_source_revision','canonical_prefix_revision','catalogue_generation','graph_generation','graph_state'))
            and game==proof['game_header'] and not game['adaptive_excluded']
            and row['canonical_prefix_moves_json'] is not None and json.loads(row['canonical_prefix_moves_json'])==[]
            and all(card[name]==proof['card_source'][name] for name in CARD_SOURCE_FIELDS if name!='introduced_at'))
        certificate=self.certificates[identity.repertoire_id]
        return policy.CurrentFacts(identity if source_current else None,'verified' if source_current else 'unknown',
            tuple(row['memberships']),certificate is not None and certificate==proof['integrity_certificate'],
            bool(card['id'] and row['card_state'] in ('learning','mature') and not row['deleted']
                and not row['blocked'] and not card['archived'] and not card['pending_validation'] and not card['superseded_by']),
            card['introduced_at'] is not None,chronology(card['introduced_at'],identity.occurred_at),
            current_revision=card['revision'],**self.queue_facts)


class OrdinaryQueueWriter:
    def __init__(self,database,scope,day):self.database,self.scope,self.day=database,scope,day
    def admit(self,identity,queue_day,*,prefer_front):
        if policy.CardScope(identity.target_id,identity.workspace_id,identity.card_id)!=self.scope or queue_day!=self.day:
            raise policy.ExposureRejected('exposure_writer_scope_mismatch')
        from .review_service import enqueue_introduced_review
        result=enqueue_introduced_review(self.database,identity.card_id,identity.card_revision,queue_day,prefer_front=prefer_front)
        return policy.Placement(result.status,result.reason,result.entry_id,result.mode)


def locked_ports(database,scope,verified=None,day=None):
    """Caller holds the card lock; only this queue day's position lock follows."""
    from .postgres_integrity import current_integrity_certificate
    from ..queue_position_lock import lock_queue_date_for_position
    day=day or date.today().isoformat()
    initial={verified.request.encounter.key():proof_document(verified)} if verified else {}
    store=BorrowedExposureStore(database,scope,initial)
    requests={record.request.encounter.key():record.request for record in store.pending_for_card(scope)}
    if verified:requests[verified.request.encounter.key()]=verified.request
    proofs={key:store.source(request) for key,request in requests.items()}
    lock_source_inputs(database,tuple(requests.values()))
    lock_queue_date_for_position(database,day)
    rows=current_source_rows(database,scope,tuple(requests.values())) if requests else {}
    certificates={request.encounter.repertoire_id:current_integrity_certificate(database,
        request.encounter.repertoire_id,rows[key]['graph_generation']) for key,request in requests.items()}
    entry=queued_entry(database,scope.card_id,day)
    queue=dict(buried=buried_today(database,scope.card_id,day),queue_day=day,
        queue_entry_id=entry['id'] if entry else None)
    return store,OrdinaryQueueWriter(database,scope,day),CurrentFactReader(proofs,rows,queue,certificates)
