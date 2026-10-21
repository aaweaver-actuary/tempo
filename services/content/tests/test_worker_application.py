"""Content reconciliation keeps ordered semantic operations and failure authority."""

import pytest

from tempo_content.domain import ContentError
from tempo_content.worker import reconcile_once
from tempo_content_database.adapters.store import ContentService


def operations(phase='routes', *, error=None, claimed=True, failure_authorized=True):
    events = []
    task = {'source_id': 'source-id', 'revision': 3, 'phase': phase, 'generation': 4}
    manifest = {'stage_id': 'stage-id'}
    hook = object()
    original_hook = hook

    def claim_task(source_key, supplied_hook):
        assert source_key == 'source-key' and supplied_hook is hook
        events.append('claim')
        return task if claimed else None

    def collect(*, hook):
        assert hook is original_hook
        events.append('collect')
        return {'worked': False, 'status': 'idle', 'phase': 'collect'}

    def read_manifest(source_id, revision):
        assert (source_id, revision) == ('source-id', 3)
        events.append('manifest')
        return manifest

    def route_slice(supplied_task, supplied_manifest, supplied_hook):
        assert supplied_task is task and supplied_manifest is manifest and supplied_hook is hook
        events.append('routes')
        if error is not None:
            raise error
        return {'worked': True, 'status': 'pending', 'phase': 'routes'}

    def exercise_slice(supplied_task, supplied_manifest, supplied_hook):
        assert supplied_task is task and supplied_manifest is manifest and supplied_hook is hook
        events.append('exercises')
        return {'worked': True, 'status': 'pending', 'phase': 'exercises'}

    def binding_slice(supplied_task, supplied_manifest, supplied_hook):
        assert supplied_task is task and supplied_manifest is manifest and supplied_hook is hook
        events.append('bindings')
        return {'worked': True, 'status': 'pending', 'phase': 'bindings'}

    def fail_task(supplied_task, code, supplied_error):
        assert supplied_task is task
        events.append(('fail', code, supplied_error))
        return failure_authorized

    return events, task, hook, dict(claim_task=claim_task, collect=collect, read_manifest=read_manifest,
                                    route_slice=route_slice, exercise_slice=exercise_slice,
                                    binding_slice=binding_slice, fail_task=fail_task)


@pytest.mark.parametrize('phase', ['routes', 'exercises', 'bindings'])
def test_worker_claims_then_reads_manifest_then_invokes_only_the_current_slice(phase):
    events, task, hook, capabilities = operations(phase)
    assert reconcile_once('source-key', hook, **capabilities) == {
        'worked': True, 'status': 'pending', 'phase': phase}
    assert events == ['claim', 'manifest', phase]


def test_worker_idle_claim_falls_back_to_collection_without_reading_manifest():
    events, task, hook, capabilities = operations(claimed=False)
    assert reconcile_once('source-key', hook, **capabilities) == {
        'worked': False, 'status': 'idle', 'phase': 'collect'}
    assert events == ['claim', 'collect']


@pytest.mark.parametrize('operation,expected_events', [
    ('claim_task', []), ('read_manifest', ['claim']),
])
def test_worker_claim_and_manifest_failures_remain_outside_validation_failure_scope(operation, expected_events):
    events, task, hook, capabilities = operations()
    error = ContentError('missing_input', 'Missing accepted input')

    def reject(*arguments):
        raise error

    capabilities[operation] = reject
    with pytest.raises(ContentError) as raised:
        reconcile_once('source-key', hook, **capabilities)
    assert raised.value is error
    assert events == expected_events


@pytest.mark.parametrize('error,code', [
    (ValueError('Malformed content'), 'invalid_content'),
    (ContentError('invalid_binding', 'Wrong binding'), 'invalid_binding'),
])
def test_worker_validation_failure_persists_original_error_before_reporting_failure(error, code):
    events, task, hook, capabilities = operations(error=error)
    assert reconcile_once('source-key', hook, **capabilities) == {
        'worked': True, 'status': 'failed', 'phase': 'routes',
        'error': {'code': code, 'message': str(error)}}
    assert events == ['claim', 'manifest', 'routes', ('fail', code, error)]


def test_worker_unknown_phase_uses_the_existing_actionable_failure():
    events, task, hook, capabilities = operations('unknown')
    assert reconcile_once('source-key', hook, **capabilities) == {
        'worked': True, 'status': 'failed', 'phase': 'unknown',
        'error': {'code': 'unknown_task_phase', 'message': 'unknown'}}
    assert events[:2] == ['claim', 'manifest']
    assert events[2][0:2] == ('fail', 'unknown_task_phase')


def test_worker_stale_generation_does_not_attempt_failure_write():
    events, task, hook, capabilities = operations(error=ContentError('stale_generation', 'Lease lost'))
    assert reconcile_once('source-key', hook, **capabilities) == {
        'worked': False, 'status': 'stale_generation', 'phase': 'routes'}
    assert events == ['claim', 'manifest', 'routes']


def test_worker_lost_failure_authority_reports_stale_without_a_failed_response():
    error = ValueError('Malformed content')
    events, task, hook, capabilities = operations(error=error, failure_authorized=False)
    assert reconcile_once('source-key', hook, **capabilities) == {
        'worked': False, 'status': 'stale_generation', 'phase': 'routes'}
    assert events == ['claim', 'manifest', 'routes', ('fail', 'invalid_content', error)]


def test_worker_unexpected_failure_propagates_without_marking_generation_failed():
    error = RuntimeError('Storage unavailable')
    events, task, hook, capabilities = operations(error=error)
    with pytest.raises(RuntimeError) as raised:
        reconcile_once('source-key', hook, **capabilities)
    assert raised.value is error
    assert events == ['claim', 'manifest', 'routes']


def test_worker_public_entry_wires_existing_methods_without_opening_a_database():
    events, task, hook, capabilities = operations()
    service = ContentService.__new__(ContentService)
    for name, operation in {
        '_claim': 'claim_task', 'collect_once': 'collect', '_manifest': 'read_manifest',
        '_route_slice': 'route_slice', '_exercise_slice': 'exercise_slice',
        '_binding_slice': 'binding_slice', '_fail_task': 'fail_task',
    }.items():
        setattr(service, name, capabilities[operation])
    assert service.work_once('source-key', hook) == {'worked': True, 'status': 'pending', 'phase': 'routes'}
    assert events == ['claim', 'manifest', 'routes']
