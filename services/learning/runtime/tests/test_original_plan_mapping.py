"""Synthetic records emitted by the pinned legacy owner codec, not historical data."""
import json
from dataclasses import replace
from datetime import date, datetime
import pytest
from tempo_learning_runtime.original_plan_mapping import (OriginalPlanEvidence, ContentOccurrence, VerifiedContentGeneration, PositionDefinition, VerifiedPositionOwner, Blocked, map_original_plan_to_slots)

RECORDS = json.loads(r'''[{"attempt_id":"attempt-original","authored_source_token":{"authored_digest":"authored","header":{"id":"rep-italian"},"integrity":{"complete":true},"original_snapshot_id":"snapshot-1","original_source_digest":"digest-1","original_source_revision":"revision-1","repertoire_id":"rep-italian"},"due_witnesses":[{"definition":{"expected_uci":"e2e4","policy_version":"policy-v1","position":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","repertoire_id":"rep-italian","variant":"standard"},"due":true,"issued_at":"2026-11-02T09:00:00+00:00","owner_epoch":"owner-epoch-1","position_revision":7,"study_day":"2026-11-02"},{"definition":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -","repertoire_id":"rep-italian","variant":"standard"},"due":true,"issued_at":"2026-11-02T09:00:00+00:00","owner_epoch":"owner-epoch-1","position_revision":7,"study_day":"2026-11-02"}],"issued_at":"2026-11-02T09:00:00+00:00","local_day":"2026-11-02","original_card_id":"legacy-card-1","original_card_revision":4,"owner_epoch":"owner-epoch-1","plan_id":"5f30c63628d511883ed0a2daefacc328a7cfc1a9033bfac2b42afa5cc9490ea1","prefix_proofs":[],"queue_entry_id":41,"record_version":1,"route":{"decision_slots":[{"definition":{"expected_uci":"e2e4","policy_version":"policy-v1","position":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","repertoire_id":"rep-italian","variant":"standard"},"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","move_offset":0,"original_ply":0},{"definition":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -","repertoire_id":"rep-italian","variant":"standard"},"fen":"rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2","move_offset":2,"original_ply":2}],"moves_uci":["e2e4","e7e5","g1f3","b8c6"],"opponent_cue_ply":null,"opponent_cue_uci":null,"route_id":"italian-knight-attack","start_ply":0,"starting_fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","target_ply":2},"source":{"complete":true,"current_integrity":true,"legal_verified":true,"snapshot_id":"snapshot-1","source_digest":"digest-1","source_revision":"revision-1"},"target":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -","repertoire_id":"rep-italian","variant":"standard"}},{"attempt_id":"attempt-original","authored_source_token":null,"due_witnesses":[{"definition":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","repertoire_id":"rep-clocks","variant":"standard"},"due":true,"issued_at":"2026-11-02T09:00:00+00:00","owner_epoch":"owner-epoch-1","position_revision":7,"study_day":"2026-11-02"},{"definition":{"expected_uci":"f3g1","policy_version":"policy-v1","position":"rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq -","repertoire_id":"rep-clocks","variant":"standard"},"due":true,"issued_at":"2026-11-02T09:00:00+00:00","owner_epoch":"owner-epoch-1","position_revision":7,"study_day":"2026-11-02"}],"issued_at":"2026-11-02T09:00:00+00:00","local_day":"2026-11-02","original_card_id":"legacy-card-1","original_card_revision":4,"owner_epoch":"owner-epoch-1","plan_id":"c12c70133f5aeda350c7dea612bfb976f061fa11f14801923e64af86832dc6f3","prefix_proofs":[],"queue_entry_id":41,"record_version":1,"route":{"decision_slots":[{"definition":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","repertoire_id":"rep-clocks","variant":"standard"},"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","move_offset":0,"original_ply":0},{"definition":{"expected_uci":"f3g1","policy_version":"policy-v1","position":"rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq -","repertoire_id":"rep-clocks","variant":"standard"},"fen":"rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2","move_offset":2,"original_ply":2},{"definition":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","repertoire_id":"rep-clocks","variant":"standard"},"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 4 3","move_offset":4,"original_ply":4},{"definition":{"expected_uci":"f3g1","policy_version":"policy-v1","position":"rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq -","repertoire_id":"rep-clocks","variant":"standard"},"fen":"rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 6 4","move_offset":6,"original_ply":6}],"moves_uci":["g1f3","g8f6","f3g1","f6g8","g1f3","g8f6","f3g1","f6g8"],"opponent_cue_ply":null,"opponent_cue_uci":null,"route_id":"clock-long","start_ply":0,"starting_fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","target_ply":0},"source":{"complete":true,"current_integrity":true,"legal_verified":true,"snapshot_id":"snapshot-1","source_digest":"digest-1","source_revision":"revision-1"},"target":{"expected_uci":"g1f3","policy_version":"policy-v1","position":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","repertoire_id":"rep-clocks","variant":"standard"}}]''')

def inputs(raw):
    route, source = raw['route'], raw['source']
    rep = raw['target']['repertoire_id']
    plan = OriginalPlanEvidence(raw['attempt_id'], raw['queue_entry_id'], raw['original_card_id'],
        raw['original_card_revision'], rep, 'standard', raw['owner_epoch'], 'content',
        source['source_digest'], date.fromisoformat(raw['local_day']), datetime.fromisoformat(raw['issued_at']),
        json.dumps(raw), raw['plan_id'], 'postgres-jsonb-value', 'legacy-issued-plan-record-v1')
    bindings = tuple(ContentOccurrence(rep, 'standard', route['route_id'], s['original_ply'], s['fen'],
        s['definition']['position'], s['definition']['expected_uci'], s['definition']['policy_version'])
        for s in route['decision_slots'])
    defs = tuple(dict.fromkeys(PositionDefinition(rep, 'standard', x.exact_position, x.expected_uci,
        x.policy_version) for x in bindings))
    content = VerifiedContentGeneration('content', source['source_digest'], source['snapshot_id'],
        source['source_revision'], 'generation', 'certificate', True, True, bindings)
    owner = VerifiedPositionOwner('study', rep, raw['owner_epoch'], source['source_digest'], True, defs)
    return plan, content, owner

@pytest.mark.parametrize('raw', RECORDS)
def test_original_occurrences_and_due_witnesses_are_preserved(raw):
    p, c, o = inputs(raw)
    result = map_original_plan_to_slots(p, c, o)
    assert isinstance(result, tuple)
    assert [x.original_occurrence_id for x in result] == [(raw['route']['route_id'], s['original_ply']) for s in raw['route']['decision_slots']]
    assert [x.original_fen for x in result] == [s['fen'] for s in raw['route']['decision_slots']]
    assert all(x.attempt_id == p.original_attempt_id and x.due_witness.study_day == p.issue_study_day and x.due_witness.issued_at == p.issued_at for x in result)
    if raw['route']['route_id'] == 'clock-long':
        assert result[0].exact_position == result[2].exact_position
        assert result[0].original_occurrence_id != result[2].original_occurrence_id
        assert result[0].due_witness == result[2].due_witness


def test_failure_order_and_no_partial_result():
    p, c, o = inputs(RECORDS[0])
    cases = [
        (p, replace(c, bindings=c.bindings[:1]), o, 'LP_SLOT_MISSING', 1),
        (p, replace(c, bindings=(c.bindings[0],) + c.bindings), o, 'LP_SLOT_AMBIGUOUS', 0),
        (p, replace(c, bindings=(replace(c.bindings[0], expected_uci='e2e5'),) + c.bindings[1:]), o, 'LP_SLOT_ILLEGAL', 0),
        (replace(p, original_plan_digest='0'*64), c, o, 'LP_PLAN_INVALID', None),
        (p, replace(c, current=False), o, 'LP_CONTENT_UNVERIFIED', None),
        (p, c, replace(o, owner_epoch='changed'), 'LP_SOURCE_STALE', None),
    ]
    for plan, content, owner, code, index in cases:
        assert map_original_plan_to_slots(plan, content, owner) == Blocked('BLOCK', code, p.original_attempt_id, index)
    assert p.stored_record_json == json.dumps(RECORDS[0])

@pytest.mark.parametrize('flag', ['complete', 'current_integrity', 'legal_verified'])
def test_original_false_source_flag_blocks_new_mapping(flag):
    import hashlib
    raw = json.loads(json.dumps(RECORDS[0]))
    raw['source'][flag] = False
    payload = {key: value for key, value in raw.items() if key != 'plan_id'}
    raw['plan_id'] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
    p, c, o = inputs(raw)
    assert map_original_plan_to_slots(p, c, o) == Blocked('BLOCK', 'LP_SOURCE_STALE', p.original_attempt_id, None)


def test_opaque_json_number_is_accepted_only_without_semantic_precision_loss():
    import hashlib
    raw = json.loads(json.dumps(RECORDS[0]))
    raw['authored_source_token']['header']['weight'] = 0.1
    payload = {key: value for key, value in raw.items() if key != 'plan_id'}
    raw['plan_id'] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
    p, c, o = inputs(raw)
    assert isinstance(map_original_plan_to_slots(p, c, o), tuple)
    # This JSONB numeric is not equal to 0.1, although float parsing rounds to 0.1.
    altered = replace(p, stored_record_json=p.stored_record_json.replace('"weight": 0.1', '"weight": 0.10000000000000001'))
    assert map_original_plan_to_slots(altered, c, o) == Blocked('BLOCK', 'LP_PLAN_INVALID', p.original_attempt_id, None)
