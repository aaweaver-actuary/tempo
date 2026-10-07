"""Issue #82: diagnostic arithmetic uses persisted reducer facts, not grades."""
import json
from datetime import datetime, timezone
import chess
import pytest
from app.services.opening_decision_evidence import decision_manifest, reduce_observations
from app.services.prefix_diagnostics import project_prefix_diagnostics


def manifest(repertoire='rep', revision=1, color='white'):
    moves = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'] if color == 'white' else ['e2e4', 'e7e5', 'g1f3', 'b8c6']
    return decision_manifest({'id': revision, 'card_id': 'card', 'revision': revision,
        'start_fen': chess.STARTING_FEN, 'moves_json': json.dumps(moves), 'trained_color': color}, repertoire)


def event(presentation, index, sequence, kind='first_response', day='2026-09-28', **changes):
    decision = presentation['decisions'][index]
    return {'sequence': sequence, 'decision_index': index, 'decision_id': decision['decision_id'],
        'expected_uci': decision['expected_uci'], 'kind': kind, 'observed_at': day + 'T12:00:00Z',
        'response_uci': decision['expected_uci'] if kind in {'first_response', 'correction'} else None,
        'assistance': None, 'disposition': None, **changes}


def project(presentation, event_groups, study_timezone='UTC'):
    attempts = [{'attempt_id': str(index), 'state': 'partial', 'started_at': '2026-09-28T12:00:00Z'} for index in range(len(event_groups))]
    records = [{'attempt_id': str(index), 'observation': observation} for index, events in enumerate(event_groups)
               for observation in reduce_observations(events, study_timezone)]
    return project_prefix_diagnostics(presentation, 1, attempts, records)


def test_prefix_diagnostics_matches_shadow_reducer_representative_attempts():
    presentation = manifest()
    events = [event(presentation, 0, 1), event(presentation, 1, 2, response_uci='d2d3'),
              event(presentation, 1, 3, 'reveal'), event(presentation, 1, 4, 'correction')]
    result = project(presentation, [events])
    expected = reduce_observations(events, 'UTC')
    for decision, observation in zip(result['decisions'], expected):
        assert decision['recent_outcomes'] == [{'attempt_id': '0', 'state': 'partial', **observation}]
        assert decision['clean_successes'] == int(observation['clean'])
    assert result['decisions'][1]['unassisted_first_response_failures'] == 1


@pytest.mark.parametrize('assistance', ['teaching', 'hint', 'revealed', 'guided', 'other'])
def test_prefix_diagnostics_assistance_and_correction_never_create_clean_recall(assistance):
    presentation = manifest()
    events = [event(presentation, 0, 1, 'assistance', assistance=assistance), event(presentation, 0, 2),
              event(presentation, 1, 3, 'manual_failure'), event(presentation, 1, 4, 'correction')]
    decisions = project(presentation, [events])['decisions']
    assert [decision['clean_successes'] for decision in decisions] == [0, 0, 0]
    assert decisions[0]['unassisted_first_responses'] == 0
    assert decisions[0]['assistance_categories'] == {assistance: 1}
    assert decisions[1]['manual_failures'] == decisions[1]['corrections'] == 1
    assert all(decision['coverage'] == 'unknown' for decision in decisions)


def test_prefix_diagnostics_partial_attempt_preserves_reached_predecessors():
    presentation = manifest()
    decisions = project(presentation, [[event(presentation, 0, 1), event(presentation, 1, 2, response_uci='d2d3')]])['decisions']
    assert decisions[0]['clean_successes'] == 1
    assert decisions[1]['first_response_failures'] == 1
    assert decisions[2]['reached_observations'] == 0
    assert decisions[2]['coverage'] == 'unknown' and decisions[2]['recent_outcomes'] == []


def test_prefix_diagnostics_distinct_days_use_frozen_study_day():
    presentation = manifest()
    events = [[event(presentation, 0, 1, observed_at='2026-09-28T03:00:00Z')] for _ in range(8)]
    decision = project(presentation, events, 'America/New_York')['decisions'][0]
    assert decision['clean_successes'] == 8 and decision['distinct_clean_days'] == 1
    assert decision['distinct_unassisted_response_days'] == 1
    assert decision['recent_outcomes'][0]['study_day'] == '2026-09-27'
    assert decision['coverage'] == 'weak'


def test_prefix_diagnostics_unknown_weak_and_strong_measure_coverage():
    presentation = manifest()
    assert project(presentation, [])['decisions'][0]['coverage'] == 'unknown'
    events = [[event(presentation, 0, 1, day=day, response_uci='d2d3')] for day in ['2026-09-26', '2026-09-27', '2026-09-28']]
    assert project(presentation, events[:1])['decisions'][0]['coverage'] == 'weak'
    decision = project(presentation, events)['decisions'][0]
    assert decision['coverage'] == 'strong' and decision['clean_successes'] == decision['distinct_clean_days'] == 0
    assert decision['unassisted_first_response_failures'] == 3


@pytest.mark.parametrize('disposition', ['illegal', 'unverified'])
def test_prefix_diagnostics_unverified_responses_do_not_establish_coverage(disposition):
    presentation = manifest()
    decision = project(presentation, [[event(presentation, 0, 1, disposition=disposition)]])['decisions'][0]
    assert decision['coverage'] == 'unknown' and decision['clean_successes'] == 0
    assert decision['first_responses'] == 1


def test_prefix_diagnostics_later_assistance_preserves_reducer_clean_evidence():
    presentation = manifest()
    decision = project(presentation, [[event(presentation, 0, 1), event(presentation, 0, 2, 'assistance', assistance='hint')]])['decisions'][0]
    assert decision['clean_successes'] == 1 and decision['assistance_before_response'] == 0


def test_prefix_diagnostics_recent_outcomes_order_instants_not_timezone_strings():
    presentation = manifest()
    earlier = event(presentation, 0, 1, observed_at='2026-09-28T12:00:00+02:00')
    later = event(presentation, 0, 1, observed_at='2026-09-28T11:00:00Z')
    assert project(presentation, [[earlier], [later]])['decisions'][0]['recent_outcomes'][0]['attempt_id'] == '1'


def test_prefix_diagnostics_history_window_and_recent_outcomes_are_bounded():
    presentation = manifest()
    events = [[event(presentation, 0, 1)] for _ in range(100)]
    result = project(presentation, events + [[]])
    assert result['window']['attempt_count'] == 100 and result['window']['older_attempts_excluded']
    assert result['decisions'][0]['clean_successes'] == 100
    assert len(result['decisions'][0]['recent_outcomes']) == 20


def test_prefix_diagnostics_repeated_decision_identity_keeps_occurrences_separate():
    presentation = decision_manifest({'id':1,'card_id':'loop','revision':1,'start_fen':chess.STARTING_FEN,
        'moves_json':json.dumps(['g1f3','g8f6','f3g1','f6g8','g1f3']),'trained_color':'white'},'rep')
    assert presentation['decisions'][0]['decision_id'] == presentation['decisions'][2]['decision_id']
    decisions = project(presentation, [[event(presentation,0,1)]])['decisions']
    assert decisions[0]['clean_successes'] == 1 and decisions[2]['reached_observations'] == 0
