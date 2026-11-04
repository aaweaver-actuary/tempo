"""Same-snapshot historical legacy authored catalogue authority observations.

This capability neither observes Learning retirement nor provides currentness,
physical identity, publication, persistence, or activation authority.
"""
from dataclasses import dataclass
import hashlib
import json

import chess
from tempo_chess_core import canonical_fen_key
from tempo_evidence_math.position_learning import Hold
from .content_origin import PreparedContentOrigin, _SEAL as _ORIGIN_SEAL
from .position_study_source import _owner_header

_SEAL = object()
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_FIELDS = {
    'repertoires': ('id', 'name', 'is_main', 'scope_source_revision', 'canonical_prefix_revision', 'canonical_prefix_moves_json', 'canonical_prefix_preview_id'),
    'repertoire_lines': ('id', 'repertoire_id', 'trained_color', 'start_fen', 'moves_json'),
}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _bounded_json_size(value, limit):
    """Count canonical escaped UTF-8 incrementally without building output."""
    total = 0
    for chunk in json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                  allow_nan=False).iterencode(value):
        total += len(chunk.encode('utf-8'))
        if total > limit:
            return None
    return total


def _bounded_prefixes(root, moves, limit):
    """Bound repeated policies before sorting or appending each expanded list."""
    node, prefixes, size = root, [], 2
    for index in range(len(moves) + 1):
        if index:
            node = node['children'][moves[index - 1]]
            size += 1
        size += 2
        for policy in node['policies']:
            encoded = json.encoder.encode_basestring(policy).encode('utf-8')
            size += len(encoded) + 1
            if size - 1 > limit:
                return None
        if node['policies']:
            size -= 1
        if size > limit:
            return None
        prefixes.append(sorted(node['policies']))
    return prefixes


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, init=False)
class PreparedCrossRepertoireAuthorityScope:
    payload: bytes
    artifact_sha256: str
    _seal: object

    def __init__(self, seal, payload, digest):
        if seal is not _SEAL:
            raise ValueError('AUTH_SCOPE_OWNER_PREPARATION_REQUIRED')
        object.__setattr__(self, 'payload', payload)
        object.__setattr__(self, 'artifact_sha256', digest)
        object.__setattr__(self, '_seal', seal)


def _read_stream(database, table):
    """Own count, highwater and keyset paging inside the caller's snapshot."""
    columns = _FIELDS[table]
    predicate = "left(id,2)<>'__'" if table == 'repertoires' else "left(repertoire_id,2)<>'__'"
    count = database.execute_native(f'SELECT count(*) AS n FROM public.{table} WHERE {predicate}').fetchone()['n']
    if count > 10000:
        return Hold('AUTH_SCOPE_LIMIT')
    top = database.execute_native(f'SELECT id FROM public.{table} WHERE {predicate} ORDER BY id COLLATE "C" DESC LIMIT 1').fetchone()
    highwater = top['id'] if top else None
    rows, cursor, size = [], None, 0
    # The CASE masks every source value before transfer when a row is too big.
    length = ' + '.join(f'coalesce(octet_length({c}::text),0)' for c in columns)
    selected = ','.join(f'CASE WHEN ({length})<=65536 THEN {c} END AS {c}' for c in columns)
    while len(rows) < count:
        page = database.execute_native(f'''SELECT {selected},({length}) AS _bytes FROM public.{table}
            WHERE {predicate} AND (%s::text IS NULL OR id COLLATE "C">%s COLLATE "C")
              AND id COLLATE "C"<=%s COLLATE "C" ORDER BY id COLLATE "C" LIMIT 256''',
            (cursor, cursor, highwater)).fetchall()
        if not page or len(page) > 256:
            return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
        for row in page:
            if row['_bytes'] > 65536 or row['id'] is None:
                return Hold('AUTH_SCOPE_LIMIT')
            item = {c: row[c] for c in columns}
            if cursor is not None and item['id'] <= cursor:
                return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
            cursor = item['id']
            size += len(_encode(item))
            if size > 32 * 1024 * 1024:
                return Hold('AUTH_SCOPE_LIMIT')
            rows.append(item)
        if len(rows) > count:
            return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
    if (cursor != highwater if count else highwater is not None):
        return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
    return rows, highwater, size


def prepare_cross_repertoire_authority_scope(database, *, prepared_origin: PreparedContentOrigin) -> PreparedCrossRepertoireAuthorityScope | Hold:
    if not isinstance(prepared_origin, PreparedContentOrigin) or prepared_origin._seal is not _ORIGIN_SEAL:
        return Hold('AUTH_SCOPE_OWNER_PREPARATION_REQUIRED')
    if not database.in_transaction or not database.read_only:
        return Hold('AUTH_SCOPE_READ_SNAPSHOT_REQUIRED')
    if _sha(prepared_origin.payload) != prepared_origin.payload_digest:
        return Hold('AUTH_SCOPE_OWNER_PREPARATION_REQUIRED')
    origin = json.loads(prepared_origin.payload)
    scope = database.execute_native("""SELECT current_schema() AS schema,
        current_setting('transaction_isolation') AS isolation,current_setting('transaction_read_only') AS ro,
        pg_current_snapshot()::text AS snapshot,pg_backend_pid() AS pid,transaction_timestamp()::text AS started,
        (SELECT max(version) FROM public.tempo_schema_migrations) AS version,
        (SELECT generation FROM public.repertoire_game_scope WHERE id=1) AS generation""").fetchone()
    if scope['schema'] != 'public' or scope['isolation'] != 'repeatable read' or scope['ro'] != 'on':
        return Hold('AUTH_SCOPE_READ_SNAPSHOT_REQUIRED')
    observation = origin['source_observation']
    if (scope['snapshot'], scope['pid'], scope['started'], scope['version']) != (observation['snapshot'], observation['backend_pid'], observation['transaction_started_at'], observation['schema_version']):
        return Hold('AUTH_SCOPE_ORIGINAL_SNAPSHOT_UNAVAILABLE')
    if scope['generation'] is None:
        return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
    actual = database.execute_native('SELECT target_id,workspace_id FROM public.legacy_learning_identity WHERE id=1').fetchone()
    if not actual or (actual['target_id'],actual['workspace_id']) != (origin['legacy_target_id'],origin['legacy_workspace_id']):
        return Hold('AUTH_SCOPE_AUTHORITY_MISMATCH')
    source = prepared_origin.source
    token = source.authored_token
    if token.as_dict() != origin['original_token'] or token.repertoire_id != origin['repertoire_id']:
        return Hold('AUTH_SCOPE_OWNER_PREPARATION_REQUIRED')
    try:
        header, integrity = _owner_header(database, token.repertoire_id, lock=False)
        if header != json.loads(token.header_json) or integrity != json.loads(token.integrity_json):
            return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
        owner = database.execute_native('''SELECT o.mode,o.epoch,e.source_digest FROM public.study_position_owners o
            JOIN public.study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
            WHERE o.repertoire_id=%s''', (token.repertoire_id,)).fetchone()
        if not owner or owner['mode'] != 'active' or owner['epoch'] != origin['owner_epoch'] or owner['source_digest'] != token.authored_digest:
            return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
        reps = _read_stream(database, 'repertoires')
        if isinstance(reps, Hold): return reps
        lines = _read_stream(database, 'repertoire_lines')
        if isinstance(lines, Hold): return lines
        repertoires, rep_high, rep_size = reps
        line_rows, line_high, line_size = lines
        if rep_size + line_size > 32 * 1024 * 1024: return Hold('AUTH_SCOPE_LIMIT')
        rep_ids = {r['id'] for r in repertoires}
        if token.repertoire_id not in rep_ids or len(rep_ids) != len(repertoires):
            return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
        selected_lines = {r['id']: r for r in origin['source_facts']['lines']}
        selected_native = {r['id']: r for r in line_rows if r['repertoire_id'] == token.repertoire_id}
        if set(selected_lines) != set(selected_native) or any(
            any(selected_lines[k][field] != row[field] for field in _FIELDS['repertoire_lines'])
            for k, row in selected_native.items()):
            return Hold('AUTH_SCOPE_SOURCE_INCOMPLETE')
        trees, parsed, total_plies = {}, {}, 0
        for row in line_rows:
            if row['repertoire_id'] not in rep_ids or row['trained_color'] not in ('white', 'black'):
                return Hold('AUTH_SCOPE_CATALOGUE_INVALID')
            moves = json.loads(row['moves_json'])
            board = chess.Board(row['start_fen'])
            if not board.is_valid() or not isinstance(moves, list) or len(moves) > 1000 or any(not isinstance(m, str) for m in moves):
                return Hold('AUTH_SCOPE_CATALOGUE_INVALID')
            total_plies += len(moves)
            if total_plies > 100000: return Hold('AUTH_SCOPE_LIMIT')
            root = canonical_fen_key(board.fen(en_passant='legal'))
            node = trees.setdefault((row['trained_color'], root), {'policies': set(), 'children': {}})
            node['policies'].add(row['repertoire_id'])
            for text in moves:
                move = chess.Move.from_uci(text)
                if not board.is_legal(move): return Hold('AUTH_SCOPE_CATALOGUE_INVALID')
                board.push(move)
                node = node['children'].setdefault(text, {'policies': set(), 'children': {}})
                node['policies'].add(row['repertoire_id'])
            parsed[row['id']] = (moves, root)
        scopes, by_route = [], {}
        mapped, seen = [], set()
        artifact = dict(contract_version='LEGACY_CROSS_REPERTOIRE_SCOPE.v1',evidence_scope='historical-repeatable-read',
            authority_family='legacy_authored_catalogue_snapshot',authority_rule='legacy-ordered-prefix-unique-repertoire.v1',
            legacy_authority_kind='legacy_logical_backend',legacy_target_id=actual['target_id'],legacy_workspace_id=actual['workspace_id'],
            physical_currentness='UNQUALIFIED',physical_instance_qualification='UNQUALIFIED',activation_eligible=False,final_legacy_fence_receipt=None,
            original_origin_payload_sha256=prepared_origin.payload_digest,original_source_token=token.as_dict(),original_owner_epoch=owner['epoch'],
            observation=dict(snapshot_id=scope['snapshot'],backend_pid=scope['pid'],transaction_started_at=scope['started'],schema_version=scope['version'],catalogue_generation=scope['generation']),
            catalogue=dict(repertoires=repertoires,lines=line_rows,repertoire_count=len(repertoires),line_count=len(line_rows),
                repertoire_highwater=rep_high,line_highwater=line_high,repertoire_stream_digest=_sha(_encode(repertoires)),
                line_stream_digest=_sha(_encode(line_rows)),selection='all-non-internal-native-authored-lines'),
            route_scopes=scopes,occurrence_map=mapped,artifact_sha256='0' * 64)
        output_bytes = _bounded_json_size(artifact, _MAX_ARTIFACT_BYTES)
        if output_bytes is None: return Hold('AUTH_SCOPE_LIMIT')
        for route in source.routes:
            row = selected_native.get(route.route_id)
            if not row: return Hold('AUTH_SCOPE_OCCURRENCE_MISMATCH')
            moves, root = parsed[route.route_id]
            if (row['start_fen'],row['trained_color'],tuple(moves)) != (route.start_fen,route.learner_color,route.moves_uci):
                return Hold('AUTH_SCOPE_OCCURRENCE_MISMATCH')
            node = trees[(route.learner_color, root)]
            prefixes = _bounded_prefixes(node, moves, _MAX_ARTIFACT_BYTES - output_bytes)
            if prefixes is None: return Hold('AUTH_SCOPE_LIMIT')
            anchor = next((i for i, policies in enumerate(prefixes) if policies == [token.repertoire_id]), None)
            record = dict(original_repertoire_id=token.repertoire_id,original_route_id=route.route_id,
                learner_color=route.learner_color,original_starting_fen=route.start_fen,ordered_moves=moves,
                root_policy_key=root,prefix_policies=prefixes,authority_start_ply=anchor,
                anchor_status='UNIQUE_PREFIX' if anchor is not None else 'AMBIGUOUS_FAMILY')
            size = _bounded_json_size(record, _MAX_ARTIFACT_BYTES - output_bytes - bool(scopes))
            if size is None: return Hold('AUTH_SCOPE_LIMIT')
            output_bytes += size + bool(scopes)
            scopes.append(record)
            by_route[route.route_id] = anchor
        if len(by_route) != len(selected_native): return Hold('AUTH_SCOPE_OCCURRENCE_MISMATCH')
        expected = []
        for route in source.routes:
            for item in route.decisions:
                expected.append((route.route_id,item))
        stored = origin['occurrences']
        if len(expected) > 100000: return Hold('AUTH_SCOPE_LIMIT')
        if len(expected) != len(stored): return Hold('AUTH_SCOPE_OCCURRENCE_MISMATCH')
        for (route_id, item), original in zip(expected, stored):
            identity = dict(repertoire_id=token.repertoire_id,route_id=route_id,ply=item.ply)
            key = (route_id,item.ply)
            if key in seen or original['original_occurrence_id'] != identity or original['original_ply'] != item.ply or original['variant'] != item.definition.key.variant or item.definition.key.repertoire_id != token.repertoire_id or original['original_policy_version'] != item.definition.policy_version or original['original_six_field_fen'] != item.fen or original['original_position_identity'] != item.definition.key.position or original['expected_uci'] != item.definition.expected_uci or original['original_source_digest'] != token.original_source_digest or original['authored_digest'] != token.authored_digest or original['owner_epoch'] != owner['epoch']:
                return Hold('AUTH_SCOPE_OCCURRENCE_MISMATCH')
            seen.add(key)
            anchor = by_route[route_id]
            record = dict(original_occurrence_id=identity,original_policy_version=item.definition.policy_version,
                original_source_digest=token.original_source_digest,authored_digest=token.authored_digest,owner_epoch=owner['epoch'],
                original_six_field_fen=item.fen,original_position_identity=item.definition.key.position,expected_uci=item.definition.expected_uci,
                content_repertoire_key=token.repertoire_id,content_policy_key=token.repertoire_id,content_route_key=route_id,
                content_route_ply=item.ply,content_authority_start_ply=anchor,
                status='AMBIGUOUS_FAMILY' if anchor is None else ('BEFORE_AUTHORITY_ANCHOR' if item.ply < anchor else 'QUALIFIED_IN_SNAPSHOT'))
            size = _bounded_json_size(record, _MAX_ARTIFACT_BYTES - output_bytes - bool(mapped))
            if size is None: return Hold('AUTH_SCOPE_LIMIT')
            output_bytes += size + bool(mapped)
            mapped.append(record)
        del artifact['artifact_sha256']
        digest = _sha(_encode(artifact))
        artifact['artifact_sha256'] = digest
        payload = _encode(artifact)
        assert len(payload) == output_bytes
        return PreparedCrossRepertoireAuthorityScope(_SEAL, payload, digest)
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        return Hold('AUTH_SCOPE_CATALOGUE_INVALID')
