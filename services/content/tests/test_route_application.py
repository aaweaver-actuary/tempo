"""Missing route-phase branches around the moved application/storage boundary."""
from contextlib import contextmanager

import chess
import pytest

from tempo_content.domain import ContentError
from tempo_content.worker import routes
from tempo_content_database.adapters.store import ContentService


def route_case(artifact=None, *, starting_fen=chess.STARTING_FEN, current=None):
    task = {'cursor': 0, 'source_id': 'source-id', 'generation': 3}
    manifest = {'stage_id': 'stage-id', 'counts': {'memberships': 1}}
    membership = {'route_key': 'route', 'repertoire_key': 'repertoire'}
    route = {'starting_fen': starting_fen, 'moves': []}
    events = []

    class Transaction:
        def execute(self, *arguments):
            events.append('database operation')
            return self

        def fetchone(self):
            return current

    @contextmanager
    def section():
        events.append('begin')
        yield Transaction()
        events.append('commit')

    def read_item(stage, kind, *arguments, **keywords):
        assert stage == 'stage-id'
        events.append(kind)
        return {'memberships': membership, 'routes': route,
                'repertoires': {'learner_color': 'white'}}[kind]

    def read_artifact(*arguments):
        events.append('artifact read')
        return artifact

    def hook(event, context):
        if 'task' in context:
            assert context['task'] is task
        events.append(event)

    service = ContentService.__new__(ContentService)
    service._item = read_item
    service._one = read_artifact
    service._transaction = section
    service._fence = lambda *arguments: events.append('fence')
    service._attach_route = lambda *arguments: events.append('attach')
    service._save_occurrences = lambda *arguments: events.append('save occurrences')
    service._advance = lambda *arguments, **keywords: events.append(('advance', keywords.get('cursor')))
    return service, task, manifest, hook, events


def test_exhausted_route_phase_uses_original_transition_without_any_input_or_cache_read():
    service, task, manifest, hook, events = route_case()
    task['cursor'] = 1

    def transition(supplied_task, phase, *, hook):
        assert supplied_task is task and phase == 'exercises'
        events.append('transition')
        return {'worked': True, 'status': 'pending', 'phase': phase}

    service._transition = transition
    assert service._route_slice(task, manifest, hook) == {
        'worked': True, 'status': 'pending', 'phase': 'exercises'}
    assert events == ['transition']


@pytest.mark.parametrize('valid', [True, False])
def test_new_route_validates_after_read_hook_before_original_registration_section(monkeypatch, valid):
    service, task, manifest, hook, events = route_case(starting_fen=chess.STARTING_FEN if valid else 'invalid-fen')
    original = routes.canonical_position

    def validate(fen):
        events.append('validate')
        return original(fen)

    monkeypatch.setattr(routes, 'canonical_position', validate)
    if valid:
        result = service._route_slice(task, manifest, hook)
        assert result['worked'] is True and result['plies'] == 0 and result['registered']
        assert events == ['memberships', 'routes', 'repertoires', 'artifact read', 'after_read',
                          'validate', 'before_commit', 'begin', 'fence', 'database operation',
                          'database operation', 'after_artifact_lock', 'attach', ('advance', None),
                          'after_writes', 'commit']
    else:
        with pytest.raises(ContentError) as error:
            service._route_slice(task, manifest, hook)
        assert error.value.code == 'invalid_fen'
        assert events == ['memberships', 'routes', 'repertoires', 'artifact read', 'after_read', 'validate']


def test_route_epoch_race_reports_original_artifact_advanced_without_saving_occurrences():
    artifact = {'cursor': 0, 'next_fen': chess.STARTING_FEN, 'complete': True, 'epoch': 1}
    service, task, manifest, hook, events = route_case(artifact, current={**artifact, 'epoch': 2})
    assert service._route_slice(task, manifest, hook) == {
        'worked': False, 'status': 'artifact_advanced', 'phase': 'routes'}
    assert events == ['memberships', 'routes', 'repertoires', 'artifact read', 'after_read',
                      'before_commit', 'begin', 'fence', 'database operation', ('advance', None), 'commit']
