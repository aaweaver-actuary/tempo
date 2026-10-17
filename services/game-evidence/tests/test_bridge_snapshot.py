"""Local v2 authority, using the installed Content/HTTP-produced synthetic fixture."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tempo_game_evidence.analysis_export import digest
from tempo_game_evidence.snapshot import parse_snapshot


def actual_snapshot():
    return json.loads((Path(__file__).resolve().parents[1] / 'fixtures/bridge-snapshot-v2.json').read_text())


def test_A_snapshot_v2_retains_actual_activation_and_separate_serving_observation():
    document = actual_snapshot()
    snapshot = parse_snapshot(document)
    assert snapshot.serving_current()
    assert snapshot.content_export['provenance']['kind'] == 'synthetic'
    historical = deepcopy(document)
    historical['current_serving']['activation_revision'] += 1
    assert not parse_snapshot(historical).serving_current()
    assert parse_snapshot(historical).activation == snapshot.activation


def test_C3_comparison_exposes_activated_physical_card_alias():
    from tempo_game_evidence.comparison import compare_game
    from .factories import observed
    document=actual_snapshot()
    card=next(binding['card_id'] for route in document['routes'] for binding in route['bindings'] if binding['ply']==0)
    document['content_export']['legacy_aliases']={card:'original-physical'}
    document['content_export']['export_digest']=digest({key:value for key,value in document['content_export'].items() if key!='export_digest'})
    game,positions=observed(['c2c4'])
    finding=compare_game(game,positions,parse_snapshot(document))[0]
    assert finding['status']=='missed' and finding['card_id']==card
    assert finding['learning_card_id']=='original-physical'


@pytest.mark.parametrize('fault', ['source', 'route', 'binding', 'legal-generation', 'response-scope', 'missing-scope'])
def test_A_snapshot_v2_rejects_mixed_generation_and_forged_response_scope(fault):
    document = actual_snapshot()
    export = document['content_export']
    if fault == 'source':
        export['source']['source_revision'] += 1
    elif fault == 'route':
        document['routes'][0]['policy_id'] = 'foreign-policy'
    elif fault == 'binding':
        document['acceptance']['routes_digest'] = 'a' * 64
    elif fault == 'legal-generation':
        export['legal_replies'][0]['generation'] += 1
    else:
        scope = export['legal_replies'][0]['scopes'][0]
        if fault == 'missing-scope':
            export['legal_replies'][0]['scopes'] = []
        else:
            reply = scope['replies'][0]
            reply['status'] = 'covered' if reply['status'] != 'covered' else 'uncovered'
            scope['digest'] = digest({key:value for key,value in scope.items() if key != 'digest'})
    export['export_digest'] = digest({key:value for key,value in export.items() if key != 'export_digest'})
    with pytest.raises(ValueError):
        parse_snapshot(document)


def test_C3_unknown_historical_mapping_cannot_become_a_repertoire_miss():
    from tempo_game_evidence.comparison import compare_game
    from .factories import observed
    document=actual_snapshot()
    known_card=document['publication']['definitions'][0]['card_id']
    document['content_export']['historical_mapping_unknown']=[item['card_id'] for item in document['publication']['definitions']]
    document['content_export']['export_digest']=digest({key:value for key,value in document['content_export'].items() if key!='export_digest'})
    snapshot=parse_snapshot(document)
    game,positions=observed(['c2c4'])
    findings=compare_game(game,positions,snapshot)
    assert findings and all(item['status']=='unknown' for item in findings)
    assert findings[0]['explanation']=='historical_identity_unresolved'


def test_C3_unbound_authored_response_requires_unknown_export_scope():
    document=actual_snapshot();export=document['content_export']
    for collection in [document['routes'],export['routes']]:
        for route in collection:
            route['bindings']=[binding for binding in route['bindings'] if binding['ply']!=2]
    document['acceptance']['routes_digest']=digest(document['routes'])
    scope=next(scope for inventory in export['legal_replies'] for scope in inventory.get('scopes',[]) if scope['history']==['e2e4'])
    scope['complete']=False
    for reply in scope['replies']:
        reply['status']='unknown';reply['responses']=[]
    scope['digest']=digest({key:value for key,value in scope.items() if key!='digest'})
    export['export_digest']=digest({key:value for key,value in export.items() if key!='export_digest'})
    assert parse_snapshot(document).serving_current()
    reply=next(reply for reply in scope['replies'] if reply['move_uci']=='e7e5');reply['status']='uncovered'
    scope['digest']=digest({key:value for key,value in scope.items() if key!='digest'})
    export['export_digest']=digest({key:value for key,value in export.items() if key!='export_digest'})
    with pytest.raises(ValueError,match='response_scope_conflicts'):
        parse_snapshot(document)


def test_C3_snapshot_storage_preserves_authority_and_rejects_corruption(monkeypatch):
    from tempo_game_evidence import snapshot as module
    document=actual_snapshot()
    document['content_export']['provenance']['description']='captured source '+('x'*1100000)
    document['content_export']['export_digest']=digest({key:value for key,value in document['content_export'].items() if key!='export_digest'})
    stored=module.snapshot_storage(document)
    assert stored['_tempo_snapshot_storage']==1
    assert module.snapshot_document(stored)==document
    assert parse_snapshot(stored).serving_current()
    with pytest.raises(ValueError,match='storage_corrupt'):
        parse_snapshot({**stored,'sha256':'0'*64})
