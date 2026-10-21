"""Missing source replay/page/seal/hook/admission boundaries after the move."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tempo_content.models import SourceSnapshot
from tempo_content_database.adapters.store import ContentService, KINDS


def source(*, paged=False):
    payload = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/source.json').read_text())
    if paged:
        original = payload['exercises'][0]
        for index in range(65 - len(payload['exercises'])):
            exercise = deepcopy(original)
            exercise['key'] = 'extra-exercise-' + str(index)
            exercise['decision_key'] = 'extra-decision-' + str(index)
            payload['exercises'].append(exercise)
    return SourceSnapshot.model_validate(payload)


def source_case(snapshot, *, replay=None, admission=(4, 8, 'pending', False), interrupt=False):
    events = []
    staged = []
    service = ContentService.__new__(ContentService)
    service.workspace_id = snapshot.workspace_id

    def preflight(source_id, digest, expected_revision):
        assert expected_revision == 2 and len(digest) == 64
        events.append('preflight')
        return replay

    def page(stage_id, kind, payload, offset):
        events.append(('page', kind, offset))
        staged.append((stage_id, kind, payload, offset))

    def seal(stage_id, source_id, digest, header, counts):
        assert all(kind not in header for kind in KINDS)
        if staged:
            payload = staged[0][2]
            assert all(item[2] is payload and item[0] == stage_id for item in staged)
            assert counts == {kind: len(payload[kind]) for kind in KINDS}
        events.append('seal')

    def hook(event, context):
        assert event == 'after_staging' and set(context) == {'source_id', 'stage_id'}
        events.append(event)
        if interrupt:
            raise ValueError('Interrupted after staging')

    def admit(parsed, source_id, publication_id, digest, stage_id, expected_revision, supplied_hook, operation_id):
        assert parsed is snapshot and expected_revision == 2 and supplied_hook is hook and operation_id == 'operation-id'
        assert len(digest) == 64
        events.append('admit')
        return admission

    service._preflight_source = preflight
    service._stage_source_page = page
    service._seal_source_manifest = seal
    service._admit_source = admit
    return service, hook, events, staged


def test_source_preflight_replay_skips_all_staging_hooks_and_admission():
    service, hook, events, staged = source_case(source(), replay={
        'requested_revision': 3, 'generation': 7, 'state': 'complete'})
    result = service.load_source(source(), 2, hook=hook, operation_id='operation-id')
    assert {key: value for key, value in result.items() if key != 'source_id'} == {
        'source_revision': 3, 'generation': 7, 'status': 'complete', 'replayed': True}
    assert result['source_id'] and events == ['preflight'] and staged == []


@pytest.mark.parametrize('admission', [(4, 8, 'pending', False), (3, 7, 'working', True)])
def test_source_pages_seal_hook_admission_keep_order_and_original_receipt(admission):
    snapshot = source(paged=True)
    service, hook, events, staged = source_case(snapshot, admission=admission)
    result = service.load_source(snapshot, 2, hook=hook, operation_id='operation-id')
    assert result == {'source_id': result['source_id'], 'source_revision': admission[0],
                      'generation': admission[1], 'status': admission[2], 'replayed': admission[3]}
    expected_pages = [('page', kind, offset) for kind in KINDS
                      for offset in range(0, len(getattr(snapshot, kind)), 64)]
    assert ('page', 'exercises', 64) in expected_pages
    assert events == ['preflight', *expected_pages, 'seal', 'after_staging', 'admit']


def test_source_after_staging_failure_cannot_reach_admission():
    snapshot = source()
    service, hook, events, staged = source_case(snapshot, interrupt=True)
    with pytest.raises(ValueError, match='Interrupted after staging'):
        service.load_source(snapshot, 2, hook=hook, operation_id='operation-id')
    assert events[-2:] == ['seal', 'after_staging'] and 'admit' not in events
    assert staged
