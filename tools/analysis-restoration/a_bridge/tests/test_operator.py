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
