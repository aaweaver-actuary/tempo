import ast
from importlib import metadata
import json
from pathlib import Path

import pytest
import tempo_a_bridge
from tempo_a_bridge.cli import Operator,execution_bundle


def test_installed_operator_is_independent_of_all_domain_services():
    assert 'site-packages' in Path(tempo_a_bridge.__file__).parts
    assert not metadata.requires('tempo-a-bridge')
    for path in Path(tempo_a_bridge.__file__).parent.glob('*.py'):
        tree=ast.parse(path.read_text())
        imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom) and node.level==0]
        assert not any(name.startswith(('tempo_content','tempo_learning','tempo_game_evidence')) for name in imports)


def test_operator_requires_private_explicit_credential_and_safe_origin(tmp_path):
    credential=tmp_path/'operator'
    credential.write_text('a'*40)
    credential.chmod(0o644)
    with pytest.raises(ValueError,match='private_operator'):
        Operator('/installed/tempo-content','http://127.0.0.1:8001',credential)
    credential.chmod(0o600)
    for origin in ('http://remote.example','https://user:secret@example.com','https://example.com/api','https://example.com?token=secret'):
        with pytest.raises(ValueError):
            Operator('/installed/tempo-content',origin,credential)
    assert Operator('/installed/tempo-content','http://127.0.0.1:8001',credential).endpoint=='http://127.0.0.1:8001'


def test_execution_mapping_retains_identity_memberships_and_every_alternative():
    definition={'card_id':'content-card','specification':{'type':'move_line','accepted_lines':[['e2e4'],['d2d4']]}}
    route={'repertoire_id':'white','route_id':'e4','policy_id':'main','route_key':'e4','policy_key':'main','repertoire_key':'white',
        'learner_color':'white','starting_fen':'explicit-fen','moves':['e2e4'],'bindings':[{'ply':0,'card_id':'content-card','reentry':False}]}
    export={'publication':{'definitions':[definition]},'source':{'source_digest':'a'*64},'routes':[route], 'legacy_aliases':{'content-card':'historical-card'}}
    bundle=execution_bundle(export,'2026-10-16')
    assert bundle['publication']==export['publication']
    assert bundle['facts'][0]['card_id']=='content-card'
    assert bundle['legacy_aliases']==export['legacy_aliases']
    assert 'route_key' not in bundle['routes'][0]
    assert bundle['routes'][0]['bindings']==route['bindings']


class SnapshotOperator(Operator):
    def __init__(self):
        self.endpoint = "http://127.0.0.1:8001"
        self.operations = {}
        self.revision = 2
        self.observations = 0

    def add_operation(self, identifier, generation):
        self.operations[identifier] = {"state": "completed", "document": {
            "export": {"complete": True, "publication": {"workspace_id": "workspace", "publication_id": "publication", "publication_generation": generation}, "routes": []},
            "receipt": {"endpoint": self.endpoint, "acknowledgement": {"activation_revision": generation}}}}

    def content(self, *arguments):
        from copy import deepcopy
        return deepcopy(self.operations[arguments[2]])

    def http(self, path, document=None):
        self.observations += 1
        return 200, {"activation_revision": self.revision, "publication_generation": 2,
            "status": "active", "observed_at": str(self.observations), "local_date": "2026-10-16", "accepted": {"immutable": True}}

    def save(self, identifier, command, document):
        self.operations[identifier]["document"].setdefault("snapshots", {}).update(document["snapshots"])
        return self.operations[identifier]


def test_A_snapshot_identity_distinguishes_publication_generations():
    operator = SnapshotOperator()
    operator.add_operation("old", 1)
    operator.add_operation("new", 2)
    historical = operator.snapshot("old")
    successor = operator.snapshot("new")
    assert historical["snapshot_id"] != successor["snapshot_id"]
    assert historical["revision"] == successor["revision"] == 2
    assert historical["activation"]["activation_revision"] == 1


def test_A_repeated_snapshot_observation_is_immutable():
    operator = SnapshotOperator()
    operator.add_operation("old", 1)
    initial = operator.snapshot("old")
    assert operator.snapshot("old") == initial
    operator.revision = 3
    assert operator.snapshot("old")["revision"] == 3


@pytest.mark.parametrize('conflicting', [False, True])
def test_A_concurrent_snapshot_checkpoint_preserves_one_identity(conflicting):
    from copy import deepcopy
    from tempo_a_bridge.cli import ContentCommandError
    operator = SnapshotOperator()
    operator.add_operation('old', 1)
    initial_save = operator.save
    def race(identifier, command, document):
        winner = deepcopy(document)
        snapshot = next(iter(winner['snapshots'].values()))
        snapshot['current_serving']['observed_at'] = 'winning-observation'
        if conflicting:
            snapshot['acceptance']['immutable'] = False
        initial_save(identifier, command, winner)
        raise ContentCommandError('checkpoint_conflict')
    operator.save = race
    if conflicting:
        with pytest.raises(ContentCommandError):
            operator.snapshot('old')
    else:
        assert operator.snapshot('old')['current_serving']['observed_at'] == 'winning-observation'


def test_A_terminal_learning_failure_is_reconciled_without_replay():
    operator = SnapshotOperator()
    operator.token_file = '/private/operator-token'
    delivery = {'endpoint': operator.endpoint, 'request': {'activation_id': 'operation:activate'}, 'retirement': False}
    operation = {'state': 'applying', 'document': {'delivery': delivery}}
    commands = []
    def content(*arguments):
        commands.append(arguments)
        if arguments[1] == 'record-failure':
            return {'state': 'failed'}
        return operation
    operator.content = content
    operator.http = lambda *args: (200, {'state': 'failed'})
    assert operator.apply('operation')['state'] == 'failed'
    assert commands[-1][1] == 'record-failure'


class RecoveryOperator(Operator):
    def __init__(self, empty=False):
        from copy import deepcopy
        self.endpoint = 'http://127.0.0.1:8001'
        self.token_file = '/private/operator-token'
        self.commands, self.requests = [], []
        self.remote_state, self.stage_error, self.abort_error = 'unknown', None, None
        self.abort_outcome = 'failed'
        self.unsupported = False
        publication = {'workspace_id': 'workspace', 'publication_id': 'publication', 'publication_generation': 1,
            'definitions': [{'card_id': 'card', 'specification': {'type': 'move_line'}}]}
        export = {'publication': publication, 'source': {'source_digest': 'a' * 64}, 'routes': [
            {'repertoire_id': 'white', 'learner_color': 'white', 'bindings': [{'card_id': 'card'}]}], 'legacy_aliases': {}}
        self.operation = {'id': 'operation', 'state': 'approved', 'source_key': 'source', 'publication_id': 'publication',
            'draft': {'source': {'workspace_id': 'workspace', 'exercises': [] if empty else [{}]}},
            'document': {'retirement_required': True} if empty else {'export': export}}
        self.current = {'schema_version': 1, 'workspace_id': 'workspace', 'publication_id': 'publication',
            'publication_generation': None, 'activation_revision': 0, 'status': 'unavailable', 'local_date': '2026-10-16'}

    def content(self, *arguments):
        from copy import deepcopy
        from tempo_a_bridge.cli import ContentCommandError
        self.commands.append(arguments)
        command = arguments[1]
        if command == 'prepare':
            if self.unsupported:
                self.operation['state'] = 'failed'
                raise ContentCommandError('bridge_scope_unsupported')
            self.operation['state'] = 'applying'
        elif command == 'record-failure':
            assert self.remote_state == 'failed'
            self.operation['state'] = 'failed'
        elif command == 'fail-before-delivery':
            assert 'delivery' not in self.operation['document']
            self.operation['state'] = 'failed'
        return deepcopy(self.operation)

    def save(self, identifier, command, document):
        from copy import deepcopy
        self.commands.append(('operation', command, identifier))
        self.operation['document'].update(deepcopy(document))
        if command == 'record':
            assert self.remote_state == 'complete'
            self.operation['state'] = 'completed'
        return deepcopy(self.operation)

    def http(self, path, document=None):
        from copy import deepcopy
        self.requests.append((path, deepcopy(document)))
        if path.endswith('/status'):
            return 200, deepcopy(self.current)
        if path.endswith('/stage'):
            assert 'delivery' in self.operation['document']
            if self.stage_error:
                raise self.stage_error
            return 200, {'status': 'accepted_pending'}
        if path.endswith('/abort'):
            assert document['request'] == self.operation['document']['delivery']['request']
            assert document['workspace_id'] == 'workspace'
            if self.abort_error:
                raise self.abort_error
            self.remote_state = self.abort_outcome
        elif path.endswith('/activate') or path.endswith('/retire') or path.endswith('/retry'):
            assert document == self.operation['document']['delivery']['request'] or document == {}
            self.remote_state = 'complete'
            return 200, {'status': 'activated'}
        return 200, {'state': self.remote_state, 'response': {'status': 'activated'}}


def test_A_unsupported_execution_scope_fails_before_irreversible_apply():
    operator = RecoveryOperator()
    operator.unsupported = True
    assert operator.apply('operation')['state'] == 'failed'
    assert operator.requests == []
    assert [command[1] for command in operator.commands] == ['show', 'prepare', 'show']


@pytest.mark.parametrize('status', ['unavailable', 'retired'])
def test_A_initial_empty_source_without_active_publication_is_recoverable(status):
    operator = RecoveryOperator(empty=True)
    operator.current.update(status=status, publication_generation=1 if status == 'retired' else None,
        activation_revision=2 if status == 'retired' else 0)
    with pytest.raises(ValueError, match='remains_approved_and_cancellable'):
        operator.apply('operation')
    assert operator.operation['state'] == 'approved'
    assert [command[1] for command in operator.commands] == ['show']
    assert len(operator.requests) == 1


@pytest.mark.parametrize('fault', ['offline', 'workspace', 'publication', 'date', 'generation', 'revision', 'status', 'version', '401', '403', '500', 'malformed'])
def test_A_unavailable_retirement_preflight_preserves_original_operation(fault):
    from tempo_a_bridge.cli import LearningHTTPError
    operator = RecoveryOperator(empty=True)
    if fault == 'version':
        operator.current.update(status='active', publication_generation=1, activation_revision=1)
    if fault.isdigit():
        operator.http = lambda *args: (_ for _ in ()).throw(LearningHTTPError(int(fault)))
    elif fault == 'offline':
        operator.http = lambda *args: (_ for _ in ()).throw(OSError('offline'))
    elif fault == 'malformed':
        operator.http = lambda *args: (200, [])
    else:
        field = {'workspace': 'workspace_id', 'publication': 'publication_id', 'date': 'local_date',
            'generation': 'publication_generation', 'revision': 'activation_revision', 'status': 'status', 'version': 'schema_version'}[fault]
        operator.current[field] = True if fault == 'version' else 'wrong'
    with pytest.raises((ValueError, OSError)):
        operator.apply('operation')
    assert operator.operation['state'] == 'approved'
    assert [command[1] for command in operator.commands] == ['show']


@pytest.mark.parametrize('status', [409, 413, 422])
def test_A_stage_rejection_does_not_release_unfenced_authority(status):
    from tempo_a_bridge.cli import LearningHTTPError
    operator = RecoveryOperator()
    operator.stage_error = LearningHTTPError(status)
    operator.abort_error = LearningHTTPError(503)
    with pytest.raises(ValueError):
        operator.apply('operation')
    assert operator.operation['state'] == 'applying'
    assert 'delivery' in operator.operation['document']
    assert not any(command[1] == 'record-failure' for command in operator.commands)
    assert sum(path.endswith('/abort') for path, _ in operator.requests) == 1


@pytest.mark.parametrize('outcome', ['failed', 'complete'])
def test_A_verified_remote_terminal_fence_allows_content_recovery(outcome):
    from tempo_a_bridge.cli import LearningHTTPError
    operator = RecoveryOperator()
    operator.stage_error = LearningHTTPError(409)
    operator.abort_outcome = outcome
    result = operator.apply('operation')
    assert result['state'] == ('failed' if outcome == 'failed' else 'completed')
    commands = [command[1] for command in operator.commands]
    assert commands.index('checkpoint') < commands.index('record-failure' if outcome == 'failed' else 'record')
    assert not any(path.endswith('/activate') for path, _ in operator.requests)


@pytest.mark.parametrize('error', [OSError('lost response'), ValueError('malformed'), 401, 403, 500, 503])
def test_A_lost_stage_response_preserves_original_operation_identity(error):
    from tempo_a_bridge.cli import LearningHTTPError
    operator = RecoveryOperator()
    operator.stage_error = LearningHTTPError(error) if isinstance(error, int) else error
    with pytest.raises((ValueError, OSError)):
        operator.apply('operation')
    delivery = json.loads(json.dumps(operator.operation['document']['delivery']))
    assert operator.operation['state'] == 'applying'
    assert not any(path.endswith('/abort') for path, _ in operator.requests)
    operator.stage_error = None
    assert operator.apply('operation')['state'] == 'completed'
    assert operator.operation['document']['delivery'] == delivery


def test_A_retirement_preflight_observation_remains_revision_fenced():
    operator = RecoveryOperator(empty=True)
    operator.current.update(status='active', publication_generation=3, activation_revision=4)
    assert operator.apply('operation')['state'] == 'completed'
    request = operator.operation['document']['delivery']['request']
    assert request == {'publication_id': 'publication', 'publication_generation': 3,
        'expected_activation_revision': 4, 'retirement_id': 'operation:retire'}
    assert sum(path.endswith('/status') for path, _ in operator.requests) == 1
    assert not any(path.endswith('/stage') for path, _ in operator.requests)


@pytest.mark.parametrize('delivered', [False, True])
def test_A_explicit_abandon_uses_original_owner_authority(delivered):
    operator = RecoveryOperator()
    if delivered:
        from tempo_a_bridge.cli import LearningHTTPError
        operator.stage_error = LearningHTTPError(503)
        with pytest.raises(ValueError):
            operator.apply('operation')
    else:
        operator.operation['state'] = 'applying'
    assert operator.abandon('operation')['state'] == 'failed'
    assert any(command[1] == ('record-failure' if delivered else 'fail-before-delivery') for command in operator.commands)
