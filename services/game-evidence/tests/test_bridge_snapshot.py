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
