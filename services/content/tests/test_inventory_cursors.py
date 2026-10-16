"""Opaque generation-bound cursor validation in every installed artifact."""
import json

import pytest
from tempo_content.domain import ContentError
from tempo_content.store import _decode_inventory_cursor, _json


def cursor(kind='positions'):
    return {'version': 1, 'workspace_id': 'workspace', 'kind': kind, 'source_key': 'source',
            'source_revision': 2, 'generation': 3, 'last': 'position' if kind == 'positions' else ['route', 'policy']}


@pytest.mark.parametrize('kind', ['positions', 'routes'])
def test_inventory_cursor_canonical_roundtrip_and_initial_page(kind):
    value = cursor(kind)
    encoded = _json(value)
    assert encoded == _json(dict(reversed(list(value.items()))))
    assert _decode_inventory_cursor(encoded, kind, 'workspace', 'source', 2, 3) == value['last']
    assert _decode_inventory_cursor(None, kind, 'workspace', 'source', 2, 3) == ('' if kind == 'positions' else ['', ''])


@pytest.mark.parametrize('field', list(cursor()))
def test_inventory_cursor_rejects_every_missing_field(field):
    value = cursor()
    del value[field]
    with pytest.raises(ContentError) as rejected:
        _decode_inventory_cursor(_json(value), 'positions', 'workspace', 'source', 2, 3)
    assert rejected.value.code == 'invalid_cursor'


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('version', '1'),
    ('workspace_id', None), ('workspace_id', 'another'), ('source_key', 1), ('source_key', 'another'),
    ('kind', 'routes'), ('kind', None), ('source_revision', True), ('source_revision', 0),
    ('source_revision', 2.0), ('generation', '3'), ('generation', False), ('last', []), ('last', ''),
])
def test_inventory_cursor_rejects_wrong_types_identity_and_version(field, value):
    token = cursor()
    token[field] = value
    with pytest.raises(ContentError) as rejected:
        _decode_inventory_cursor(_json(token), 'positions', 'workspace', 'source', 2, 3)
    assert rejected.value.code == 'invalid_cursor'


@pytest.mark.parametrize('value', ['route', ['route'], ['route', 1], ['', 'policy'], ['route', 'policy', 'extra']])
def test_route_cursor_requires_ordered_string_pair(value):
    token = cursor('routes')
    token['last'] = value
    with pytest.raises(ContentError) as rejected:
        _decode_inventory_cursor(json.dumps(token), 'routes', 'workspace', 'source', 2, 3)
    assert rejected.value.code == 'invalid_cursor'


@pytest.mark.parametrize('after', ['{', 'null', '[]', '"old-position"', '["route","policy"]', 1,
    pytest.param('['*20000+']'*20000, id='excessive-nesting')])
def test_inventory_cursor_rejects_malformed_and_legacy_tokens(after):
    with pytest.raises(ContentError) as rejected:
        _decode_inventory_cursor(after, 'positions', 'workspace', 'source', 2, 3)
    assert rejected.value.code == 'invalid_cursor'


@pytest.mark.parametrize('field', ['source_revision', 'generation'])
def test_inventory_cursor_rejects_changed_generation_identity(field):
    value = cursor()
    value[field] += 1
    with pytest.raises(ContentError) as rejected:
        _decode_inventory_cursor(_json(value), 'positions', 'workspace', 'source', 2, 3)
    assert rejected.value.code == 'stale_inventory_cursor'
