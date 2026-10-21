"""Moved route/publication computation keeps its facts and storage call points."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

import chess
import pytest
from tempo_content_publication_contracts.v1 import serialize_contract

from tempo_content.domain import ContentError, inventory_segment
from tempo_content.models import SourceSnapshot
from tempo_content.policy import inventory_report
from tempo_content.publication import prepare_publication, publication_authority
from tempo_content.publication import preparation
from tempo_content.worker import route_segment
from tempo_content.worker import routes
from tempo_content_database.adapters import store


NOW = '2026-10-16T12:00:00Z'


def source():
    return SourceSnapshot.model_validate(json.loads(
        (Path(__file__).resolve().parents[1] / 'fixtures/source.json').read_text()))


def publication_inputs():
    snapshot = source()
    revisions = {exercise.key: 2 for exercise in snapshot.exercises}
    facts = dict(source_id='source-id', source_key=snapshot.source_key,
                 source_digest='source-digest', inventory_version='inventory-version')
    return snapshot, revisions, facts


@pytest.mark.parametrize('state,count,last_complete,reported,stale', [
    ('complete', 0, 3, 'retirement_required', False),
    ('complete', 1, 2, 'complete', True),
    ('pending', 0, 2, 'pending', True),
    ('failed', 0, 2, 'failed', True),
])
def test_inventory_projection_preserves_retirement_status_staleness_and_row_precedence(
    state, count, last_complete, reported, stale,
):
    source = {'id': 'source-id', 'requested_revision': 3,
              'last_complete_revision': last_complete, 'state': 'source-value'}
    task = {'source_id': 'source-id', 'generation': 7, 'revision': 3, 'state': state}
    assert inventory_report(source, task, count) == {
        **source, **task, 'state': reported, 'status': reported, 'stale': stale}


def test_route_preparation_keeps_sixteen_ply_windows_and_terminal_inventory():
    route = {'moves': ['g1f3', 'g8f6', 'f3g1', 'f6g8'] * 5}
    artifact = {'cursor': 0, 'next_fen': chess.STARTING_FEN, 'complete': False}
    original = deepcopy((route, artifact))
    first, moves, terminal, occurrences, next_fen = route_segment(route, artifact, 'white')
    assert (first, len(moves), terminal, len(occurrences)) == (0, 16, False, 16)
    resumed = {'cursor': 16, 'next_fen': next_fen, 'complete': False}
    second, tail, ended, final_occurrences, end_fen = route_segment(route, resumed, 'white')
    assert (second, len(tail), ended, len(final_occurrences)) == (16, 4, True, 5)
    whole, whole_fen = inventory_segment(chess.STARTING_FEN, route['moves'], 'white')
    assert occurrences + final_occurrences == whole and end_fen == whole_fen
    assert (route, artifact) == original


def test_completed_route_reuse_does_not_revalidate_or_traverse_cached_facts():
    route = {'moves': ['intentionally-not-a-move']}
    artifact = {'cursor': 1, 'next_fen': 'opaque-cached-fen', 'complete': True}
    assert route_segment(route, artifact, 'black') == (1, [], True, [], 'opaque-cached-fen')


def test_publication_preparation_keeps_exact_publication_and_authoritative_provenance():
    snapshot, revisions, facts = publication_inputs()
    publication, provenance = prepare_publication(snapshot, 3, 7, NOW, revisions, **facts)
    expected_payload, expected_provenance = publication_authority(
        snapshot, publication, revisions, **{key: value for key, value in facts.items() if key != 'source_key'})
    assert expected_payload == serialize_contract(publication)
    assert expected_provenance == provenance == {
        **facts, 'source_revision': 3, 'snapshot': snapshot.model_dump(mode='json')}
    assert {definition.exercise_revision for definition in publication.definitions} == {2}
    assert (publication.source_revision, publication.publication_generation, publication.built_at) == (3, 7, NOW)


def test_publication_missing_revision_count_is_rejected_before_build_or_serialization(monkeypatch):
    snapshot, revisions, facts = publication_inputs()

    def unexpected(*arguments):
        raise AssertionError('Incomplete revisions must precede construction and serialization')

    monkeypatch.setattr(preparation, 'build_publication', unexpected)
    monkeypatch.setattr(preparation, 'serialize_contract', unexpected)
    with pytest.raises(ContentError) as error:
        prepare_publication(snapshot, 3, 7, NOW, {}, **facts)
    assert error.value.code == 'incomplete_exercises'


def test_publication_preparation_retains_original_serialization_and_decode_pass(monkeypatch):
    snapshot, revisions, facts = publication_inputs()
    events = []

    class Encoded:
        def decode(self):
            events.append('decode')
            return 'serialized-publication'

    def serialize(publication):
        events.append(('serialize', publication.publication_generation))
        return Encoded()

    monkeypatch.setattr(preparation, 'serialize_contract', serialize)
    publication, provenance = prepare_publication(snapshot, 3, 7, NOW, revisions, **facts)
    assert events == [('serialize', 7), 'decode']
    assert provenance['source_revision'] == publication.source_revision


def test_route_preparation_stays_between_read_and_commit_hooks_without_a_pool_lease(monkeypatch):
    events = []
    task = {'cursor': 0, 'source_id': 'source-id', 'generation': 3}
    membership = {'route_key': 'route', 'repertoire_key': 'repertoire'}
    route = {'starting_fen': chess.STARTING_FEN, 'moves': []}
    artifact = {'cursor': 0, 'next_fen': chess.STARTING_FEN, 'complete': True, 'epoch': 1}

    class Transaction:
        def execute(self, *arguments):
            events.append('database operation')
            return self

        def fetchone(self):
            return artifact

    @contextmanager
    def section():
        events.append('begin')
        yield Transaction()
        events.append('commit')

    def compute(supplied_route, supplied_artifact, color):
        assert supplied_route is route and supplied_artifact is artifact and color == 'white'
        events.append('compute')
        return 0, [], True, [], chess.STARTING_FEN

    service = store.ContentService.__new__(store.ContentService)
    service._item = lambda stage, kind, *arguments, **keywords: {
        'memberships': membership, 'routes': route, 'repertoires': {'learner_color': 'white'}}[kind]
    service._one = lambda *arguments: artifact
    service._transaction = section
    service._fence = lambda *arguments: events.append('fence')
    service._attach_route = lambda *arguments: events.append('attach')
    service._save_occurrences = lambda *arguments: events.append('save occurrences')
    service._advance = lambda *arguments, **keywords: events.append(('advance', keywords['cursor']))
    monkeypatch.setattr(routes, 'route_segment', compute)
    result = service._route_slice(task, {'stage_id': 'stage', 'counts': {'memberships': 1}},
                                  lambda event, context: events.append(event))
    assert result['reused'] is True
    assert events == ['after_read', 'compute', 'before_commit', 'begin', 'fence',
                      'database operation', 'after_artifact_lock', 'attach', 'save occurrences',
                      'database operation', ('advance', 1), 'after_writes', 'commit']


def test_publication_preparation_stays_after_build_reservation_and_before_commit_hook(monkeypatch):
    events = []
    source = {'id': 'source-id', 'requested_revision': 3, 'publication_id': 'publication-id'}
    task = {'revision': 3, 'generation': 7, 'state': 'complete'}
    manifest = {'counts': {'exercises': 1}, 'digest': 'source-digest'}
    snapshot, revisions, facts = publication_inputs()
    publication, provenance = object(), {'provenance': 'unchanged'}

    class Transaction:
        def execute(self, *arguments):
            events.append('database operation')
            return self

        def fetchone(self):
            return {'built_at': NOW}

    @contextmanager
    def section():
        events.append('begin')
        yield Transaction()
        events.append('commit')

    def compute(supplied_snapshot, revision, generation, built_at, supplied_revisions, **supplied_facts):
        assert supplied_snapshot is snapshot and supplied_revisions is revisions
        assert (revision, generation, built_at) == (3, 7, NOW)
        assert supplied_facts == {**facts, 'source_key': 'source-key', 'inventory_version': store.INVENTORY_VERSION}
        events.append('compute')
        return publication, provenance

    def save(supplied_publication, source_id, supplied_provenance, hook):
        assert supplied_publication is publication and supplied_provenance is provenance and source_id == 'source-id'
        events.append('store publication')

    service = store.ContentService.__new__(store.ContentService)
    service._source = lambda key: source
    reads = iter([task, None])
    service._one = lambda *arguments: next(reads)
    service._manifest = lambda *arguments: manifest
    service._transaction = section
    service._lock_source = lambda *arguments: source
    service._snapshot = lambda supplied: events.append('snapshot') or snapshot
    service._exercise_revisions = lambda *arguments: events.append('revisions') or revisions
    service.store_publication = save
    monkeypatch.setattr(store, 'prepare_publication', compute)
    assert service.publication_build('source-key', lambda event, context: events.append(event)) is publication
    assert events == ['begin', 'database operation', 'database operation', 'commit',
                      'snapshot', 'revisions', 'compute', 'before_commit', 'store publication']


def test_publication_authority_reconstruction_stays_before_original_write_transaction(monkeypatch):
    snapshot, revisions, facts = publication_inputs()
    publication, provenance = prepare_publication(snapshot, 3, 7, NOW, revisions, **facts)
    manifest = {'digest': facts['source_digest']}
    events = []

    class ReachedWriteSection(Exception):
        pass

    @contextmanager
    def section():
        events.append('begin')
        raise ReachedWriteSection
        yield

    def reconstruct(supplied_snapshot, supplied_publication, supplied_revisions, **supplied_facts):
        assert supplied_snapshot is snapshot and supplied_publication is publication and supplied_revisions is revisions
        assert supplied_facts == {key: value for key, value in {**facts, 'inventory_version': store.INVENTORY_VERSION}.items() if key != 'source_key'}
        events.append('authority')
        return serialize_contract(publication), provenance

    service = store.ContentService.__new__(store.ContentService)
    service._one = lambda *arguments: events.append('replay') or None
    service._manifest = lambda *arguments: events.append('manifest') or manifest
    service._exercise_revisions = lambda *arguments: events.append('revisions') or revisions
    service._snapshot = lambda *arguments: events.append('snapshot') or snapshot
    service._transaction = section
    monkeypatch.setattr(store, 'publication_authority', reconstruct)
    with pytest.raises(ReachedWriteSection):
        service.store_publication(publication, 'source-id', provenance)
    assert events == ['replay', 'manifest', 'revisions', 'snapshot', 'authority', 'begin']
