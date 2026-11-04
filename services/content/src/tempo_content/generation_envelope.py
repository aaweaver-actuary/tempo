"""Pure validation of declared Content generation envelopes, never authority."""
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal

from tempo_content_publication_contracts.v1 import Publication

_LIMIT = 64 * 1024 * 1024
_STRINGS = set('workspace_id database_authority_id source_id source_key publication_id snapshot_id snapshot_schema_identity fence_id transfer_id receipt_id'.split())
_DIGESTS = set('source_digest publication_digest export_digest export_sha256 approved_manifest_digest certificate_digest'.split())
_POSITIVE = set('source_revision generation requested_revision complete_revision complete_generation'.split())
_COUNTS = set('export_byte_length definition_count route_count membership_count binding_count alias_count'.split())
_FIELDS = _STRINGS | _DIGESTS | _POSITIVE | _COUNTS | {'contract_version', 'fence_owner', 'export_profile', 'inventory'}
_TRIPLE = ('repertoire_id', 'route_id', 'policy_id')
_INVENTORY = {'definitions', 'routes', 'memberships', 'bindings', 'aliases', 'provenance'}
_HEX = re.compile(r'[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class GenerationEnvelopeResult:
    status: Literal['ENVELOPE_VALID', 'BLOCK']
    code: str
    certificate_digest: str | None = None
    authority: Literal['UNVERIFIED'] = 'UNVERIFIED'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _reject(_):
    raise ValueError('non-integer number')


def _load(raw):
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_float=_reject, parse_constant=_reject)
    _canonical(value)  # reject unpaired Unicode surrogates anywhere
    return value


def _text(value):
    return isinstance(value, str) and bool(value)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _shape(value, required, optional=()):
    return isinstance(value, dict) and set(required) <= value.keys() and value.keys() <= set(required) | set(optional)


def _binding(b):
    return (_shape(b, {'ply', 'card_id', 'reentry'}, {'exercise_ply', 'decision_id'})
            and _integer(b['ply']) and _text(b['card_id']) and type(b['reentry']) is bool
            and ('exercise_ply' not in b or _integer(b['exercise_ply']))
            and ('decision_id' not in b or _text(b['decision_id'])))


def _route(r):
    strings = set(_TRIPLE) | {'route_key', 'policy_key', 'repertoire_key', 'repertoire_title', 'learner_color', 'starting_fen'}
    return (_shape(r, strings | {'moves', 'bindings'}, {'authority_start_ply'})
            and all(_text(r[k]) for k in strings) and r['learner_color'] in ('white', 'black')
            and isinstance(r['moves'], list) and len(r['moves']) <= 1000 and all(_text(x) for x in r['moves'])
            and isinstance(r['bindings'], list) and all(_binding(x) for x in r['bindings'])
            and ('authority_start_ply' not in r or r['authority_start_ply'] is None or _integer(r['authority_start_ply'])))


def _certificate(c):
    if not _shape(c, _FIELDS) or not all(_text(c[k]) for k in _STRINGS):
        return False
    if not all(isinstance(c[k], str) and _HEX.fullmatch(c[k]) for k in _DIGESTS):
        return False
    if not all(_integer(c[k], 1) for k in _POSITIVE) or not all(_integer(c[k]) for k in _COUNTS):
        return False
    if c['contract_version'] != 'C-FENCE.v1' or c['fence_owner'] != 'Content' or c['export_profile'] != 'learning':
        return False
    inv = c['inventory']
    if not _shape(inv, _INVENTORY) or not all(isinstance(inv[k], list) for k in _INVENTORY):
        return False
    if any(len(inv[k]) > limit for k, limit in (('definitions', 10000), ('routes', 100000), ('memberships', 100000), ('bindings', 100000), ('aliases', 8192))):
        return False
    if not all(isinstance(x, dict) for x in inv['definitions']) or not all(_route(x) for x in inv['routes']):
        return False
    if not all(_shape(x, _TRIPLE) and all(_text(x[k]) for k in _TRIPLE) for x in inv['memberships']):
        return False
    if not all(_shape(x, set(_TRIPLE) | {'binding'}) and all(_text(x[k]) for k in _TRIPLE) and _binding(x['binding']) for x in inv['bindings']):
        return False
    if not all(_shape(x, {'card_id', 'legacy_id'}) and _text(x['card_id']) and _text(x['legacy_id']) for x in inv['aliases']):
        return False
    p = inv['provenance']
    if len(p) != 3 or not all(_shape(x, {'kind', 'identity', 'digest'}) for x in p):
        return False
    if [x['kind'] for x in p] != ['export_provenance', 'export_publication', 'export_source']:
        return False
    for x in p:
        keys = {'workspace_id', 'publication_id', 'generation'} if x['kind'] == 'export_publication' else {'workspace_id', 'source_id', 'source_key'}
        if not _shape(x['identity'], keys) or not all(_text(x['identity'][k]) for k in keys - {'generation'}):
            return False
        if 'generation' in keys and not _integer(x['identity']['generation'], 1):
            return False
        if not isinstance(x['digest'], str) or not _HEX.fullmatch(x['digest']):
            return False
    return True


def _unique(items, key):
    keys = [key(x) for x in items]
    return len(keys) == len(set(keys))


def _same(a, b):
    return sorted(_canonical(x) for x in a) == sorted(_canonical(x) for x in b)


def validate_content_generation_envelope(*, certificate_json: bytes, export_bytes: bytes, expected_transfer_id: str) -> GenerationEnvelopeResult:
    def block(code):
        return GenerationEnvelopeResult('BLOCK', 'content_generation_' + code)
    try:
        if not isinstance(certificate_json, bytes) or not isinstance(export_bytes, bytes) or len(certificate_json) > _LIMIT or len(export_bytes) > _LIMIT or not _text(expected_transfer_id):
            return block('envelope_invalid')
        c, e = _load(certificate_json), _load(export_bytes)
        if not _certificate(c) or not isinstance(e, dict):
            return block('envelope_invalid')
        # Validate structurally closed export before comparing dependent semantics.
        required = {'schema_version', 'kind', 'workspace_id', 'source', 'publication', 'publication_digest', 'complete', 'provenance', 'routes', 'legacy_aliases', 'historical_mapping_unknown', 'export_digest'}
        if not _shape(e, required, {'export_profile', 'legal_replies_omitted', 'legal_replies'}):
            return block('envelope_invalid')
        if e['schema_version'] != 1 or type(e['schema_version']) is not int or e['kind'] != 'content_bridge_export':
            return block('envelope_invalid')
        s, pub, prov = e['source'], e['publication'], e['provenance']
        if not _shape(s, {'source_id', 'source_key', 'source_digest', 'source_revision', 'inventory_version'}) or not all(_text(s[k]) for k in ('source_id', 'source_key', 'inventory_version')) or not isinstance(s['source_digest'], str) or not _HEX.fullmatch(s['source_digest']) or not _integer(s['source_revision'], 1):
            return block('envelope_invalid')
        if not _shape(prov, {'kind', 'source_sha256', 'description'}, {'evidence_refs'}) or prov['kind'] not in ('synthetic', 'authored_export', 'public_reference') or not isinstance(prov['source_sha256'], str) or not _HEX.fullmatch(prov['source_sha256']) or not _text(prov['description']) or len(prov['description']) > 2000 or ('evidence_refs' in prov and (not isinstance(prov['evidence_refs'], list) or len(prov['evidence_refs']) > 100 or not all(isinstance(x, str) for x in prov['evidence_refs']))):
            return block('envelope_invalid')
        if not isinstance(e['routes'], list) or not all(_route(x) for x in e['routes']) or not isinstance(e['legacy_aliases'], dict) or not all(_text(k) and _text(v) for k, v in e['legacy_aliases'].items()) or not isinstance(e['historical_mapping_unknown'], list) or not all(_text(x) for x in e['historical_mapping_unknown']) or type(e['complete']) is not bool:
            return block('envelope_invalid')
        Publication.model_validate(pub, strict=True)
        if len(pub['definitions']) > 10000 or len(e['routes']) > 100000 or len(e['legacy_aliases']) > 8192 or len(e['historical_mapping_unknown']) > 8192 or sum(len(r['bindings']) for r in e['routes']) > 100000:
            return block('envelope_invalid')
        if c['transfer_id'] != expected_transfer_id:
            return block('transfer_mismatch')
        if e.get('export_profile') != 'learning' or e.get('legal_replies_omitted') is not True or 'legal_replies' in e:
            return block('profile_mismatch')
        if c['requested_revision'] != c['source_revision'] or c['complete_revision'] != c['source_revision']:
            return block('source_changed')
        if c['complete_generation'] != c['generation']:
            return block('stale_generation')
        if (_digest({k: v for k, v in c.items() if k != 'certificate_digest'}) != c['certificate_digest']
                or len(export_bytes) != c['export_byte_length'] or hashlib.sha256(export_bytes).hexdigest() != c['export_sha256']
                or _digest({k: v for k, v in e.items() if k != 'export_digest'}) != e['export_digest'] or e['export_digest'] != c['export_digest']
                or e['workspace_id'] != c['workspace_id'] or pub['workspace_id'] != c['workspace_id']
                or any(s[k] != c[k] for k in ('source_id', 'source_key', 'source_digest', 'source_revision'))
                or pub['publication_id'] != c['publication_id'] or pub['publication_generation'] != c['generation'] or pub['source_revision'] != c['source_revision']
                or e['publication_digest'] != c['publication_digest'] or _digest(pub) != c['publication_digest']):
            return block('immutable_conflict')
        inv = c['inventory']
        routes = e['routes']
        triples = [{k: r[k] for k in _TRIPLE} for r in routes]
        bindings = [{**{k: r[k] for k in _TRIPLE}, 'binding': b} for r in routes for b in r['bindings']]
        aliases = [{'card_id': k, 'legacy_id': v} for k, v in e['legacy_aliases'].items()]
        source_id = {'workspace_id': e['workspace_id'], 'source_id': s['source_id'], 'source_key': s['source_key']}
        provenance = [{'kind': 'export_provenance', 'identity': source_id, 'digest': _digest(prov)},
                      {'kind': 'export_publication', 'identity': {'workspace_id': e['workspace_id'], 'publication_id': pub['publication_id'], 'generation': pub['publication_generation']}, 'digest': _digest(pub)},
                      {'kind': 'export_source', 'identity': source_id, 'digest': _digest(s)}]
        actual = {'definitions': pub['definitions'], 'routes': routes, 'memberships': triples, 'bindings': bindings, 'aliases': aliases, 'provenance': provenance}
        identity = lambda x: tuple(x[k] for k in _TRIPLE)
        bind_id = lambda x: identity(x) + (x['binding']['ply'], x['binding']['card_id'], x['binding'].get('exercise_ply', None), x['binding'].get('decision_id', None))
        for data in (actual, inv):
            if (not _unique(data['definitions'], lambda x: x['card_id']) or not _unique(data['routes'], identity) or not _unique(data['memberships'], identity) or not _unique(data['bindings'], bind_id) or not _unique(data['aliases'], lambda x: x['card_id']) or not _unique(data['aliases'], lambda x: x['legacy_id'])):
                return block('inventory_mismatch')
        card_ids = {d['card_id'] for d in pub['definitions']}
        if (not e['complete'] or e['historical_mapping_unknown'] or any(not _same(actual[k], inv[k]) for k in _INVENTORY)
                or any(c[field] != len(actual[key]) for key, field in (('definitions', 'definition_count'), ('routes', 'route_count'), ('memberships', 'membership_count'), ('bindings', 'binding_count'), ('aliases', 'alias_count')))
                or any(b['binding']['card_id'] not in card_ids for b in bindings)):
            return block('inventory_mismatch')
        return GenerationEnvelopeResult('ENVELOPE_VALID', 'content_generation_envelope_valid', c['certificate_digest'])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        return block('envelope_invalid')
