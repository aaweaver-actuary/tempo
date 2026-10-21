"""The binding phase keeps application order and one root-owned atomic write."""

import chess
import pytest

from tempo_content.domain import ContentError, canonical_position
from tempo_content.worker import reconcile_binding
from tempo_content.worker import bindings
from tempo_content_database.adapters import store


def binding_case():
    task = {'cursor': 0, 'source_id': 'source-id', 'generation': 7}
    manifest = {'stage_id': 'stage-id', 'counts': {'bindings': 1}}
    binding = {'route_key': 'route', 'policy_key': 'policy', 'ply': 0, 'exercise_key': 'exercise'}
    route = {'artifact_id': 'artifact-id', 'complete': True}
    occurrence = {'position_key': canonical_position(chess.STARTING_FEN),
                  'opponent': False, 'authored_move': 'e2e4'}
    exercise = {'starting_fen': chess.STARTING_FEN,
                'specification': {'type': 'move_line', 'mode': 'single',
                                  'grading_policy': 'reference', 'accepted_lines': [['e2e4']]}}
    return task, manifest, binding, route, occurrence, exercise


def application_operations():
    task, manifest, binding, route, occurrence, exercise = binding_case()
    events = []

    def hook(event, context):
        assert context['task'] is task
        events.append(event)

    def transition(supplied_task, phase, *, state, hook):
        assert supplied_task is task and phase == state == 'complete'
        events.append('transition')
        return {'worked': True, 'status': 'complete', 'phase': 'complete'}

    def read_item(stage_id, kind, index=None, *, key=None):
        assert stage_id == 'stage-id'
        events.append(('item', kind))
        if kind == 'bindings':
            assert index == 0 and key is None
            return binding
        assert kind == 'exercises' and key == 'exercise' and index is None
        return exercise

    def read_target(supplied_task, supplied_binding):
        assert supplied_task is task and supplied_binding is binding
        events.append('target')
        return route, occurrence

    def commit_binding(supplied_task, supplied_binding, supplied_occurrence, accepted, supplied_hook):
        assert supplied_task is task and supplied_binding is binding and supplied_occurrence is occurrence
        assert accepted == ['e2e4'] and supplied_hook is hook
        events.append('commit binding')

    capabilities = dict(transition=transition, read_item=read_item, read_target=read_target,
                        commit_binding=commit_binding, notify=store._hook)
    return events, task, manifest, hook, capabilities


def test_binding_phase_exhaustion_transitions_without_reading_or_validating(monkeypatch):
    events, task, manifest, hook, capabilities = application_operations()
    task['cursor'] = 1

    def unexpected(*arguments):
        raise AssertionError('Exhaustion must not validate a binding')

    monkeypatch.setattr(bindings, 'binding_responses', unexpected)
    assert reconcile_binding(task, manifest, hook, **capabilities) == {
        'worked': True, 'status': 'complete', 'phase': 'complete'}
    assert events == ['transition']


def test_binding_phase_reads_before_validation_and_commits_after_original_hooks(monkeypatch):
    events, task, manifest, hook, capabilities = application_operations()
    original = bindings.binding_responses

    def validate(*arguments):
        events.append('validate')
        return original(*arguments)

    monkeypatch.setattr(bindings, 'binding_responses', validate)
    assert reconcile_binding(task, manifest, hook, **capabilities) == {
        'worked': True, 'status': 'pending', 'phase': 'bindings', 'validated': 'exercise'}
    assert events == [('item', 'bindings'), 'target', ('item', 'exercises'),
                      'after_read', 'validate', 'before_commit', 'commit binding']


@pytest.mark.parametrize('route', [None, {'complete': False}])
def test_binding_phase_still_reads_exercise_before_rejecting_missing_or_incomplete_target(route):
    events, task, manifest, hook, capabilities = application_operations()
    capabilities['read_target'] = lambda *arguments: events.append('target') or (route, None)
    with pytest.raises(ContentError) as error:
        reconcile_binding(task, manifest, hook, **capabilities)
    assert error.value.code == 'incomplete_binding'
    assert events == [('item', 'bindings'), 'target', ('item', 'exercises'), 'after_read']


def test_binding_phase_missing_exercise_propagates_before_read_hook_or_write():
    events, task, manifest, hook, capabilities = application_operations()
    original = capabilities['read_item']
    error = ContentError('source_item_missing', 'Missing exercise')

    def read_item(stage, kind, *arguments, **keywords):
        if kind == 'exercises':
            events.append('missing exercise')
            raise error
        return original(stage, kind, *arguments, **keywords)

    capabilities['read_item'] = read_item
    with pytest.raises(ContentError) as raised:
        reconcile_binding(task, manifest, hook, **capabilities)
    assert raised.value is error
    assert events == [('item', 'bindings'), 'target', 'missing exercise']


@pytest.mark.parametrize('event', ['after_read', 'before_commit'])
def test_binding_phase_hook_failure_cannot_enter_write_section(event):
    events, task, manifest, hook, capabilities = application_operations()
    error = ValueError('Interrupted phase')

    def reject_at_boundary(supplied_hook, supplied_event, **context):
        assert supplied_hook is hook and context['task'] is task
        events.append(supplied_event)
        if supplied_event == event:
            raise error

    capabilities['notify'] = reject_at_boundary
    with pytest.raises(ValueError) as raised:
        reconcile_binding(task, manifest, hook, **capabilities)
    assert raised.value is error
    assert 'commit binding' not in events
    assert events[-1] == event


def test_existing_binding_entry_point_wires_owner_operations_without_opening_a_database():
    events, task, manifest, hook, capabilities = application_operations()
    service = store.ContentService.__new__(store.ContentService)
    service._transition = capabilities['transition']
    service._item = capabilities['read_item']
    service._binding_target = capabilities['read_target']
    service._commit_binding = capabilities['commit_binding']
    assert service._binding_slice(task, manifest, hook) == {
        'worked': True, 'status': 'pending', 'phase': 'bindings', 'validated': 'exercise'}
    assert events == [('item', 'bindings'), 'target', ('item', 'exercises'),
                      'after_read', 'before_commit', 'commit binding']
