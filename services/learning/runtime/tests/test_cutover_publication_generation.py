"""F12: inactive whole-generation admission and exact identity fencing."""
from copy import deepcopy
import json
import time

from test_content_bridge import content_bundle, request_for, stage_v2


def test_f12_complete_generation_and_stale_inactive_fences(case):
    payload = content_bundle(10000)
    size = len(json.dumps(payload, separators=(',', ':')).encode())
    assert size < 16_777_216
    operator = {'Authorization': 'Bearer ' + case.configuration.operator_token}
    started = time.perf_counter()
    first = stage_v2(case, payload)
    assert first.status_code == 200, first.text
    assert stage_v2(case, payload).json() == first.json()
    assert case.query('SELECT count(*) FROM learning.cards')[0][0] == 0
    changed = deepcopy(payload)
    changed['source_digest'] = 'b' * 64
    assert stage_v2(case, changed).status_code == 409
    partial = deepcopy(payload)
    partial['publication']['publication_generation'] = 2
    partial['publication']['source_revision'] = 2
    for definition in partial['publication']['definitions']:
        definition.update(publication_generation=2, source_revision=2)
    partial['facts'].pop()
    assert stage_v2(case, partial).status_code == 422
    assert case.query('SELECT count(*) FROM learning.accepted_publications')[0][0] == 1
    response = case.client.post('/internal/publications/v2/activate', json=request_for(payload), headers=operator)
    assert response.status_code == 200, response.text
    assert case.query('SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0] == 10000
    assert case.query('SELECT count(*) FROM learning.opening_graph_steps WHERE generation=1')[0][0] == 10000
    expected = {definition['card_id'] for definition in payload['publication']['definitions']}
    actual = {row[0] for row in case.query('SELECT id FROM learning.cards WHERE serving_active=1')}
    assert actual == expected
    successor = deepcopy(payload)
    successor['publication'].update(publication_generation=3, source_revision=3)
    successor['source_digest'] = 'c' * 64
    for definition in successor['publication']['definitions']:
        definition.update(publication_generation=3, source_revision=3)
    assert stage_v2(case, successor).status_code == 200
    assert stage_v2(case, payload).status_code == 409
    assert case.client.post('/internal/publications/v2/activate', json=request_for(payload, 1), headers=operator).status_code == 409
    assert [tuple(row) for row in case.query('SELECT generation,activation_revision FROM learning.serving_publications')] == [(1, 1)]
    print(f'F12 definitions=10000 facts=10000 routes=10000 bindings=10000 compact_bytes={size} elapsed_seconds={time.perf_counter()-started:.3f}')
