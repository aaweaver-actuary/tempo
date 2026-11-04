"""Prospective Tactics generation fixtures, never production assets."""
from pathlib import Path

import pytest

from app.services import tactics_asset_generation as gen
from app.services.tactics_asset_archive import AssetGenerationBlocked

C = b'{"version":1,"packs":[{"id":"a","asset":"data/a.json"}]}'
P = b'[{"PuzzleId":"p1","FEN":"synthetic","Moves":"a1a2"}]'
LIMITS = gen.AssetCaptureLimits(1024, 1024, 2048, 4, 4, 6)


@pytest.fixture
def roots(tmp_path):
    root = tmp_path / 'source'
    (root / 'data').mkdir(parents=True)
    (root / 'data/tactics-catalog.json').write_bytes(C)
    (root / 'data/a.json').write_bytes(P)
    archive = tmp_path / 'archive'
    archive.mkdir(mode=0o700)
    return root, archive


def capture(roots, limits=LIMITS):
    return gen.capture_recorded_tactics_generation(configured_root=roots[0], archive_root=roots[1], workspace_id='fixture', process_incarnation='fixture-process', limits=limits)


def test_recorded_generation_is_private_and_prospective(roots):
    first = capture(roots)
    assert first.files_at_capture.source_atomicity == 'UNPROVEN'
    assert first.archive_receipt.status == 'ARCHIVED_READBACK'
    assert first.observation().code == 'CACHE_PROVENANCE_UNRECORDED'
    assert first.catalog()['packs'][0]['id'] == 'a'
    assert first.observation().loaded_cache_states == ('catalog',)
    result = first.pack_records('a')
    result[0]['PuzzleId'] = 'modified'
    assert first.puzzle_membership()['p1'][1]['PuzzleId'] == 'p1'
    obs = first.observation()
    assert obs.loaded_cache_states == ('catalog', 'packs', 'membership')
    assert obs.history == obs.installed_authority == 'UNPROVEN'
    assert obs.membership_inventory_id == first.inventory.inventory_id
    second = capture(roots)
    assert second.inventory == first.inventory
    assert second.archive_receipt == first.archive_receipt
    (roots[0] / 'data/a.json').write_bytes(P.replace(b'synthetic', b'changed'))
    third = capture(roots)
    assert third.inventory.inventory_id != first.inventory.inventory_id
    assert first.pack_records('a')[0]['FEN'] == 'synthetic'
    assert first.pack_records('unknown').code == 'UNKNOWN_PACK'


def test_symlinks_missing_traversal_and_corrupt(roots):
    root, _ = roots
    (root / 'data/a.json').unlink()
    assert capture(roots).code == 'MISSING_ASSET'
    (root / 'data/a.json').symlink_to(root / 'data/tactics-catalog.json')
    assert capture(roots).code == 'UNSAFE_ASSET_PATH'
    (root / 'data/a.json').unlink()
    (root / 'data/a.json').write_bytes(b'bad')
    assert capture(roots).code == 'INVALID_JSON'
    (root / 'data/tactics-catalog.json').write_bytes(C.replace(b'data/a.json', b'../a.json'))
    assert capture(roots).code == 'UNSAFE_ASSET_PATH'
    (root / 'data/tactics-catalog.json').write_bytes(C.replace(b'data/a.json', b'link/a.json'))
    (root / 'link').symlink_to(root / 'data', target_is_directory=True)
    assert capture(roots).code == 'UNSAFE_ASSET_PATH'


def test_limits_and_changed_source(roots, monkeypatch):
    assert capture(roots, gen.AssetCaptureLimits(2, 1024, 2048, 4, 4, 6)).code == 'CAPTURE_LIMIT_EXCEEDED'
    actual = gen._read_relative
    calls = 0
    def mutate(*args):
        nonlocal calls
        result = actual(*args)
        calls += 1
        if calls == 2:
            (roots[0] / 'data/a.json').write_bytes(P + b' ')
        return result
    monkeypatch.setattr(gen, '_read_relative', mutate)
    assert capture(roots).code == 'SOURCE_CHANGED'


def test_cache_faults_and_archive_corruption(roots):
    obj = capture(roots)
    obj._catalog_cache = {'unrecorded': 'external'}
    assert obj.catalog().code == 'CACHE_PROVENANCE_UNRECORDED'
    obj._catalog_cache = ('different-generation', obj.inventory.catalog.sha256, {})
    assert obj.observation().code == 'CACHE_GENERATION_MISMATCH'
    obj._catalog_cache = None
    blob = roots[1] / 'blobs' / obj.inventory.packs[0].asset.sha256
    blob.write_bytes(b'corrupt')
    assert obj.pack_records('a').code == 'ARCHIVE_READBACK_FAILED'


def test_import_is_inert(monkeypatch):
    import importlib
    import os
    import sys
    def forbidden(*args, **kwargs):
        raise AssertionError('import must not open files or read environment')
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(os, 'getenv', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    for name in ('app.services.tactics_asset_generation', 'app.services.tactics_asset_archive'):
        sys.modules.pop(name, None)
    importlib.import_module('app.services.tactics_asset_generation')
