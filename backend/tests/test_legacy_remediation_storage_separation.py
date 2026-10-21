"""Legacy remediation authority and lifecycle retain semantic owner storage."""
from datetime import datetime,timezone
import json
from types import SimpleNamespace

import pytest
from app.services import legacy_study_remediation as bridge
from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken

NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)


def authority():
    return LegacyAuthorityToken(kind='legacy_graph',target_id='target',workspace_id='legacy',repertoire_id='rep',
        scope_source_revision=1,canonical_prefix_revision=1,source_digest='a'*64,catalogue_digest='b'*64,
        catalogue_generation=1,graph_digest='c'*64,graph_generation=1,graph_state='ready')


def current():
    return bridge.LegacyAcceptedDecision('card',2,'decision',authority(),'position',('e2e4',),'legacy',
        'route',0,0,'d'*64,('e2e4',),True,True,True)


def payload():
    return dict(authority=authority().model_dump(mode='json'),decision_scope_proof={},game_id='game',
        occurred_at=NOW.isoformat(),comparison=dict(card_revision=2,expected_moves=['e2e4']),encounter=dict(observed_move='d2d4'))


def test_existing_read_projection_uses_tuple_cursor_without_owner_mutation():
    calls=[];raw=SimpleNamespace(info=object(),row_factory='owner')
    raw.cursor=lambda **options: calls.append(options) or SimpleNamespace(execute=lambda *args: calls.append(args) or 'result')
    projection=bridge.storage.read_projection(SimpleNamespace(raw=raw))
    assert projection.execute(' SELECT value',('value',))=='result'
    assert projection.info is raw.info and raw.row_factory=='owner'
    assert len(calls)==2
    with pytest.raises(RuntimeError,match='read-only projection'):projection.execute('UPDATE value')
    assert len(calls)==2


@pytest.mark.parametrize('sources',[[],[dict(source_current=True)]*4,[dict(source_current=False)]])
def test_current_fact_source_fence_rejects_before_optional_graph_lock(monkeypatch,sources):
    monkeypatch.setattr(bridge.storage,'current_facts',lambda *_: [dict(payload_json=json.dumps(payload()),introduced_at=NOW.isoformat())])
    monkeypatch.setattr(bridge.storage,'source_fences',lambda *_: sources)
    monkeypatch.setattr(bridge.storage,'try_lock_graph',lambda *_: pytest.fail('Stale sources reached graph lock'))
    assert bridge.current_fact(None,'card',lock=True) is None


def test_optional_authority_is_reread_after_all_share_locks(monkeypatch):
    events=[];rows=iter([[dict(payload_json=json.dumps(payload()),introduced_at=NOW.isoformat())],[]])
    monkeypatch.setattr(bridge.storage,'current_facts',lambda *_: events.append('facts') or next(rows))
    monkeypatch.setattr(bridge.storage,'source_fences',lambda *_: events.append('sources') or [dict(game_id='game',source_current=True)])
    monkeypatch.setattr(bridge.storage,'try_lock_graph',lambda *_: events.append('graph') or (1,))
    monkeypatch.setattr(bridge.storage,'try_hold_authority',lambda *_: events.append('authority rows') or (1,))
    monkeypatch.setattr(bridge.storage,'try_hold_source_games',lambda *_: events.append('source games') or [('game',)])
    assert bridge.current_fact(None,'card',lock=True) is None
    assert events==['facts','sources','graph','authority rows','source games','facts']


@pytest.mark.parametrize('origin',[None,(1,)])
def test_prior_or_missing_origin_never_receives_retrospective_binding(monkeypatch,origin):
    monkeypatch.setattr(bridge.storage,'is_postgres',lambda _: True)
    monkeypatch.setattr(bridge.storage,'issued_origin',lambda *_: origin)
    monkeypatch.setattr(bridge,'current_fact',lambda *_args,**_kwargs: pytest.fail('Prior issuance reached authority acquisition'))
    assert bridge.bind_first_issuance(None,4,'card',2) is None


def test_first_issuance_binds_after_current_authority_with_original_clock(monkeypatch):
    events=[]
    monkeypatch.setattr(bridge.storage,'is_postgres',lambda _: True)
    monkeypatch.setattr(bridge.storage,'issued_origin',lambda *_: (0,))
    monkeypatch.setattr(bridge,'current_fact',lambda *_args,**_kwargs: events.append('authority') or current())
    monkeypatch.setattr(bridge,'datetime',SimpleNamespace(now=lambda *_: events.append('clock') or NOW))
    def bind(bound,accepted):
        assert bound.attempt_id=='legacy-queue:4:revision:2' and bound.issued_at==NOW and accepted.card_id=='card'
        events.append('bind')
    monkeypatch.setattr(bridge,'repository',lambda _: SimpleNamespace(bind_attempt=bind))
    bridge.bind_first_issuance(None,4,'card',2);assert events==['authority','clock','bind']


def test_settlement_missing_binding_stops_before_current_authority(monkeypatch):
    monkeypatch.setattr(bridge.storage,'is_postgres',lambda _: True)
    monkeypatch.setattr(bridge.storage,'bound_attempt',lambda *_: None)
    monkeypatch.setattr(bridge,'current_fact',lambda *_args,**_kwargs: pytest.fail('Missing binding acquired authority'))
    assert bridge.settle_review(None,'card',7,queue_entry_id=4,revision=2,source_attempt_id='attempt') is False


def test_settlement_compares_bound_authority_before_review_lookup(monkeypatch):
    monkeypatch.setattr(bridge.storage,'is_postgres',lambda _: True)
    monkeypatch.setattr(bridge.storage,'bound_attempt',lambda *_: ('attempt','card',2,'different',1,NOW))
    monkeypatch.setattr(bridge,'current_fact',lambda *_args,**_kwargs: current())
    monkeypatch.setattr(bridge.storage,'settlement_review',lambda *_: pytest.fail('Changed binding read review'))
    assert bridge.settle_review(None,'card',7,queue_entry_id=4,revision=2,source_attempt_id='attempt') is False


@pytest.mark.parametrize('source',['attempt:other','attempt:attempt'])
def test_settlement_consumes_only_matching_review_then_clears_priority(monkeypatch,source):
    events=[];accepted=current();identity=bridge.accepted_storage_identity(accepted)
    monkeypatch.setattr(bridge.storage,'is_postgres',lambda _: True)
    monkeypatch.setattr(bridge.storage,'bound_attempt',lambda *_: ('legacy-queue:4:revision:2','card',2,*identity,NOW))
    monkeypatch.setattr(bridge,'current_fact',lambda *_args,**_kwargs: accepted)
    monkeypatch.setattr(bridge.storage,'settlement_review',lambda *_: dict(rating='correct',guided=0,reviewed_at=NOW.isoformat(),source_kind='study',invalidated_at=None,source_ref=source))
    monkeypatch.setattr(bridge.storage,'settlement_origin',lambda *_: (0,))
    monkeypatch.setattr(bridge,'repository',lambda _: SimpleNamespace(settle=lambda *_: events.append('settle') or True))
    monkeypatch.setattr(bridge,'clear_priority',lambda *_: events.append('clear'))
    result=bridge.settle_review(None,'card',7,queue_entry_id=4,revision=2,source_attempt_id='attempt')
    assert result is (source=='attempt:attempt')
    assert events==(['settle','clear'] if source=='attempt:attempt' else [])


def test_invalidation_requires_review_owner_mark_before_remediation_effect(monkeypatch):
    monkeypatch.setattr(bridge.storage,'invalidated_review',lambda *_: (None,))
    monkeypatch.setattr(bridge,'repository',lambda _: pytest.fail('Uninvalidated review changed settlement'))
    with pytest.raises(ValueError,match='review owner must invalidate'):bridge.invalidate_review(None,'card',7,'reason')


def test_admission_keeps_card_before_day_lock_order(monkeypatch):
    events=[]
    monkeypatch.setattr(bridge.storage,'lock_card',lambda *_: events.append('card'))
    monkeypatch.setattr(bridge,'lock_queue_date_for_position',lambda *_: events.append('day'))
    monkeypatch.setattr(bridge,'admit_prelocked',lambda *_: events.append('admit') or 'result')
    assert bridge.admit_card(None,dict(card_id='card'))=='result'
    assert events==['card','day','admit']


def test_frozen_epoch_rejection_precedes_canonical_queue_writer(monkeypatch):
    from app.services import queue_application,queue_ordering,frozen_opening_order,review_service
    monkeypatch.setattr(bridge,'current_fact',lambda *_args,**_kwargs: current())
    monkeypatch.setattr(bridge,'repository',lambda _: object())
    tokens=iter(['before','after']);monkeypatch.setattr(frozen_opening_order,'activation',lambda *_args,**_kwargs: next(tokens))
    monkeypatch.setattr(queue_application,'presentation_rows',lambda *_: [])
    monkeypatch.setattr(queue_ordering,'_protected_queue_ids',lambda _: set())
    monkeypatch.setattr(bridge.storage,'buried_entry',lambda *_: None)
    monkeypatch.setattr(bridge,'apply_admission',lambda _repo,_candidate,writer: writer.apply(None))
    monkeypatch.setattr(review_service,'ensure_card_queued_after',lambda *_args,**_kwargs: pytest.fail('Changed frozen epoch reached queue writer'))
    assert bridge.admit_prelocked(None,'card','2026-10-21')==dict(status='deferred',reason='frozen_order_changed',queue_entry_id=None)


def test_status_without_accepted_work_stops_before_authority_or_module_reads(monkeypatch):
    monkeypatch.setattr(bridge.storage,'target_identity',lambda _: ('target','legacy'))
    monkeypatch.setattr(bridge.storage,'card_work',lambda *_: None)
    monkeypatch.setattr(bridge.storage,'current_revision',lambda *_: (2,))
    monkeypatch.setattr(bridge,'current_fact',lambda *_: pytest.fail('No accepted work read authority'))
    result=bridge.card_status(None,'card')
    assert result==dict(target_id='target',workspace_id='legacy',card_id='card',accepted=False,current=False,admission=None,settlement=None,actual_card_revision=2)
