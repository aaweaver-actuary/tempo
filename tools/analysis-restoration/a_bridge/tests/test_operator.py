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
