from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app.services import postgres_opening_evidence as evidence
from tempo_legacy_database.adapters.study import position_first_responses as storage


def plan():
    key = NS(repertoire_id='r', variant='standard', position='position')
    definition = NS(key=key, expected_uci='e2e4', policy_version='v1')
    slot = NS(definition=definition, original_ply=6, move_offset=0,
              fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1')
    return NS(plan_id='a'*64, attempt_id='attempt', queue_entry_id=21, original_card_id='card',
              original_card_revision=4, owner_epoch='epoch', decision_slots=(slot,), target=definition,
              starting_fen=slot.fen, moves_uci=('e2e4',))


def event(**changes):
    args = dict(event_id='event', decision_index=0, actual_uci='d2d4', manual_failure=False,
                assistance_before=False, observed_at='2026-11-02T12:00:00+00:00',
                classify_alternative=lambda *_: 'wrong')
    args.update(changes)
    return evidence.position_first_response_envelope(plan(), **args)


def test_server_adjudicates_issued_slot_and_never_guesses_alternative():
    row = event()
    assert (row['decision_index'], row['original_ply'], row['disposition']) == (0, 6, 'wrong')
    for disposition in ('alternate', 'unverified'):
        assert event(classify_alternative=lambda *_, d=disposition: d)['disposition'] == disposition
    assert event(actual_uci='e2e4')['disposition'] == 'expected'
    assert event(actual_uci='e2e5')['disposition'] == 'illegal'
    assert event(actual_uci=None, manual_failure=True)['disposition'] == 'manual_failure'
    with pytest.raises(ValueError):
        event(classify_alternative=lambda *_: 'maybe')


def test_digest_and_frozen_definition_are_checked():
    row = event()
    digest = evidence._position_digest(row)
    assert evidence.decode_position_first_response(row, digest, plan()) == row
    changed = dict(row, original_ply=0)
    with pytest.raises(ValueError):
        evidence.decode_position_first_response(changed, digest, plan())
    with pytest.raises(ValueError):
        evidence.decode_position_first_response(changed, evidence._position_digest(changed), plan())


def test_abandoned_wrong_retries_once_and_changed_bytes_conflict(monkeypatch):
    saved = []
    effects = []
    monkeypatch.setattr(storage, 'receipt', lambda *args: saved[:])
    def insert(_db, row, digest, _json, accepted):
        saved.append(dict(payload_json=row, payload_digest=digest, accepted_at=accepted))
        return ('event',)
    monkeypatch.setattr(storage, 'insert', insert)
    def grade(_db, attempt, ids):
        effects.append((attempt, ids))
        return object()
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    row = event()
    first = evidence.persist_position_first_response(None, plan(), row, now, apply_grade=grade)
    again = evidence.persist_position_first_response(None, plan(), row, now, apply_grade=grade)
    assert first == again and effects == [('attempt', ('event',))]
    with pytest.raises(evidence.EvidenceConflict):
        evidence.persist_position_first_response(None, plan(), event(assistance_before=True), now, apply_grade=grade)
    assert len(effects) == 1


def test_correct_stays_pending_and_grade_failure_aborts(monkeypatch):
    saved = []
    monkeypatch.setattr(storage, 'receipt', lambda *args: saved[:])
    monkeypatch.setattr(storage, 'insert', lambda _db, row, digest, _json, accepted:
                        saved.append(dict(payload_json=row, payload_digest=digest, accepted_at=accepted)) or ('event',))
    def unexpected(*_):
        raise AssertionError('positive response must await completion')
    evidence.persist_position_first_response(None, plan(), event(actual_uci='e2e4'),
                                             datetime.now(timezone.utc), apply_grade=unexpected)
    saved.clear()
    def held(*_):
        raise evidence.EvidenceConflict('grade held')
    with pytest.raises(evidence.EvidenceConflict):
        evidence.persist_position_first_response(None, plan(), event(), datetime.now(timezone.utc),
                                                 apply_grade=held)


def test_standalone_checkpoint_bridges_original_ply_and_replays_guided_correction(monkeypatch):
    import json
    import sys
    from types import ModuleType
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from tempo_evidence_math import opening_position_key
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source

    frozen = plan()
    slot = frozen.decision_slots[0]
    slot.definition.key.position = opening_position_key(slot.fen)
    decision = {'decision_id': 'd' * 64, 'decision_index': 0, 'move_offset': 2,
                'fen': slot.fen, 'expected_uci': 'e2e4'}
    raw = {'attempt_id': 'attempt', 'origin_queue_entry_id': 21, 'queue_entry_id': 21,
           'started_at': '2026-11-02T12:00:00+00:00', 'study_timezone': 'UTC',
           'manifest': {'manifest_id': 'a' * 64, 'presentation_snapshot_id': 1,
                        'repertoire_id': 'r', 'card_id': 'card', 'card_revision': 4,
                        'trained_color': 'white', 'decisions': [decision]},
           'events': [{'sequence': 1, 'decision_index': 0, 'decision_id': 'd' * 64,
                       'expected_uci': 'e2e4', 'kind': 'first_response',
                       'observed_at': '2026-11-02T12:00:00+00:00', 'response_uci': 'd2d4',
                       'disposition': 'unverified'}]}
    from app.contracts.opening_evidence import OpeningDecisionManifest
    scope = OpeningDecisionManifest.model_validate(raw['manifest']).model_dump(mode='json')
    raw['manifest'] = evidence.issued_position_manifest(frozen, scope)
    raw['events'][0]['decision_id'] = raw['manifest']['decisions'][0]['decision_id']
    # The saved source proves the one expected move, independently of the
    # browser's unverified disposition.
    source = NS(routes=[NS(decisions=[NS(definition=slot.definition)])], authored_token=NS(authored_digest='authored'))
    monkeypatch.setattr(position_study_source, 'fence_position_source', lambda *_: None)
    grade_module = ModuleType('tempo_legacy_database.adapters.study.position_grade_effects')
    grades = []
    grade_module.apply_in_borrowed_transaction = lambda _db, attempt, ids, **kw: grades.append((attempt, ids))
    monkeypatch.setitem(sys.modules, grade_module.__name__, grade_module)
    from tempo_legacy_database.adapters.study import position_owner_epoch
    monkeypatch.setattr(position_owner_epoch, 'owner_mode', lambda *_: NS(disposition='position', epoch='epoch'))
    monkeypatch.setattr(evidence, 'begin_position_checkpoint', lambda _db, prep: evidence.PositionCheckpointFence(prep, _db, storage, 'authored', evidence._POSITION_CHECKPOINT_SEAL))
    monkeypatch.setattr(storage, 'lock_original_queue', lambda *_: True)
    monkeypatch.setattr(storage, 'lock_attempt', lambda *_: True)
    monkeypatch.setattr(storage, 'acceptance_clock', lambda *_: (datetime.now(timezone.utc), 'UTC'))
    monkeypatch.setattr(evidence, '_validate_checkpoint_scope', lambda *_, **__: None)
    monkeypatch.setattr(evidence, '_persist_observations', lambda *_: None)
    monkeypatch.setattr(evidence.storage, 'insert_attempt_returning', lambda *_: True)
    monkeypatch.setattr(evidence.storage, 'insert_event_batch', lambda *_: None)
    monkeypatch.setattr(evidence.storage, 'update_attempt', lambda *_: None)
    saved = []
    monkeypatch.setattr(storage, 'receipt', lambda *_: saved[:])
    def saved_grade(_db, event_id):
        import hashlib
        return {'event_id':event_id, 'attempt_id':'attempt', 'decision_index':0, 'source_kind':'study',
                'effect_id':hashlib.sha256(('study:first_response:' + event_id).encode()).hexdigest(),
                'request_digest':hashlib.sha256((frozen.plan_id + ':' + saved[0]['payload_digest']).encode()).hexdigest()}
    monkeypatch.setattr(storage, 'grade_receipt', saved_grade)
    def insert(_db, envelope, digest, _json, accepted):
        saved.append(dict(payload_json=envelope, payload_digest=digest, accepted_at=accepted,
                          **{k: envelope[k] for k in ('event_id', 'attempt_id', 'decision_index', 'repertoire_id', 'variant', 'position', 'owner_epoch')}))
        return ('event',)
    monkeypatch.setattr(storage, 'insert', insert)
    monkeypatch.setattr(evidence, '_read_checkpoint_source', lambda *_, **__: (None, ()))
    from dataclasses import replace
    req = OpeningEvidenceCheckpoint.model_validate(raw)
    prepared = replace(evidence._compute_checkpoint_publication(req, None, ()),
                       position_preparation=evidence.PositionCheckpointPreparation(req, frozen, source, evidence._POSITION_CHECKPOINT_SEAL))
    result = evidence.commit_standalone_checkpoint(None, prepared)
    assert result['persisted'] and len(grades) == 1
    assert saved[0]['payload_json']['original_ply'] == 6
    assert saved[0]['payload_json']['decision_index'] == 0
    assert saved[0]['payload_json']['disposition'] == 'wrong'
    # A later guided correction is not another first response or transition.
    raw['events'].append({'sequence': 2, 'decision_index': 0, 'decision_id': raw['manifest']['decisions'][0]['decision_id'],
                          'expected_uci': 'e2e4', 'kind': 'correction',
                          'observed_at': '2026-11-02T12:01:00+00:00', 'response_uci': 'e2e4'})
    req = OpeningEvidenceCheckpoint.model_validate(raw)
    from tempo_evidence_math.position_learning import Hold
    prepared = replace(evidence._compute_checkpoint_publication(req, None, ()),
                       position_preparation=evidence.PositionCheckpointPreparation(req, frozen, Hold('source_changed'), evidence._POSITION_CHECKPOINT_SEAL))
    evidence.commit_standalone_checkpoint(None, prepared)
    assert len(saved) == len(grades) == 1
    raw['events'][0]['response_uci'] = 'c2c4'
    req = OpeningEvidenceCheckpoint.model_validate(raw)
    prepared = replace(evidence._compute_checkpoint_publication(req, None, ()),
                       position_preparation=evidence.PositionCheckpointPreparation(req, frozen, Hold('source_changed'), evidence._POSITION_CHECKPOINT_SEAL))
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        evidence.commit_standalone_checkpoint(None, prepared)
    assert len(saved) == len(grades) == 1


def test_public_preparation_and_owner_fence_bind_writer(monkeypatch):
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from tempo_legacy_database.adapters.study import position_issued_plans
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_evidence_math.position_learning import Hold

    frozen = plan()
    frozen.target = NS(key=NS(repertoire_id='r'))
    frozen.to_record = lambda: {'authored_source_token': {'authored_digest': 'authored'}}
    req = OpeningEvidenceCheckpoint.model_validate({
        'attempt_id':'attempt', 'origin_queue_entry_id':21, 'queue_entry_id':21,
        'started_at':'2026-11-02T12:00:00+00:00', 'study_timezone':'UTC',
        'manifest': {'manifest_id':'a'*64, 'presentation_snapshot_id':1, 'repertoire_id':'r',
                     'card_id':'card', 'card_revision':4, 'trained_color':'white',
                     'decisions':[{'decision_id':'d'*64, 'decision_index':0, 'move_offset':6,
                                   'fen':frozen.decision_slots[0].fen, 'expected_uci':'e2e4'}]}})
    class Cursor:
        def fetchone(self): return (True,)
    class DB:
        read_only = False
        raw = NS(info=NS(transaction_status=NS(name='INTRANS')))
        def execute_native(self, *_): return Cursor()
    db = DB()
    monkeypatch.setattr(storage, 'has_position_tables', lambda *_: True)
    monkeypatch.setattr(position_issued_plans, 'replay_exact_attempt', lambda *_: frozen)
    monkeypatch.setattr(position_study_source, 'prepare_position_source', lambda *_: Hold('source_changed'))
    prep = evidence.prepare_position_checkpoint(db, req)
    fence = evidence.begin_position_checkpoint(db, prep)
    assert fence.preparation.plan is frozen and fence.authored_digest == 'authored'
    with pytest.raises(evidence.EvidenceConflict):
        evidence.commit_position_checkpoint(DB(), fence, [])
    with pytest.raises(evidence.EvidenceConflict):
        evidence.begin_position_checkpoint(db, NS())


def test_completion_replay_skips_stale_source_but_new_grade_holds():
    from tempo_evidence_math.position_learning import Hold
    frozen = plan()
    payload = event(actual_uci='e2e4')
    row = {'event_id': payload['event_id'], 'attempt_id': payload['attempt_id'],
           'payload_json': payload, 'payload_digest': evidence._position_digest(payload)}
    prep = evidence.PositionCheckpointPreparation(None, frozen, Hold('source_changed'), evidence._POSITION_CHECKPOINT_SEAL)
    db = object()
    fence = evidence.PositionCheckpointFence(prep, db, storage, 'authored', evidence._POSITION_CHECKPOINT_SEAL)
    accepted = [{'receipt':row, 'grade_receipt':{'grade':'good'}}]
    evidence.fence_position_completion(db, fence, accepted)
    with pytest.raises(evidence.EvidenceConflict, match='source_changed'):
        evidence.fence_position_completion(db, fence, [{'receipt':row, 'grade_receipt':None}])


def test_issued_manifest_covers_displayed_context_and_repeated_position_ordinals():
    import chess
    from tempo_evidence_math import opening_position_key
    board = chess.Board()
    moves = ('g1f3', 'g8f6', 'f3g1', 'f6g8', 'g1f3')
    slots = []
    for offset, move in enumerate(moves):
        if board.turn == chess.WHITE:
            key = NS(repertoire_id='r', variant='standard', position=opening_position_key(board.fen()))
            slots.append(NS(move_offset=offset, original_ply=offset + 4, fen=board.fen(),
                            definition=NS(key=key, expected_uci=move, policy_version='v1')))
        board.push_uci(move)
    frozen = NS(plan_id='a'*64, original_card_id='card', original_card_revision=4,
                target=NS(key=NS(repertoire_id='r')), starting_fen=chess.STARTING_FEN,
                moves_uci=moves, decision_slots=tuple(slots))
    scope = {'manifest_version':1, 'policy_version':1, 'manifest_id':'b'*64,
             'presentation_snapshot_id':13, 'repertoire_id':'r', 'card_id':'card',
             'card_revision':4, 'trained_color':'white',
             # Original physical card includes only the final target segment.
             'decisions':[{'decision_id':'c'*64, 'decision_index':0, 'move_offset':0,
                           'fen':slots[-1].fen, 'expected_uci':'g1f3'}]}
    result = evidence.issued_position_manifest(frozen, scope)
    assert result['presentation_snapshot_id'] == 13
    assert [d['decision_index'] for d in result['decisions']] == [0, 1, 2]
    assert [d['move_offset'] for d in result['decisions']] == [0, 2, 4]
    assert result['decisions'][0]['decision_id'] == result['decisions'][2]['decision_id']
    assert [slot.original_ply for slot in slots] == [4, 6, 8]
    assert scope['decisions'][0]['decision_id'] == 'c'*64


def test_foreground_preparation_validates_issued_manifest_not_physical(monkeypatch):
    from contextlib import nullcontext
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from tempo_legacy_database.adapters.study import position_issued_plans
    frozen = plan()
    scope = {'manifest_version':1, 'policy_version':1, 'manifest_id':'b'*64,
             'presentation_snapshot_id':13, 'repertoire_id':'r', 'card_id':'card',
             'card_revision':4, 'trained_color':'white',
             'decisions':[{'decision_id':'c'*64, 'decision_index':0, 'move_offset':2,
                           'fen':frozen.decision_slots[0].fen, 'expected_uci':'e2e4'}]}
    expected = evidence.issued_position_manifest(frozen, scope)
    now = datetime.now(timezone.utc).isoformat()
    request = OpeningEvidenceCheckpoint.model_validate({'attempt_id':'attempt',
        'origin_queue_entry_id':21, 'queue_entry_id':21, 'started_at':now,
        'study_timezone':'UTC', 'manifest':expected, 'events':[]})
    monkeypatch.setattr(evidence.storage, 'postgres_is_configured', lambda: True)
    monkeypatch.setattr(evidence.storage, 'read_connection', lambda: nullcontext(object()))
    monkeypatch.setattr(evidence.storage, 'presentation_context', lambda *_: {'id':13})
    monkeypatch.setattr(evidence, '_manifest_snapshot', lambda row: row)
    monkeypatch.setattr(evidence, 'decision_manifest', lambda *_: scope)
    monkeypatch.setattr(storage, 'has_position_tables', lambda *_: True)
    monkeypatch.setattr(position_issued_plans, 'replay_exact_attempt', lambda *_: frozen)
    assert evidence.prepare_checkpoint(request) == expected
    tampered = request.model_copy(deep=True)
    tampered.manifest.decisions[0].move_offset = 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        evidence.prepare_checkpoint(tampered)


def test_all_displayed_events_map_to_issued_ordinals_including_repeated_position(monkeypatch):
    import chess
    from tempo_evidence_math import opening_position_key
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_legacy_database.adapters.study import position_owner_epoch
    board = chess.Board()
    slots = []
    moves = ('g1f3', 'g8f6', 'f3g1', 'f6g8', 'g1f3')
    for offset, move in enumerate(moves):
        if board.turn == chess.WHITE:
            key = NS(repertoire_id='r', variant='standard', position=opening_position_key(board.fen()))
            slots.append(NS(move_offset=offset, original_ply=offset+4, fen=board.fen(),
                            definition=NS(key=key, expected_uci=move, policy_version='v1')))
        board.push_uci(move)
    frozen = NS(plan_id='a'*64, attempt_id='attempt', queue_entry_id=21, original_card_id='card',
                original_card_revision=4, owner_epoch='epoch', target=slots[-1].definition,
                starting_fen=chess.STARTING_FEN, moves_uci=moves, decision_slots=tuple(slots))
    scope = {'manifest_version':1, 'policy_version':1, 'manifest_id':'b'*64,
             'presentation_snapshot_id':13, 'repertoire_id':'r', 'card_id':'card',
             'card_revision':4, 'trained_color':'white',
             'decisions':[{'decision_id':'c'*64, 'decision_index':0, 'move_offset':0,
                           'fen':slots[-1].fen, 'expected_uci':'g1f3'}]}
    manifest = evidence.issued_position_manifest(frozen, scope)
    now = datetime.now(timezone.utc).isoformat()
    events = [{'sequence':i+1, 'decision_index':i, 'decision_id':d['decision_id'],
               'expected_uci':d['expected_uci'], 'kind':'first_response', 'observed_at':now,
               'response_uci':d['expected_uci'], 'assistance':None, 'disposition':'expected'}
              for i, d in enumerate(manifest['decisions'])]
    req = OpeningEvidenceCheckpoint.model_validate({'attempt_id':'attempt', 'origin_queue_entry_id':21,
        'queue_entry_id':21, 'started_at':now, 'study_timezone':'UTC', 'manifest':manifest, 'events':events})
    source = NS(routes=[NS(decisions=[NS(definition=s.definition) for s in slots])],
                authored_token=NS(authored_digest='authored'))
    db = object()
    prep = evidence.PositionCheckpointPreparation(req, frozen, source, evidence._POSITION_CHECKPOINT_SEAL)
    fence = evidence.PositionCheckpointFence(prep, db, storage, 'authored', evidence._POSITION_CHECKPOINT_SEAL)
    monkeypatch.setattr(position_study_source, 'fence_position_source', lambda *_: None)
    monkeypatch.setattr(position_owner_epoch, 'owner_mode', lambda *_: NS(disposition='position', epoch='epoch'))
    monkeypatch.setattr(storage, 'acceptance_clock', lambda *_: (datetime.now(timezone.utc), 'UTC'))
    monkeypatch.setattr(storage, 'grade_receipt', lambda *_: None)
    saved = []
    monkeypatch.setattr(storage, 'receipt', lambda _db, event_id, attempt, index:
                        [r for r in saved if r['event_id'] == event_id or
                         (r['attempt_id'], r['decision_index']) == (attempt, index)])
    def insert(_db, payload, digest, _json, accepted):
        saved.append({**payload, 'payload_json':payload, 'payload_digest':digest, 'accepted_at':accepted})
        return ('ok',)
    monkeypatch.setattr(storage, 'insert', insert)
    evidence.commit_position_checkpoint(db, fence, events)
    assert [(r['decision_index'], r['original_ply']) for r in saved] == [(0,4), (1,6), (2,8)]
    assert len({r['event_id'] for r in saved}) == 3
