"""Approval-bound Learning delivery keeps authored scope, without optional replies."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import chess
import pytest

from tempo_content.bridge.application import BridgeApplication, ImportManifest, canonical, digest
from tempo_content.domain import ContentError, build_publication, canonical_source
from tempo_content.models import SourceSnapshot


def export_case():
    board = chess.Board()
    board.push_uci('e2e4')
    source = SourceSnapshot.model_validate({
        'schema_version': 1, 'workspace_id': 'profile-test', 'source_key': 'opening', 'title': 'Authored source',
        'repertoires': [{'key': 'black', 'title': 'Répertoire black', 'learner_color': 'black'}],
        'policies': [{'key': 'black', 'repertoire_key': 'black'}],
        'routes': [{'key': 'line', 'starting_fen': chess.STARTING_FEN,
                    'moves': ['e2e4', 'e7e5', 'g1f3', 'b8c6'], 'authority_start_ply': 1}],
        'memberships': [{'repertoire_key': 'black', 'policy_key': 'black', 'route_key': 'line'}],
        'exercises': [{'key': 'segment', 'decision_key': 'segment', 'policy_key': 'black',
                       'starting_fen': board.fen(en_passant='fen'), 'prompt': 'Continue',
                       'execution_prelude': {'starting_fen': chess.STARTING_FEN, 'moves': ['e2e4']},
                       'specification': {'type': 'move_line', 'mode': 'stepwise_line',
                                         'grading_policy': 'reference', 'accepted_lines': [['e7e5', 'g1f3', 'b8c6']]}}],
        'bindings': [{'route_key': 'line', 'policy_key': 'black', 'ply': 1, 'exercise_key': 'segment'},
                     {'route_key': 'line', 'policy_key': 'black', 'ply': 3, 'exercise_key': 'segment', 'exercise_ply': 2}],
    })
    manifest = {'schema_version': 1, 'kind': 'authored_import', 'workspace_id': source.workspace_id,
                'source_key': source.source_key, 'title': source.title, 'complete': True,
                'provenance': {'kind': 'synthetic', 'source_sha256': hashlib.sha256(canonical_source(source).encode()).hexdigest(),
                               'description': 'Explicit synthetic segmented graph'},
                'legacy_aliases': {}, 'historical_mapping_unknown': [], 'reentry': []}
    publication = build_publication(source, 1, 1, '2026-10-26T00:00:00Z', {'segment': 1})
    row = {'payload': publication.model_dump_json(), 'digest': digest(publication.model_dump(mode='json')),
           'provenance': {'snapshot': source.model_dump(mode='json'), 'source_id': 'source', 'source_key': 'opening',
                          'source_digest': 'a' * 64, 'source_revision': 1, 'inventory_version': 'test'}}
    application = BridgeApplication(SimpleNamespace(workspace_id=source.workspace_id),
                                    SimpleNamespace(publication=lambda *args: row))
    return source, manifest, publication, application


def test_learning_profile_preserves_complete_authored_execution_and_marks_only_reply_omission():
    source, manifest, publication, application = export_case()
    full = application.export(publication.publication_id, 1, manifest)
    learning = application.export(publication.publication_id, 1, {**manifest, 'export_profile': 'learning'})
    assert full['legal_replies'] and 'export_profile' not in full and 'legal_replies_omitted' not in full
    assert 'legal_replies' not in learning
    assert learning['export_profile'] == 'learning' and learning['legal_replies_omitted'] is True
    assert learning['complete'] is True
    assert learning['export_digest'] == digest({key: value for key, value in learning.items() if key != 'export_digest'})
    assert learning['export_digest'] != full['export_digest']
    assert {key: value for key, value in full.items() if key not in ('legal_replies', 'export_digest')} == {
        key: value for key, value in learning.items() if key not in ('export_profile', 'legal_replies_omitted', 'export_digest')}
    assert learning['publication'] == publication.model_dump(mode='json')
    route = learning['routes'][0]
    assert route['moves'] == source.routes[0].moves and route['authority_start_ply'] == 1
    assert route['repertoire_title'] == source.repertoires[0].title
    assert [binding['ply'] for binding in route['bindings']] == [1, 3]
    assert [binding['exercise_ply'] for binding in route['bindings']] == [1, 3]
    assert learning['legacy_aliases'] == {}


def test_learning_profile_never_enumerates_optional_reply_inventory(monkeypatch):
    from tempo_content.bridge import application as module
    _, manifest, publication, application = export_case()
    def unexpected(*args):
        pytest.fail('Learning-only delivery must not enumerate optional reply inventory')
    monkeypatch.setattr(module, 'canonical_position', unexpected)
    monkeypatch.setattr(module, 'append_export_scope', unexpected)
    assert application.export(publication.publication_id, 1, {**manifest, 'export_profile': 'learning'})['complete']


def test_default_full_export_keeps_the_qualified_prior_canonical_bytes():
    _, manifest, publication, application = export_case()
    exported = application.export(publication.publication_id, 1, manifest)
    # Qualified against the exact pre-profile application on the same pinned
    # fixture, Python 3.12/chess 1.11.2/Pydantic 2.12.5 runtime.
    assert hashlib.sha256(canonical(exported).encode()).hexdigest() == 'cdefba478d3134d330f7e7538a97a46de4d570961a3b7b69d2ec6480d311825d'


@pytest.mark.parametrize('profile', ['games', 'full', '', False, 1])
def test_unknown_export_profiles_fail_closed(profile):
    _, manifest, publication, application = export_case()
    with pytest.raises(ValueError):
        ImportManifest.model_validate({**manifest, 'export_profile': profile})
    with pytest.raises(ContentError, match='Unsupported approved'):
        application.export(publication.publication_id, 1, {**manifest, 'export_profile': profile})


class DraftStorage:
    def __init__(self):
        self.drafts = {}
        self.operations = {}

    def insert_draft(self, database, identifier, source_id, source_key, revision, fingerprint, document):
        self.drafts[identifier] = {'id': identifier, 'source_id': source_id, 'source_key': source_key,
                                   'expected_revision': revision, 'digest': fingerprint, 'document': deepcopy(document)}

    def draft(self, identifier):
        return self.drafts[identifier]

    def source_scope_lock(self, *args):
        pass

    def operation_for_draft(self, *args):
        return None

    def lock_scope(self, *args):
        pass

    def source_revision(self, *args):
        return {'requested_revision': 1}

    def insert_operation(self, database, identifier, draft_id, source_id):
        self.operations[identifier] = {'draft_id': draft_id, 'state': 'approved'}


def test_default_manifest_bytes_and_draft_digest_are_unchanged_while_profile_requires_new_approval():
    source, manifest, _, _ = export_case()
    parsed = ImportManifest.model_validate(manifest)
    legacy_manifest = {**manifest, 'provenance': {**manifest['provenance'], 'evidence_refs': []},
                       'repertoires': [], 'policies': [], 'route_assignments': []}
    assert canonical(parsed.model_dump(mode='json')) == canonical(legacy_manifest)
    assert 'export_profile' not in json.loads(parsed.model_dump_json())
    assert ImportManifest.model_validate({**manifest, 'export_profile': None}).model_dump(mode='json') == parsed.model_dump(mode='json')
    storage = DraftStorage()
    application = BridgeApplication(SimpleNamespace(workspace_id=source.workspace_id,
                                    _transaction=lambda: nullcontext(None)), storage)
    application.operation = lambda identifier: storage.operations[identifier]
    old = application.draft(source, manifest, 1)
    assert old['digest'] == digest({'schema_version': 1, 'source': json.loads(canonical_source(source)),
                                   'manifest': legacy_manifest, 'expected_revision': 1, 'proposal': None})
    new = application.draft(source, {**manifest, 'export_profile': 'learning'}, 1)
    assert new['id'] != old['id'] and new['digest'] != old['digest']
    assert new['document']['source'] == old['document']['source']
    with pytest.raises(ContentError, match='exact inspected draft digest'):
        application.approve(new['id'], old['digest'])
    assert storage.operations == {}
    assert application.approve(new['id'], new['digest'])['state'] == 'approved'


@pytest.mark.parametrize('profile', [None, 'learning'])
def test_learning_and_default_profiles_retain_the_existing_64_mib_export_guard(monkeypatch, profile):
    from tempo_content.bridge import application as module
    _, manifest, publication, application = export_case()
    original = module.canonical
    class OversizedBytes:
        def __len__(self):
            return 64 * 1024 * 1024 + 1
    class OversizedExport(str):
        def encode(self, *args, **kwargs):
            return OversizedBytes()
    def measured(value):
        if isinstance(value, dict) and value.get('kind') == 'content_bridge_export':
            return OversizedExport('budget measurement')
        return original(value)
    monkeypatch.setattr(module, 'canonical', measured)
    with pytest.raises(ContentError, match='64 MiB'):
        application.export(publication.publication_id, 1, {**manifest, 'export_profile': profile})


def test_new_learning_intent_reuses_complete_inventory_but_terminal_failed_intent_cannot_prepare():
    source, manifest, publication, application = export_case()
    profile = {**manifest, 'export_profile': 'learning'}
    operation = {'state': 'approved', 'source_key': source.source_key, 'source_id': 'source',
                 'draft': {'source': source.model_dump(mode='json'), 'manifest': profile, 'expected_revision': 1},
                 'document': {}}
    state = {'state': 'approved'}
    calls = []
    application.operation = lambda identifier: deepcopy(operation)
    application.service._transaction = lambda: nullcontext(None)
    def load(snapshot, expected_revision, operation_id):
        assert snapshot == source.model_dump(mode='json') and expected_revision == 1 and operation_id == 'new-intent'
        calls.append('same-source replay')
        return {'replayed': True, 'source_revision': 1, 'generation': 1}
    application.service.load_source = load
    application.service.inventory_status = lambda key: {'state': 'complete'}
    application.service.publication_build = lambda key: (calls.append('existing publication') or publication)
    application.service.work_once = lambda key: pytest.fail('Complete inventory must not rerun')
    application.storage.lock_scope = lambda *args: None
    application.storage.operation_state_for_update = lambda *args: state
    application.storage.mark_applying = lambda *args: state.update(state='applying')
    application.storage.merge_prepared_document = lambda database, identifier, document: operation['document'].update(document)
    prepared = application.prepare('new-intent')
    assert 'export_packed' in prepared['document'] and calls == ['same-source replay', 'existing publication']
    operation['state'] = state['state'] = 'failed'
    with pytest.raises(ContentError, match='Failed pre-delivery intent'):
        application.prepare('original-failed-intent')
    assert calls == ['same-source replay', 'existing publication'] and state['state'] == 'failed'
