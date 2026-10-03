"""Named regular-suite protections for the shadow evidence foundation."""
import json
import hashlib
import pytest
import chess
from app.opening_evidence_contracts import OpeningEvidenceCheckpoint
from app.services.opening_decision_evidence import decision_manifest, reduce_observations, validate_checkpoint, EvidenceConflict


MOVES = ['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6','e1g1','f8e7','f1e1']


def manifest(moves=MOVES, repertoire='rep', fen=chess.STARTING_FEN, color='white'):
    return decision_manifest({'id':1,'card_id':'card','revision':3,'start_fen':fen,
                              'moves_json':json.dumps(moves),'trained_color':color}, repertoire)


def event(decisions, index, sequence, kind='first_response', response=None, **extra):
    decision = decisions[index]
    return {'sequence':sequence,'decision_index':index,'decision_id':decision['decision_id'],
            'expected_uci':decision['expected_uci'],'kind':kind,
            'observed_at':'2026-09-30T12:00:00Z',
            'response_uci':response if response else decision['expected_uci'] if kind in {'first_response','correction'} else None,
            'assistance':None, 'disposition':None, **extra}


def test_manifest_uses_saved_revision_and_only_learner_decisions_deterministically():
    result = manifest()
    assert result == manifest()
    assert result['card_revision'] == 3
    assert [decision['move_offset'] for decision in result['decisions']] == [0,2,4,6,8,10]
    assert [decision['decision_index'] for decision in result['decisions']] == list(range(6))
    assert manifest(MOVES[:2],color='black')['decisions'][0]['move_offset'] == 1


def test_shadow_six_decision_failure_preserves_clean_predecessors_and_unreached_successor():
    decisions = manifest()['decisions']
    events = [event(decisions,index,index+1) for index in range(4)]
    events += [event(decisions,4,5,response='d2d3'),event(decisions,4,6,kind='reveal'),
               event(decisions,4,7,kind='correction')]
    observations = reduce_observations(events,'America/New_York')
    assert len(observations) == 5
    assert [observation['clean'] for observation in observations] == [True]*4+[False]
    assert observations[-1]['first_response_uci'] == 'd2d3'
    assert observations[-1]['corrected'] and observations[-1]['revealed']
    assert observations[-1]['first_response_correct'] is False


def test_three_clean_first_responses_are_three_observations():
    decisions = manifest()['decisions']
    result=reduce_observations([event(decisions,index,index+1) for index in range(3)],'UTC')
    assert len(result)==3 and all(observation['clean'] for observation in result)


def test_assistance_before_response_stays_assisted_and_later_assistance_does_not_rewrite_recall():
    decisions = manifest()['decisions']
    assisted=reduce_observations([event(decisions,0,1,'assistance',assistance='teaching'),event(decisions,0,2)],'UTC')[0]
    assert assisted['first_response_correct'] and not assisted['clean']
    assert assisted['assistance_before_response']==['teaching']
    clean=reduce_observations([event(decisions,0,1),event(decisions,0,2,'assistance',assistance='hint')],'UTC')[0]
    assert clean['clean'] and clean['assistance_before_response']==[]


def test_manual_again_has_no_fabricated_first_response_and_correction_is_not_clean():
    decisions=manifest()['decisions']
    result=reduce_observations([event(decisions,0,1,'manual_failure'),event(decisions,0,2,'correction')],'UTC')[0]
    assert result['manual_failure'] and result['corrected'] and not result['clean']
    assert result['first_response_uci'] is None and result['first_response_correct'] is None


def test_transposed_presentations_share_decision_identity_with_separate_provenance():
    first=manifest(['g1f3','g8f6','d2d4','d7d5','c2c4'])
    second=manifest(['d2d4','d7d5','g1f3','g8f6','c2c4'])
    assert first['decisions'][-1]['decision_id']==second['decisions'][-1]['decision_id']
    assert first['manifest_id']!=second['manifest_id']
    assert manifest(repertoire='other')['decisions'][0]['decision_id']!=manifest()['decisions'][0]['decision_id']


def test_manifest_preserves_legal_en_passant_identity_normalization():
    no_capture=chess.Board(); no_capture.push_uci('e2e4')
    first=manifest(['e7e5'],fen=no_capture.fen(en_passant='fen'),color='black')
    second=manifest(['e7e5'],fen=no_capture.fen(en_passant='legal'),color='black')
    assert first['decisions'][0]['decision_id']==second['decisions'][0]['decision_id']


@pytest.mark.parametrize('moves', [['e2e5'],[],['e2e4','e7e5']])
def test_invalid_opening_manifest_fails_closed(moves):
    with pytest.raises(ValueError): manifest(moves)


def test_checkpoint_rejects_client_identity_and_sequence_conflicts():
    authoritative=manifest()
    payload={'attempt_id':'attempt','manifest':authoritative,'origin_queue_entry_id':1,
             'started_at':'2026-09-30T12:00:00Z','study_timezone':'UTC',
             'events':[event(authoritative['decisions'],0,1)]}
    validate_checkpoint(OpeningEvidenceCheckpoint.model_validate(payload),authoritative)
    payload['events'][0]['expected_uci']='d2d4'
    with pytest.raises(EvidenceConflict): validate_checkpoint(OpeningEvidenceCheckpoint.model_validate(payload),authoritative)


def test_noncontiguous_or_replaced_first_response_cannot_be_reduced():
    decisions=manifest()['decisions']
    with pytest.raises(EvidenceConflict): reduce_observations([event(decisions,0,2)],'UTC')
    with pytest.raises(EvidenceConflict): reduce_observations([event(decisions,0,1),event(decisions,0,2)],'UTC')


def test_study_day_uses_original_response_time_and_frozen_timezone():
    decisions=manifest()['decisions']
    observed=event(decisions,0,1,observed_at='2026-09-30T03:00:00Z')
    assert reduce_observations([observed],'America/New_York')[0]['study_day']=='2026-09-29'


@pytest.mark.parametrize('trained_color', ['white', 'black'])
def test_manifest_identity_preserves_captured_color_in_v1_hash(trained_color):
    moves = ['e2e4'] if trained_color=='white' else ['e2e4','e7e5']
    result = manifest(moves=moves, color=trained_color)
    assert result == manifest(moves=moves, color=trained_color)
    components = ['opening-decision-manifest',1,1,'rep','card',3,
                  trained_color,chess.STARTING_FEN,json.dumps(moves)]
    expected = hashlib.sha256(json.dumps(components,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert result['manifest_id'] == expected
    components[6] = 'black' if trained_color=='white' else 'white'
    opposite_identity = hashlib.sha256(json.dumps(components,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert result['manifest_id'] != opposite_identity
    assert result['trained_color'] == trained_color
