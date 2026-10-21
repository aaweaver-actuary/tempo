"""Existing Content decisions accept source/row facts without storage effects."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import chess
import pytest

from tempo_content.domain import ContentError, canonical_position, canonical_source, stable_id
from tempo_content.models import SourceExercise, SourceSnapshot
from tempo_content.policy import (
    binding_responses, coverage_reply, exercise_continuation, prepare_source, publication_report,
)


def source():
    return SourceSnapshot.model_validate(json.loads(
        (Path(__file__).resolve().parents[1] / 'fixtures/source.json').read_text()))


def exercise():
    return SourceExercise(key='move', decision_key='decision', policy_key='policy',
                          starting_fen=chess.STARTING_FEN, prompt='Choose a response',
                          specification={'type': 'move_line', 'mode': 'single',
                                         'grading_policy': 'reference',
                                         'accepted_lines': [['e2e4'], ['d2d4']]})


def test_source_preparation_retains_canonical_payload_digest_and_stable_identities():
    snapshot = source()
    hashed = []

    def digest_text(text):
        hashed.append(text)
        return hashlib.sha256(text.encode()).hexdigest()

    parsed, payload, digest, source_id, publication_id, stage_id = prepare_source(
        snapshot, 0, snapshot.workspace_id, digest_text=digest_text)
    assert parsed is snapshot
    assert hashed == [canonical_source(snapshot)]
    assert payload == json.loads(hashed[0])
    assert source_id == stable_id(snapshot.workspace_id, snapshot.source_key, 'source', snapshot.source_key)
    assert publication_id == stable_id(snapshot.workspace_id, snapshot.source_key, 'publication', 'source')
    assert stage_id == stable_id(snapshot.workspace_id, snapshot.source_key, 'stage', digest)


@pytest.mark.parametrize('revision', [True, -1, '0'])
def test_source_revision_rejection_precedes_canonicalization_or_hashing(revision):
    calls = []
    with pytest.raises(ContentError) as error:
        prepare_source({}, revision, 'workspace', digest_text=lambda text: calls.append(text))
    assert error.value.code == 'invalid_source_revision'
    assert calls == []


def test_source_workspace_rejection_precedes_hashing():
    calls = []
    with pytest.raises(ContentError) as error:
        prepare_source(source(), 0, 'different-workspace', digest_text=lambda text: calls.append(text))
    assert error.value.code == 'workspace_mismatch'
    assert calls == []


def test_exercise_policy_retains_execution_validation_and_learner_turn_rule():
    assert exercise_continuation(exercise(), None, 'white') is None
    with pytest.raises(ContentError) as error:
        exercise_continuation(exercise(), None, 'black')
    assert error.value.code == 'exercise_learner_color_mismatch'


@pytest.mark.parametrize('mutation,code', [
    ('incomplete', 'incomplete_binding'), ('opponent', 'invalid_binding'),
    ('position', 'invalid_binding'), ('policy', 'route_policy_conflict'),
])
def test_binding_policy_retains_route_completion_position_and_authored_response_guards(mutation, code):
    payload = exercise().model_dump(mode='json')
    route = {'complete': mutation != 'incomplete'}
    occurrence = {'opponent': mutation == 'opponent',
                  'position_key': 'different' if mutation == 'position' else canonical_position(chess.STARTING_FEN),
                  'authored_move': 'g1f3' if mutation == 'policy' else 'e2e4'}
    with pytest.raises(ContentError) as error:
        binding_responses(payload, {}, route, occurrence)
    assert error.value.code == code


def test_binding_policy_preserves_accepted_alternatives_without_mutating_supplied_facts():
    payload = exercise().model_dump(mode='json')
    original = deepcopy(payload)
    occurrence = {'opponent': False, 'position_key': canonical_position(chess.STARTING_FEN),
                  'authored_move': 'e2e4'}
    assert binding_responses(payload, {}, {'complete': True}, occurrence) == ['e2e4', 'd2d4']
    assert payload == original


@pytest.mark.parametrize('state,complete,count,error,validation,errors,compatibility', [
    ('complete', True, 1, None, 'validated', [], 'requires_execution_adapter'),
    ('failed', False, 1, {'code': 'invalid_content'}, 'rejected', ['invalid_content'], 'requires_execution_adapter'),
    ('pending', False, 0, None, 'retryable_failure', ['retirement_required'], 'requires_execution_adapter'),
    ('pending', False, 129, None, 'retryable_failure', ['inventory_incomplete'], 'definition_limit_exceeded'),
])
def test_publication_report_retains_validation_errors_and_existing_compatibility_limit(
    state, complete, count, error, validation, errors, compatibility,
):
    status = {'last_complete_revision': 2 if complete else 1, 'requested_revision': 2,
              'state': state, 'error': error, 'publication_id': stable_id('workspace', 'source', 'publication', 'source'),
              'generation': 3, 'revision': 2, 'last_complete_generation': 3 if complete else 2,
              'stale': not complete}
    result = publication_report('workspace', status, count)
    assert result['validation']['status'] == validation
    assert result['validation']['errors'] == errors
    assert result['learning_compatibility'] == {
        'definition_count': count, 'bundle_limit': 128, 'status': compatibility, 'activation': 'not_requested'}
    assert result['stale'] is (not complete)


@pytest.mark.parametrize('direct,transposed,status,supported', [
    ({'present': 1}, {'present': 1}, 'covered', False),
    (None, {'present': 1}, 'transposition_supported', True),
    (None, None, 'uncovered', False),
])
def test_coverage_labels_keep_direct_authority_ahead_of_transposition(direct, transposed, status, supported):
    reply = {'move_uci': 'e7e5', 'resulting_position_key': 'position'}
    result = coverage_reply(reply, True, direct, transposed)
    assert result == {**reply, 'legal': True, 'authored': True, 'covered': bool(direct),
                      'transposition_supported': supported, 'status': status}
    assert 'status' not in reply
