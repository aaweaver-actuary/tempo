"""Historical original retention; never Content correspondence or activation.

Prepare in a caller-owned read-only repeatable-read snapshot. Publish in a
separate caller-owned writer after the existing authored and epoch fences.
Physical currentness and physical instance identity are explicitly unqualified.
"""
from dataclasses import dataclass
import hashlib
import json
import uuid

from tempo_evidence_math.position_learning import Hold
from .position_study_source import prepare_position_source_with_facts, fence_position_source
from tempo_legacy_database.adapters.study.position_owner_epoch import owner_mode

_SEAL = object()
_MAX_BYTES = 16 * 1024 * 1024
_MAX_OCCURRENCES = 100000


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _bounded_json_size(value, limit):
    """Measure canonical JSON as escaped UTF-8, stopping before overflow.

    In particular, do not encode an arbitrarily long source string merely to
    discover that it exceeds the envelope limit.
    """
    remaining = limit
    def take(count):
        nonlocal remaining
        remaining -= count
        return remaining >= 0
    def string(text):
        if not take(2): return False
        for char in text:
            code = ord(char)
            if char in ('"', '\\') or code in (8, 9, 10, 12, 13): size = 2
            elif code < 32: size = 6
            elif 0xD800 <= code <= 0xDFFF: raise UnicodeError('invalid surrogate')
            elif code < 0x80: size = 1
            elif code < 0x800: size = 2
            elif code < 0x10000: size = 3
            else: size = 4
            if not take(size): return False
        return True
    def walk(obj):
        if isinstance(obj, str): return string(obj)
        if obj is None: return take(4)
        if obj is True: return take(4)
        if obj is False: return take(5)
        if isinstance(obj, (int, float)):
            return take(len(json.dumps(obj, allow_nan=False)))
        if isinstance(obj, (list, tuple)):
            if not take(2): return False
            for index, item in enumerate(obj):
                if index and not take(1): return False
                if not walk(item): return False
            return True
        if isinstance(obj, dict):
            if not take(2): return False
            for index, key in enumerate(sorted(obj)):
                if not isinstance(key, str): raise TypeError('non-string JSON key')
                if index and not take(1): return False
                if not string(key) or not take(1) or not walk(obj[key]): return False
            return True
        raise TypeError('unsupported JSON value')
    return limit - remaining if walk(value) else None


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, init=False)
class PreparedContentOrigin:
    source: object
    payload: bytes
    payload_digest: str
    _seal: object

    def __init__(self, seal, source, payload):
        if seal is not _SEAL:
            raise ValueError('CORR_OWNER_PREPARATION_REQUIRED')
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'payload', payload)
        object.__setattr__(self, 'payload_digest', _sha(payload))
        object.__setattr__(self, '_seal', seal)


@dataclass(frozen=True)
class OriginalContentExportReceipt:
    receipt_id: str
    export_id: str
    payload: bytes
    receipt_digest: str


def prepare_content_origin(database, *, repertoire_id: str, content_workspace_id: str,
                           content_source_key: str) -> PreparedContentOrigin | Hold:
    """Capture exact complete facts and owner projection in one RO/RR snapshot."""
    if not all(isinstance(v, str) and v for v in (repertoire_id, content_workspace_id, content_source_key)):
        return Hold('CORR_IDENTITY_REQUIRED')
    if not database.in_transaction or not database.read_only:
        return Hold('CORR_READ_ONLY_SNAPSHOT_REQUIRED')
    scope = database.execute_native("""SELECT current_database() AS db,current_schema() AS schema,
        current_setting('transaction_isolation') AS isolation,current_setting('transaction_read_only') AS ro,
        pg_current_snapshot()::text AS snapshot,pg_backend_pid() AS pid,
        transaction_timestamp()::text AS started,
        (SELECT max(version) FROM public.tempo_schema_migrations) AS version""").fetchone()
    if scope['schema'] != 'public' or scope['isolation'] != 'repeatable read' or scope['ro'] != 'on':
        return Hold('CORR_READ_ONLY_SNAPSHOT_REQUIRED')
    actual = database.execute_native('SELECT target_id,workspace_id FROM public.legacy_learning_identity WHERE id=1').fetchone()
    if not actual or not actual['target_id'] or not actual['workspace_id']:
        return Hold('CORR_AUTHORITY_UNAVAILABLE')
    source = prepare_position_source_with_facts(database, repertoire_id)
    if isinstance(source, Hold):
        return source
    prepared = source.prepared
    token = prepared.authored_token
    if json.loads(token.header_json)['workspace_id'] != actual['workspace_id']:
        return Hold('CORR_AUTHORITY_MISMATCH')
    owner = database.execute_native("""SELECT o.mode,
        CASE WHEN octet_length(o.epoch)<=%s THEN o.epoch END AS epoch,
        octet_length(o.epoch) AS epoch_bytes,e.source_digest FROM public.study_position_owners o
        JOIN public.study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
        WHERE o.repertoire_id=%s""", (_MAX_BYTES, repertoire_id)).fetchone()
    if owner and owner['epoch_bytes'] > _MAX_BYTES:
        return Hold('CORR_EXPORT_LIMIT')
    if not owner or owner['mode'] != 'active' or not owner['epoch'] or owner['source_digest'] != token.authored_digest:
        return Hold('CORR_OWNER_EPOCH_UNAVAILABLE')
    try:
        from app.repertoire.snapshot import project_published_repertoire
        facts = source.publication_facts()
        # Reuse actual accepted projection; archive hash is a separate domain.
        checked = project_published_repertoire(facts)
        if (checked.membership.digest, checked.membership.snapshot_id, checked.membership.source_revision) != (
                token.original_source_digest, token.original_snapshot_id, token.original_source_revision):
            return Hold('CORR_SOURCE_FACTS_MISMATCH')
        occurrences = []
        routes = []
        envelope = dict(contract_version='LEGACY_CONTENT_HISTORICAL_ORIGIN.v1',
            evidence_scope='historical-repeatable-read', historical_snapshot_consistency='owner-verified',
            physical_currentness='UNQUALIFIED', physical_instance_qualification='UNQUALIFIED',
            activation_eligible=False, final_legacy_fence_receipt=None,
            legacy_authority_kind='legacy_logical_backend', legacy_target_id=actual['target_id'],
            legacy_workspace_id=actual['workspace_id'], repertoire_id=repertoire_id, owner_epoch=owner['epoch'],
            original_token=token.as_dict(), original_token_codec='AuthoredSourceToken.as_dict.v1',
            source_snapshot_codec='legacy-published-source-facts-semantic.v1',
            source_facts=facts, source_facts_sha256='0' * 64,
            original_source_digest_codec='current_published_repertoire_snapshot.fingerprint.v1',
            source_observation=dict(database=scope['db'],schema=scope['schema'],schema_version=scope['version'],
                snapshot=scope['snapshot'],backend_pid=scope['pid'],transaction_started_at=scope['started']),
            routes=routes, occurrences=occurrences, occurrence_sha256='0' * 64,
            content_workspace_expectation=content_workspace_id, content_source_key_expectation=content_source_key,
            content_projection_status='BLOCKED_AUTHORITY_SCOPE', missing=['cross_repertoire_authority_anchor'],
            content_source_bytes=None, content_source_sha256=None, base_manifest_bytes=None, base_manifest_sha256=None)
        output_bytes = _bounded_json_size(envelope, _MAX_BYTES)
        if output_bytes is None:
            return Hold('CORR_EXPORT_LIMIT')
        seen = set()
        for route in prepared.routes:
            route_record = dict(route_id=route.route_id, starting_fen=route.start_fen,
                moves=route.moves_uci, learner_color=route.learner_color,
                authority_start_ply=None, authority_scope='UNQUALIFIED')
            size = _bounded_json_size(route_record, _MAX_BYTES - output_bytes - bool(routes))
            if size is None:
                return Hold('CORR_EXPORT_LIMIT')
            output_bytes += size + bool(routes)
            routes.append(route_record)
            for item in route.decisions:
                identity = (repertoire_id, route.route_id, item.ply)
                if identity in seen or item.definition.key.repertoire_id != repertoire_id:
                    return Hold('CORR_OCCURRENCE_MISMATCH')
                if len(occurrences) >= _MAX_OCCURRENCES:
                    return Hold('CORR_EXPORT_LIMIT')
                record = dict(original_occurrence_id=dict(repertoire_id=repertoire_id, route_id=route.route_id, ply=item.ply),
                    original_ply=item.ply, original_policy_version=item.definition.policy_version,
                    variant=item.definition.key.variant, original_six_field_fen=item.fen,
                    original_position_identity=item.definition.key.position, expected_uci=item.definition.expected_uci,
                    original_source_digest=token.original_source_digest, authored_digest=token.authored_digest,
                    owner_epoch=owner['epoch'])
                size = _bounded_json_size(record, _MAX_BYTES - output_bytes - bool(occurrences))
                if size is None:
                    return Hold('CORR_EXPORT_LIMIT')
                output_bytes += size + bool(occurrences)
                seen.add(identity)
                occurrences.append(record)
        # The entire envelope, including repeated strings and digest fields, is
        # already bounded before any of these full encodings are materialized.
        envelope['source_facts_sha256'] = _sha(_encode(facts))
        envelope['occurrence_sha256'] = _sha(_encode(occurrences))
        payload = _encode(envelope)
        assert len(payload) == output_bytes
        return PreparedContentOrigin(_SEAL, prepared, payload)
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        return Hold('CORR_SOURCE_FACTS_UNSUPPORTED', str(exc))


def capture_content_origin(database, *, prepared: PreparedContentOrigin, export_id: str) -> OriginalContentExportReceipt | Hold:
    """Insert or replay exact prepared bytes; never commit.

    Replay includes the original snapshot observation, including timestamp. A
    fresh preparation is not claimed to reproduce those bytes after recreation.
    """
    if not isinstance(prepared, PreparedContentOrigin) or prepared._seal is not _SEAL:
        return Hold('CORR_OWNER_PREPARATION_REQUIRED')
    if not isinstance(export_id, str) or not export_id:
        return Hold('CORR_IDENTITY_REQUIRED')
    if database.read_only or not database.in_transaction:
        return Hold('borrowed_owner_writer_transaction_required')
    if _sha(prepared.payload) != prepared.payload_digest:
        return Hold('CORR_SOURCE_FACTS_MISMATCH')
    payload = json.loads(prepared.payload)
    token = prepared.source.authored_token
    if payload.get('original_token') != token.as_dict():
        return Hold('CORR_SOURCE_FACTS_MISMATCH')
    actual = database.execute_native('SELECT target_id,workspace_id FROM public.legacy_learning_identity WHERE id=1').fetchone()
    if not actual or (actual['target_id'], actual['workspace_id']) != (payload['legacy_target_id'], payload['legacy_workspace_id']):
        return Hold('CORR_AUTHORITY_MISMATCH')
    digest = _sha(prepared.payload)
    existing = database.execute_native("""SELECT receipt_id,payload,receipt_digest FROM public.legacy_content_historical_origins
        WHERE legacy_target_id=%s AND legacy_workspace_id=%s AND export_id=%s""",
        (actual['target_id'], actual['workspace_id'], export_id)).fetchone()
    if existing is not None:
        if bytes(existing['payload']) != prepared.payload or existing['receipt_digest'] != digest:
            return Hold('CORR_IMMUTABLE_CONFLICT')
        return OriginalContentExportReceipt(existing['receipt_id'], export_id, bytes(existing['payload']), digest)
    held = fence_position_source(database, prepared.source)
    if held is not None:
        return held
    locked_actual = database.execute_native('SELECT target_id,workspace_id FROM public.legacy_learning_identity WHERE id=1 FOR SHARE').fetchone()
    if not locked_actual or (locked_actual['target_id'], locked_actual['workspace_id']) != (actual['target_id'], actual['workspace_id']):
        return Hold('CORR_AUTHORITY_MISMATCH')
    owner = owner_mode(database, token.repertoire_id, token.authored_digest)
    if owner.disposition != 'position' or owner.epoch != payload['owner_epoch']:
        return Hold('CORR_OWNER_EPOCH_UNAVAILABLE')
    receipt_id = str(uuid.uuid4())
    row = database.execute_native("""INSERT INTO public.legacy_content_historical_origins
        (legacy_target_id,legacy_workspace_id,export_id,receipt_id,payload,receipt_digest)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (legacy_target_id,legacy_workspace_id,export_id)
        DO NOTHING RETURNING receipt_id,payload,receipt_digest""",
        (actual['target_id'], actual['workspace_id'], export_id, receipt_id, prepared.payload, digest)).fetchone()
    if row is None:
        row = database.execute_native("""SELECT receipt_id,payload,receipt_digest FROM public.legacy_content_historical_origins
            WHERE legacy_target_id=%s AND legacy_workspace_id=%s AND export_id=%s""",
            (actual['target_id'],actual['workspace_id'],export_id)).fetchone()
        if row is None or bytes(row['payload']) != prepared.payload or row['receipt_digest'] != digest:
            return Hold('CORR_IMMUTABLE_CONFLICT')
    return OriginalContentExportReceipt(row['receipt_id'], export_id, bytes(row['payload']), row['receipt_digest'])
