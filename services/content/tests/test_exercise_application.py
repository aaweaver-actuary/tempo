"""Missing exercise-phase order and continuation/result boundary cases."""
import chess
from pydantic import ValidationError
import pytest

from tempo_content.models import SourceExercise
from tempo_content.worker import exercises
from tempo_content_database.adapters.store import ContentService


def exercise_case():
    task = {'cursor': 0, 'exercise_state': None}
    manifest = {'stage_id': 'stage-id', 'counts': {'exercises': 1}}
    payload = SourceExercise(key='exercise', decision_key='decision', policy_key='policy',
                             starting_fen=chess.STARTING_FEN, prompt='Choose a response',
                             specification={'type': 'move_line', 'mode': 'single',
                                            'grading_policy': 'reference',
                                            'accepted_lines': [['e2e4']]}).model_dump(mode='json')
    events = []

    def hook(event, context):
        assert context['task'] is task
        events.append(event)

    def read_item(stage, kind, index=None, *, key=None):
        assert stage == 'stage-id'
        events.append(kind)
        if kind == 'exercises':
            assert index == 0 and key is None
            return payload
        if kind == 'policies':
            assert index is None and key == 'policy'
            return {'repertoire_key': 'repertoire'}
        assert kind == 'repertoires' and index is None and key == 'repertoire'
        return {'learner_color': 'white'}

    service = ContentService.__new__(ContentService)
    service._item = read_item
    return service, task, manifest, payload, hook, events


def test_exhausted_exercise_phase_transitions_without_reading_another_payload():
    service, task, manifest, payload, hook, events = exercise_case()
    task['cursor'] = 1

    def transition(supplied_task, phase, *, hook):
        assert supplied_task is task and phase == 'bindings'
        events.append('transition')
        return {'worked': True, 'status': 'pending', 'phase': phase}

    service._transition = transition
    service._commit_exercise = lambda *arguments: pytest.fail('Exhaustion cannot commit another exercise')
    assert service._exercise_slice(task, manifest, hook) == {
        'worked': True, 'status': 'pending', 'phase': 'bindings'}
    assert events == ['transition']


def test_exercise_payload_is_parsed_before_dependent_reads_or_hooks():
    service, task, manifest, payload, hook, events = exercise_case()
    payload.pop('key')
    service._transition = lambda *arguments, **keywords: pytest.fail('Unexpected transition')
    service._commit_exercise = lambda *arguments: pytest.fail('Invalid payload cannot commit')
    with pytest.raises(ValidationError):
        service._exercise_slice(task, manifest, hook)
    assert events == ['exercises']


@pytest.mark.parametrize('continuation', [None, {}])
def test_exercise_phase_keeps_read_validation_hook_commit_order_and_result_mapping(monkeypatch, continuation):
    service, task, manifest, payload, hook, events = exercise_case()

    def validate(exercise, state, learner_color):
        assert isinstance(exercise, SourceExercise) and state is None and learner_color == 'white'
        events.append('validate')
        return continuation

    def commit(supplied_task, supplied_payload, exercise, state, supplied_hook):
        assert supplied_task is task and supplied_payload is payload and isinstance(exercise, SourceExercise)
        assert state is continuation and supplied_hook is hook
        events.append('commit exercise')
        return continuation is None

    service._transition = lambda *arguments, **keywords: pytest.fail('Unexpected transition')
    service._commit_exercise = commit
    monkeypatch.setattr(exercises, 'exercise_continuation', validate)
    result = service._exercise_slice(task, manifest, hook)
    assert result == ({'worked': True, 'status': 'pending', 'phase': 'exercises', 'validated': 'exercise'}
                      if continuation is None else {'worked': True, 'status': 'pending', 'phase': 'exercises', 'validation_pending': True})
    assert events == ['exercises', 'policies', 'repertoires', 'after_read', 'validate',
                      'before_commit', 'commit exercise']
