"""Synthetic T-ASSET.v1 identity and rejection regressions."""
import hashlib
import json

import pytest

from app.services.tactics_asset_inventory import inventory_tactics_asset_bytes

C = b'{"version":1,"packs":[{"id":"a","asset":"data/a.json"}]}'
P = b'[{"PuzzleId":"p1","FEN":"8/8/8/8/8/8/8/K6k w - - 0 1","Moves":"a1a2 h1h2","DeckId":"d","DeckPosition":1},{"PuzzleId":"p2","FEN":"8/8/8/8/8/8/8/K6k w - - 0 1","Moves":["a1a2","h1h2"],"DeckId":"d","DeckPosition":2}]'


def inventory(c=C, assets=None):
    return inventory_tactics_asset_bytes(catalog_bytes=c, pack_assets=(("data/a.json", P),) if assets is None else assets)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def test_ta01_exact_order_and_ta04_root_independence_ta05_unproven():
    result = inventory()
    assert result == inventory()
    assert (result.status, result.historical_authority, result.puzzle_count) == ('INVENTORIED', 'UNPROVEN', 2)
    assert result.catalog.sha256 == sha(C) and result.catalog.byte_length == len(C)
    assert result.packs[0].asset.sha256 == sha(P) and result.packs[0].asset.byte_length == len(P)
    records = json.loads(P)
    members = [dict(pack_id='a', index=i, puzzle_id=r['PuzzleId'], record_sha256=sha(canonical(r))) for i, r in enumerate(records)]
    assert [(m.pack_id, m.index, m.puzzle_id) for m in result.memberships] == [('a', 0, 'p1'), ('a', 1, 'p2')]
    assert result.ordered_memberships_digest == sha(canonical(members))
    envelope = dict(contract_version='T-ASSET.inventory.v1', catalog=dict(logical_path='data/tactics-catalog.json', byte_length=len(C), sha256=sha(C)), packs=[dict(pack_id='a', asset=dict(logical_path='data/a.json', byte_length=len(P), sha256=sha(P)), record_count=2)], memberships=members, puzzle_count=2, catalog_version=1, ordered_memberships_digest=sha(canonical(members)))
    assert result.inventory_id == sha(canonical(envelope))


def test_ta02_bytes_order_and_record_identity():
    original = inventory()
    reversed_pack = canonical(list(reversed(json.loads(P))))
    changed = inventory(assets=(('data/a.json', reversed_pack),))
    assert changed.packs[0].asset.sha256 != original.packs[0].asset.sha256
    assert changed.ordered_memberships_digest != original.ordered_memberships_digest
    assert changed.inventory_id != original.inventory_id
    whitespace = inventory(assets=(('data/a.json', b' ' + P),))
    assert whitespace.ordered_memberships_digest == original.ordered_memberships_digest
    assert whitespace.inventory_id != original.inventory_id
    changed_fen = inventory(assets=(('data/a.json', P.replace(b'K6k', b'K5k1', 1)),))
    assert changed_fen.memberships[0].record_sha256 != original.memberships[0].record_sha256
    reordered_keys = canonical(json.loads(P))
    assert inventory(assets=(('data/a.json', reordered_keys),)).ordered_memberships_digest == original.ordered_memberships_digest


def test_ta03_missing_duplicate_and_extra_assets():
    assert (inventory(assets=()).code, inventory(assets=()).pack_id) == ('MISSING_ASSET', 'a')
    assert inventory(assets=(('data/a.json', P), ('data/a.json', P))).code == 'DUPLICATE_ASSET_PATH'
    assert inventory(assets=(('data/a.json', P), ('extra', b'[]'))).code == 'UNEXPECTED_ASSET'
    duplicate = inventory(assets=(('data/a.json', P.replace(b'"p2"', b'"p1"')),))
    assert (duplicate.code, duplicate.logical_path, duplicate.pack_id, duplicate.puzzle_id) == ('DUPLICATE_PUZZLE_ID', 'data/a.json', 'a', 'p1')
    assert inventory(c=b'{"version":1,"packs":[{"id":"a","asset":"x"},{"id":"a","asset":"y"}]}').code == 'DUPLICATE_PACK_ID'
    assert inventory(c=b'{"version":1,"packs":[{"id":"a","asset":"x"},{"id":"b","asset":"x"}]}').code == 'DUPLICATE_ASSET_PATH'
    c = b'{"version":1,"packs":[{"id":"a","asset":"x"},{"id":"b","asset":"y"}]}'
    assert inventory(c=c, assets=(('x', P), ('y', P))).code == 'DUPLICATE_PUZZLE_ID'


@pytest.mark.parametrize('path', ['../a', '/a', 'data/../a', 'data//a', 'data/./a', 'data\\a', 'https://a', 'a\u0000b'])
def test_ta03_unsafe_path(path):
    c = json.dumps({'version': 1, 'packs': [{'id': 'a', 'asset': path}]}).encode()
    assert inventory(c=c, assets=()).code == 'UNSAFE_ASSET_PATH'


@pytest.mark.parametrize('raw', [b'\xef\xbb\xbf{}', b'\xff', b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1.2}', b'{"x":9223372036854775808}', b'{"x":"\\ud800"}'])
def test_ta03_invalid_json(raw):
    assert inventory(c=raw).code == 'INVALID_JSON'


def test_ta03_invalid_shapes_and_pack_json():
    assert inventory(c=b'{"version":true,"packs":[]}').code == 'UNSUPPORTED_CATALOG_VERSION'
    assert inventory(c=b'{"version":1,"packs":[null]}').code == 'INVALID_CATALOG'
    assert inventory(assets=(('data/a.json', b'null'),)).code == 'INVALID_PACK'
    assert inventory(assets=(('data/a.json', b'[{"PuzzleId":"p"}]'),)).code == 'INVALID_PUZZLE_RECORD'
    assert inventory(assets=(('data/a.json', b'[{"PuzzleId":"p","PuzzleId":"q"}]'),)).code == 'INVALID_JSON'
