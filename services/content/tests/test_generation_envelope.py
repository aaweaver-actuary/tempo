"""Synthetic C-FENCE contract regression: envelope validity is never authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tempo_content.generation_envelope import validate_content_generation_envelope

GOLDEN = json.loads((Path(__file__).parents[1] / 'fixtures/generation-envelope-v1/goldens.json').read_text())


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(raw(value)).hexdigest()


def inputs():
    return deepcopy(GOLDEN['certificate']), deepcopy(GOLDEN['export'])


def sign(c, e, *, export=True):
    if export:
        e['export_digest'] = digest({k: v for k, v in e.items() if k != 'export_digest'})
        data = raw(e)
        c.update(export_digest=e['export_digest'], export_sha256=hashlib.sha256(data).hexdigest(), export_byte_length=len(data))
    c['certificate_digest'] = digest({k: v for k, v in c.items() if k != 'certificate_digest'})
    return raw(c), raw(e)


def check(c, e, code, *, transfer='transfer-a'):
    result = validate_content_generation_envelope(certificate_json=c, export_bytes=e, expected_transfer_id=transfer)
    assert result.code == 'content_generation_' + code
    assert result.authority == 'UNVERIFIED'
    assert result.status == ('ENVELOPE_VALID' if code == 'envelope_valid' else 'BLOCK')
    return result


def test_cf01_and_cf07_golden_and_replay():
    c, e = inputs()
    original = sign(c, e)
    assert check(*original, 'envelope_valid') == check(*original, 'envelope_valid')
    assert check(*original, 'envelope_valid').certificate_digest == c['certificate_digest']
    c.pop('fence_id')
    check(raw(c), raw(e), 'envelope_invalid')


@pytest.mark.parametrize(('field', 'code'), [('requested_revision', 'source_changed'), ('complete_revision', 'source_changed'), ('complete_generation', 'stale_generation')])
def test_cf02_cf03_stale_declarations(field, code):
    c, e = inputs()
    c[field] += 1
    check(*sign(c, e), code)


@pytest.mark.parametrize('mutation', ['binding', 'alias', 'route', 'membership', 'duplicate_binding', 'duplicate_alias'])
def test_cf04_exact_inventories(mutation):
    c, e = inputs()
    if mutation == 'binding': e['routes'][0]['bindings'].clear()
    elif mutation == 'alias': e['legacy_aliases']['card-a'] = 'legacy-card-b'
    elif mutation == 'route': e['routes'].append(deepcopy(e['routes'][0]))
    elif mutation == 'membership': c['inventory']['memberships'].append(deepcopy(c['inventory']['memberships'][0]))
    elif mutation == 'duplicate_binding': c['inventory']['bindings'].append(deepcopy(c['inventory']['bindings'][0]))
    else: c['inventory']['aliases'].append(deepcopy(c['inventory']['aliases'][0]))
    check(*sign(c, e), 'inventory_mismatch')


def test_cf05_bytes_profile_and_immutable_identity():
    c, e = inputs()
    cert, data = sign(c, e)
    check(cert, data + b' ', 'immutable_conflict')
    e['export_profile'] = 'full'
    check(*sign(c, e), 'profile_mismatch')
    c, e = inputs()
    e['source']['source_digest'] = 'd' * 64
    check(*sign(c, e), 'immutable_conflict')


def test_cf06_distinct_namespace_and_transfer():
    c, e = inputs()
    first = check(*sign(c, e), 'envelope_valid')
    c['database_authority_id'] = 'another-database'
    second = check(*sign(c, e), 'envelope_valid')
    assert first.certificate_digest != second.certificate_digest
    check(*sign(c, e), 'transfer_mismatch', transfer='wrong')


@pytest.mark.parametrize('mutation', ['missing', 'unknown', 'bool', 'float', 'nan', 'unicode', 'duplicate', 'oversize', 'unknown_witness'])
def test_strict_codec_and_bounds(mutation):
    c, e = inputs()
    if mutation == 'missing': del c['requested_revision']
    if mutation == 'unknown': c['unexpected'] = 1
    if mutation == 'bool': c['requested_revision'] = True
    if mutation == 'unknown_witness': c['inventory']['provenance'][0]['extra'] = 1
    certificate = raw(c)
    if mutation == 'float': certificate = certificate.replace(b'"requested_revision":1', b'"requested_revision":1.0')
    if mutation == 'nan': certificate = certificate.replace(b'"requested_revision":1', b'"requested_revision":NaN')
    if mutation == 'unicode': certificate = certificate.replace(b'"workspace-a"', b'"\\ud800"', 1)
    if mutation == 'duplicate': certificate = b'{"workspace_id":"duplicate",' + certificate[1:]
    if mutation == 'oversize': certificate = b' ' * (64 * 1024 * 1024 + 1)
    check(certificate, raw(e), 'envelope_invalid')


def test_public_pure_import_avoids_bridge_and_database():
    code = '''\nimport sys\nfrom importlib.abc import MetaPathFinder\nclass Guard(MetaPathFinder):\n def find_spec(self, fullname, path=None, target=None):\n  if fullname.startswith(("tempo_content.bridge", "tempo_content_database", "psycopg")):\n   raise AssertionError(fullname)\nsys.meta_path.insert(0, Guard())\nfrom tempo_content.generation_envelope import validate_content_generation_envelope\nassert callable(validate_content_generation_envelope)\n'''
    result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cf06_separate_workspace_source_and_authority_remain_distinct():
    c, e = inputs()
    first = check(*sign(c, e), 'envelope_valid')
    c.update(database_authority_id='database-b', workspace_id='workspace-b', source_id='source-b')
    e['workspace_id'] = e['publication']['workspace_id'] = 'workspace-b'
    e['source']['source_id'] = 'source-b'
    e['publication_digest'] = c['publication_digest'] = digest(e['publication'])
    for witness in c['inventory']['provenance']:
        witness['identity']['workspace_id'] = 'workspace-b'
        if witness['kind'] == 'export_publication': witness['digest'] = digest(e['publication'])
        else: witness['identity']['source_id'] = 'source-b'
        if witness['kind'] == 'export_source': witness['digest'] = digest(e['source'])
    second = check(*sign(c, e), 'envelope_valid')
    assert first.certificate_digest != second.certificate_digest


@pytest.mark.parametrize('kind', ['export_source', 'export_publication', 'export_provenance'])
def test_provenance_witnesses_bind_export_component(kind):
    c, e = inputs()
    witness = next(x for x in c['inventory']['provenance'] if x['kind'] == kind)
    witness['digest'] = 'f' * 64
    check(*sign(c, e), 'inventory_mismatch')


def test_internally_consistent_omission_remains_unverified():
    c, e = inputs()
    e['legacy_aliases'].clear()
    c['inventory']['aliases'].clear()
    c['alias_count'] = 0
    assert check(*sign(c, e), 'envelope_valid').authority == 'UNVERIFIED'


def test_profile_and_revision_precede_immutable_mismatch():
    c, e = inputs()
    c['requested_revision'] += 1
    e['export_profile'] = 'full'
    check(raw(c), raw(e), 'profile_mismatch')
    e['export_profile'] = 'learning'
    check(raw(c), raw(e), 'source_changed')
