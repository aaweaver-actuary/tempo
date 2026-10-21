"""Missing replay/precondition boundaries around the moved publication workflows."""
import hashlib
import json
from pathlib import Path

import pytest
from tempo_content_publication_contracts.v1 import Publication, serialize_contract

from tempo_content.domain import ContentError, build_publication
from tempo_content.models import SourceSnapshot
from tempo_content_database.adapters.store import ContentService


def publication():
    snapshot = SourceSnapshot.model_validate(json.loads(
        (Path(__file__).resolve().parents[1] / 'fixtures/source.json').read_text()))
    return build_publication(snapshot, 3, 7, '2026-10-16T12:00:00Z',
                             {exercise.key: 1 for exercise in snapshot.exercises})


def build_case(state, *, replay=False, count=1):
    value = publication()
    events = []
    source = {'id': 'source-id', 'requested_revision': 3, 'publication_id': value.publication_id}
    task = {'generation': 7, 'revision': 3, 'state': state}
    service = ContentService.__new__(ContentService)
    service._source = lambda key: events.append('source') or source
    service._publication_task = lambda *arguments: events.append('task') or task
    service._publication_payload = lambda *arguments: events.append('payload') or (
        {'payload': serialize_contract(value)} if replay else None)
    service._manifest = lambda *arguments: events.append('manifest') or {'counts': {'exercises': count}}

    def unexpected(*arguments):
        pytest.fail('Early replay/precondition cannot reserve, hydrate or persist')

    service._reserve_publication_build = unexpected
    service._snapshot = unexpected
    service._exercise_revisions = unexpected
    service.store_publication = unexpected
    return service, events, value


def test_publication_build_replay_precedes_task_state_check_and_all_build_work():
    service, events, value = build_case('pending', replay=True)
    actual = service.publication_build('source-key', hook=lambda *arguments: pytest.fail('Replay cannot emit build hook'))
    assert serialize_contract(actual) == serialize_contract(value)
    assert events == ['source', 'task', 'payload']


@pytest.mark.parametrize('state,count,code,events_expected', [
    ('pending', 1, 'inventory_incomplete', ['source', 'task', 'payload']),
    ('complete', 0, 'retirement_required', ['source', 'task', 'payload', 'manifest']),
])
def test_publication_build_preconditions_reject_before_reservation_or_hydration(state, count, code, events_expected):
    service, events, value = build_case(state, count=count)
    with pytest.raises(ContentError) as error:
        service.publication_build('source-key')
    assert error.value.code == code
    assert events == events_expected


def test_publication_admission_replay_still_invokes_original_atomic_conflict_guard():
    value = publication()
    serialized = serialize_contract(value)
    events = []
    provenance = {'reviewed': 'provenance'}
    error = ContentError('publication_conflict', 'Simulated original immutable guard')
    service = ContentService.__new__(ContentService)
    service._publication_payload = lambda *arguments: events.append('replay') or {'payload': serialized}

    def unexpected(*arguments):
        pytest.fail('Replay cannot reconstruct authoritative inputs')

    def commit(parsed, source_id, supplied_provenance, payload, digest, expected_payload, expected_provenance, hook):
        assert isinstance(parsed, Publication) and serialize_contract(parsed) == serialized
        assert source_id == 'source-id' and supplied_provenance is provenance
        assert payload == serialized and digest == hashlib.sha256(serialized.encode()).hexdigest()
        assert expected_payload is expected_provenance is hook is None
        events.append('atomic guard')
        raise error

    service._manifest = service._exercise_revisions = service._snapshot = unexpected
    service._commit_publication = commit
    with pytest.raises(ContentError) as raised:
        service.store_publication(value.model_dump(mode='json'), 'source-id', provenance)
    assert raised.value is error
    assert events == ['replay', 'atomic guard']
