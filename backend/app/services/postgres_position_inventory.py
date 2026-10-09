"""Restartable position inventory with shared immutable route artifacts.

Every worker delivery reads one bounded item, computes after closing the reader,
then commits the item and cursor through the foreground-admitted writer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from ..database import background_read_connection, connection
from .canonical_prefix import read_prefix, line_origin
from .durable_tasks import (enqueue_task_in_transaction, lock_current_slice,
                           advance_task_slice_in_transaction, complete_task_slice_in_transaction)
from .position_inventory import INVENTORY_VERSION, SEGMENT_PLIES, inventory_segment
from .redis_admission_gate import background_lease
from .repertoire_comparison import canonical_fen

TASK_KIND = 'position_inventory'
RECONCILE_KIND = 'position_inventory_reconcile'
PAGE_SIZE = 64


def _now():
    return datetime.now(timezone.utc).isoformat()


def input_identity(database, repertoire_id, *, lock=False):
    repertoire = database.execute_native(
        'SELECT scope_source_revision,canonical_prefix_revision,canonical_prefix_preview_id,canonical_prefix_moves_json '
        'FROM repertoires WHERE id=%s' + (' FOR UPDATE' if lock else ''), (repertoire_id,)
    ).fetchone()
    if repertoire is None:
        return None
    graph = database.execute_native(
        "SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s AND state='ready'",
        (repertoire_id,)).fetchone()
    if graph is None:
        return None
    if json.loads(repertoire[3]) and repertoire[2] is None:
        return None
    return {'graph_generation': int(graph[0]), 'source_revision': int(repertoire[0]),
            'prefix_revision': int(repertoire[1]), 'preview_id': repertoire[2] if json.loads(repertoire[3]) else None}


def request_inventory_in_transaction(database, repertoire_id):
    """Same inputs keep a lease/cursor; this never traverses or hashes routes."""
    identity = input_identity(database, repertoire_id, lock=True)
    if identity is None:
        return None  # First graph publication will enqueue it.
    # Failure handling and inventory slices hold the task before changing their
    # generation. Match that order before superseding a builder or enqueueing.
    database.execute_native(
        'SELECT id FROM background_tasks WHERE kind=%s AND deduplication_key=%s FOR UPDATE',
        (TASK_KIND, repertoire_id)).fetchone()
    latest = database.execute_native(
        "SELECT * FROM inventory_generations WHERE repertoire_id=%s "
        "AND state IN ('building','published') ORDER BY created_at DESC,id DESC LIMIT 1",
        (repertoire_id,)).fetchone()
    if latest and all(latest[field] == value for field, value in identity.items()):
        return str(latest['id'])
    database.execute_native(
        "UPDATE inventory_generations SET state='superseded' WHERE repertoire_id=%s AND state='building'",
        (repertoire_id,))
    generation_id = str(uuid.uuid4())
    database.execute_native(
        'INSERT INTO inventory_generations(id,repertoire_id,graph_generation,source_revision,'
        "prefix_revision,preview_id,state,created_at) VALUES(%s,%s,%s,%s,%s,%s,'building',%s)",
        (generation_id, repertoire_id, identity['graph_generation'], identity['source_revision'],
         identity['prefix_revision'], identity['preview_id'], _now()))
    enqueue_task_in_transaction(database, TASK_KIND, repertoire_id,
                                {'repertoire_id': repertoire_id, 'inventory_id': generation_id,
                                 'phase': 'routes', 'after_line_id': ''}, priority=85)
    return generation_id


def _current(database, task):
    payload = task['payload']
    identity = input_identity(database, payload['repertoire_id'], lock=True)
    if not lock_current_slice(database, task):
        return False
    generation = database.execute_native('SELECT * FROM inventory_generations WHERE id=%s',
                                         (payload['inventory_id'],)).fetchone()
    if (identity is None or not generation or generation['state'] != 'building'
            or any(generation[field] != value for field, value in identity.items())):
        database.execute_native("UPDATE inventory_generations SET state='superseded' "
                                "WHERE id=%s AND state='building'", (payload['inventory_id'],))
        complete_task_slice_in_transaction(database, task)
        if identity is not None:
            request_inventory_in_transaction(database, payload['repertoire_id'])
        return False
    return True


def _advance(database, task, phase, **cursor):
    return advance_task_slice_in_transaction(
        database, task, next_phase=phase,
        next_payload={'repertoire_id': task['payload']['repertoire_id'],
                      'inventory_id': task['payload']['inventory_id'], 'phase': phase, **cursor})


def _prepare_route(database, payload):
    line = database.execute_native(
        'SELECT id,start_fen,trained_color,md5(moves_json) AS moves_hash '
        'FROM repertoire_lines WHERE repertoire_id=%s AND id>%s ORDER BY id LIMIT 1',
        (payload['repertoire_id'], payload.get('after_line_id', ''))).fetchone()
    if line is None:
        return None
    prefix = read_prefix(database, payload['repertoire_id'])
    origin = line_origin(database, prefix['preview_id'], line['start_fen']) if prefix['moves'] else []
    if prefix['moves'] and origin is None:
        raise ValueError('Canonical route certification is pending; verify the current prefix before inventorying it')
    origin = origin or []
    scope_start_ply = max(0, len(prefix['moves'])-len(origin))
    route_identity = json.dumps([INVENTORY_VERSION, line['start_fen'], line['moves_hash'],
                                 line['trained_color'], scope_start_ply, origin], separators=(',', ':'))
    return {**dict(line), 'route_id': hashlib.sha256(route_identity.encode()).hexdigest(),
            'origin_json': json.dumps(origin), 'scope_start_ply': scope_start_ply,
            'absolute_ply_offset': len(origin)}


def _stage_occurrences(database, route, occurrences):
    scoped = [position for position in occurrences
              if position['ply'] >= route['scope_start_ply']+route['absolute_ply_offset']]
    positions = {position['fen_key']: position for position in scoped if position['opponent']}
    database.execute_native(
        'INSERT INTO inventory_positions(fen_key,fen,legal_reply_count) '
        'SELECT fen_key,fen,legal_reply_count FROM jsonb_to_recordset(%s::jsonb) '
        'AS item(fen_key text,fen text,legal_reply_count bigint) ON CONFLICT DO NOTHING',
        (json.dumps([{'fen_key': position['fen_key'], 'fen': position['fen'],
                      'legal_reply_count': len(position['legal_replies'])} for position in positions.values()]),))
    database.execute_native(
        'INSERT INTO inventory_legal_replies(fen_key,move_uci,resulting_fen_key) '
        'SELECT fen_key,move_uci,resulting_fen_key FROM jsonb_to_recordset(%s::jsonb) '
        'AS item(fen_key text,move_uci text,resulting_fen_key text) ON CONFLICT DO NOTHING',
        (json.dumps([{'fen_key': position['fen_key'], **reply} for position in positions.values()
                     for reply in position['legal_replies']]),))
    database.execute_native(
        'INSERT INTO inventory_occurrences(route_id,ply,fen_key,opponent,authored_move) '
        'SELECT %s,ply,fen_key,opponent,authored_move FROM jsonb_to_recordset(%s::jsonb) '
        'AS item(ply bigint,fen_key text,opponent bigint,authored_move text) ON CONFLICT DO NOTHING',
        (route['id'], json.dumps([{'ply': position['ply'], 'fen_key': position['fen_key'],
                                  'opponent': int(position['opponent']),
                                  'authored_move': position['authored_move']} for position in scoped])))


def _route_slice(task):
    payload = task['payload']
    with background_read_connection() as database:
        prepared = _prepare_route(database, payload)
    with background_lease(), connection(background=True) as database:
        if not _current(database, task):
            return False
        if prepared is None:
            return _advance(database, task, 'responses', after_line_id='', after_decision_index=-1)
        route_id = prepared['route_id']
        database.execute_native(
            'INSERT INTO inventory_routes(id,start_fen,moves_json,trained_color,scope_start_ply,'
            'origin_json,absolute_ply_offset,checkpoint_fen) '
            'SELECT %s,start_fen,moves_json::jsonb,trained_color,%s,%s,%s,start_fen FROM repertoire_lines '
            'WHERE id=%s AND repertoire_id=%s AND md5(moves_json)=%s ON CONFLICT DO NOTHING',
            (route_id, prepared['scope_start_ply'], prepared['origin_json'], prepared['absolute_ply_offset'],
             prepared['id'], payload['repertoire_id'], prepared['moves_hash']))
        database.execute_native(
            'INSERT INTO inventory_generation_routes(generation_id,line_id,route_id) VALUES(%s,%s,%s) '
            'ON CONFLICT DO NOTHING', (payload['inventory_id'], prepared['id'], route_id))
        existing = database.execute_native('SELECT complete FROM inventory_routes WHERE id=%s FOR UPDATE',
                                            (route_id,)).fetchone()
        if existing and existing['complete']:
            return _advance(database, task, 'routes', after_line_id=prepared['id'])
        return _advance(database, task, 'traverse', line_id=prepared['id'], route_id=route_id)


def _traverse_slice(task):
    payload = task['payload']
    with background_read_connection() as database:
        route = dict(database.execute_native(
            'SELECT id,trained_color,scope_start_ply,absolute_ply_offset,completed_plies,checkpoint_fen,complete,'
            'jsonb_array_length(moves_json::jsonb) AS move_count,'
            'jsonb_path_query_array(moves_json::jsonb,format(\'$[%%s to %%s]\','
            'completed_plies,completed_plies+%s-1)::jsonpath) AS moves '
            'FROM inventory_routes WHERE id=%s', (SEGMENT_PLIES, payload['route_id'])).fetchone())
    occurrences, ending_fen = inventory_segment(
        route['checkpoint_fen'], route['moves'], route['trained_color'],
        route['completed_plies']+route['absolute_ply_offset'],
        terminal=route['completed_plies']+len(route['moves']) == route['move_count']) if not route['complete'] else ([], route['checkpoint_fen'])
    with background_lease(), connection(background=True) as database:
        if not _current(database, task):
            return False
        current = database.execute_native('SELECT completed_plies,complete FROM inventory_routes WHERE id=%s FOR UPDATE',
                                           (route['id'],)).fetchone()
        if not current['complete'] and current['completed_plies'] == route['completed_plies']:
            _stage_occurrences(database, route, occurrences)
            completed_plies = route['completed_plies']+len(route['moves'])
            database.execute_native('UPDATE inventory_routes SET completed_plies=%s,checkpoint_fen=%s,complete=%s WHERE id=%s',
                                     (completed_plies, ending_fen, int(completed_plies == route['move_count']), route['id']))
            finished = completed_plies == route['move_count']
        else:
            finished = bool(current['complete'])
        return (_advance(database, task, 'routes', after_line_id=payload['line_id']) if finished
                else _advance(database, task, 'traverse', line_id=payload['line_id'], route_id=route['id']))


def _response_slice(task):
    payload = task['payload']
    with background_read_connection() as database:
        row = database.execute_native(
            'SELECT step.line_id,step.decision_index,step.card_id,step.starting_fen,step.trained_color,card.start_fen AS card_start_fen,'
            'jsonb_array_length(step.moves_json::jsonb) AS move_count,'
            'jsonb_path_query_array(step.moves_json::jsonb,%s::jsonpath) AS moves '
            'FROM opening_graph_steps step JOIN inventory_generations inventory '
            'ON inventory.repertoire_id=step.repertoire_id AND inventory.graph_generation=step.generation '
            'JOIN repertoire_cards link ON link.repertoire_id=step.repertoire_id AND link.card_id=step.card_id '
            "JOIN cards card ON card.id=step.card_id AND card.archived=0 AND card.content_type='opening' "
            'WHERE inventory.id=%s AND card.moves_json::jsonb=step.moves_json::jsonb '
            'AND (step.line_id,step.decision_index)>(%s,%s) '
            'ORDER BY step.line_id,step.decision_index LIMIT 1',
            (f"$[{payload.get('step_offset', 0)} to {payload.get('step_offset', 0)+SEGMENT_PLIES-1}]",
             payload['inventory_id'], payload.get('after_line_id', ''), payload.get('after_decision_index', -1))).fetchone()
    responses, ending_fen = ([], None)
    if row is not None:
        occurrences, ending_fen = inventory_segment(payload.get('step_fen', row['starting_fen']),
            row['moves'], row['trained_color'], payload.get('step_offset', 0), terminal=False)
        responses = [{'fen_key': position['fen_key'], 'move_uci': position['authored_move'],
                      'card_id': row['card_id'], 'line_id': row['line_id']}
                     for position in occurrences if not position['opponent'] and position['authored_move']
                     and canonical_fen(row['card_start_fen']) == canonical_fen(row['starting_fen'])]
    with background_lease(), connection(background=True) as database:
        if not _current(database, task):
            return False
        if row is None:
            return _advance(database, task, 'publish')
        database.execute_native(
            'INSERT INTO inventory_responses(generation_id,fen_key,move_uci,card_id,line_id) '
            'SELECT %s,fen_key,move_uci,card_id,line_id FROM jsonb_to_recordset(%s::jsonb) '
            'AS item(fen_key text,move_uci text,card_id text,line_id text) '
            'WHERE EXISTS(SELECT 1 FROM inventory_generation_routes membership JOIN inventory_occurrences occurrence '
            'ON occurrence.route_id=membership.route_id WHERE membership.generation_id=%s '
            'AND membership.line_id=item.line_id AND occurrence.opponent=0 '
            'AND occurrence.fen_key=item.fen_key AND occurrence.authored_move=item.move_uci) ON CONFLICT DO NOTHING',
            (payload['inventory_id'], json.dumps(responses), payload['inventory_id']))
        next_offset = payload.get('step_offset', 0)+len(row['moves'])
        if next_offset < row['move_count']:
            return _advance(database, task, 'responses', after_line_id=payload.get('after_line_id', ''),
                            after_decision_index=payload.get('after_decision_index', -1),
                            step_offset=next_offset, step_fen=ending_fen)
        return _advance(database, task, 'responses', after_line_id=row['line_id'],
                        after_decision_index=row['decision_index'])


def execute_inventory_slice(task):
    phase = task['payload'].get('phase', 'routes')
    if phase == 'routes':
        return _route_slice(task)
    if phase == 'traverse':
        return _traverse_slice(task)
    if phase == 'responses':
        return _response_slice(task)
    if phase == 'publish':
        with background_lease(), connection(background=True) as database:
            if not _current(database, task):
                return False
            pending = database.execute_native(
                'SELECT 1 FROM inventory_generation_routes membership JOIN inventory_routes route ON route.id=membership.route_id '
                'WHERE membership.generation_id=%s AND route.complete=0 LIMIT 1', (task['payload']['inventory_id'],)).fetchone()
            if pending:
                raise ValueError('Inventory contains an unfinished route')
            database.execute_native("UPDATE inventory_generations SET state='published',published_at=%s WHERE id=%s",
                                     (_now(), task['payload']['inventory_id']))
            database.execute_native('INSERT INTO inventory_publications(repertoire_id,generation_id) VALUES(%s,%s) '
                                    'ON CONFLICT(repertoire_id) DO UPDATE SET generation_id=excluded.generation_id',
                                    (task['payload']['repertoire_id'], task['payload']['inventory_id']))
            return _advance(database, task, 'cleanup')
    if phase != 'cleanup':
        raise ValueError(f'Unknown inventory phase: {phase}')
    # Retain the published generation plus the active builder; delete stale
    # membership/response rows in bounded pages before deleting their headers.
    with background_lease(), connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        for table in ('inventory_generation_routes', 'inventory_responses'):
            deleted = database.execute_native(
                f'DELETE FROM {table} WHERE ctid IN (SELECT item.ctid FROM {table} item '
                'JOIN inventory_generations generation ON generation.id=item.generation_id '
                'LEFT JOIN inventory_publications publication ON publication.repertoire_id=generation.repertoire_id '
                "WHERE generation.repertoire_id=%s AND generation.state!='building' "
                'AND generation.id IS DISTINCT FROM publication.generation_id LIMIT %s) RETURNING 1',
                (task['payload']['repertoire_id'], PAGE_SIZE)).fetchall()
            if deleted:
                return _advance(database, task, 'cleanup')
        database.execute_native(
            'DELETE FROM inventory_generations WHERE id IN (SELECT generation.id FROM inventory_generations generation '
            'LEFT JOIN inventory_publications publication ON publication.repertoire_id=generation.repertoire_id '
            "WHERE generation.repertoire_id=%s AND generation.state!='building' "
            'AND generation.id IS DISTINCT FROM publication.generation_id LIMIT %s)',
            (task['payload']['repertoire_id'], PAGE_SIZE))
        orphan = database.execute_native(
            'SELECT route.id FROM inventory_routes route WHERE NOT EXISTS('
            'SELECT 1 FROM inventory_generation_routes membership WHERE membership.route_id=route.id) '
            'ORDER BY route.id LIMIT 1 FOR UPDATE OF route SKIP LOCKED').fetchone()
        if orphan:
            deleted = database.execute_native(
                'DELETE FROM inventory_occurrences WHERE ctid IN (SELECT ctid FROM inventory_occurrences '
                'WHERE route_id=%s LIMIT %s) RETURNING 1', (orphan[0], PAGE_SIZE)).fetchall()
            if not deleted:
                database.execute_native('DELETE FROM inventory_routes WHERE id=%s', (orphan[0],))
            return _advance(database, task, 'cleanup')
        return complete_task_slice_in_transaction(database, task)


def execute_reconcile_slice(task):
    """Upgrade sweep visits one repertoire per delivery; no startup computation."""
    with background_read_connection() as database:
        row = database.execute_native('SELECT id FROM repertoires WHERE id>%s ORDER BY id LIMIT 1',
                                       (task['payload'].get('after_repertoire_id', ''),)).fetchone()
    with background_lease(), connection(background=True) as database:
        if row is not None:
            # Repertoire -> reconcile task -> inventory task, matching all
            # requests that can wait on a foreground repertoire mutation.
            database.execute_native('SELECT id FROM repertoires WHERE id=%s FOR UPDATE',
                                    (row[0],)).fetchone()
        if not lock_current_slice(database, task):
            return False
        if row is None:
            return complete_task_slice_in_transaction(database, task)
        request_inventory_in_transaction(database, row[0])
        return advance_task_slice_in_transaction(database, task, next_phase='reconcile',
                                                 next_payload={'after_repertoire_id': row[0]})


def inventory_progress(database, repertoire_id):
    identity = input_identity(database, repertoire_id)
    publication = database.execute_native(
        'SELECT generation.* FROM repertoires repertoire LEFT JOIN inventory_publications publication '
        'ON publication.repertoire_id=repertoire.id LEFT JOIN inventory_generations generation '
        'ON generation.id=publication.generation_id WHERE repertoire.id=%s', (repertoire_id,)).fetchone()
    if publication is None:
        raise KeyError('Repertoire not found')
    task = database.execute_native('SELECT state,phase,last_error,payload_json FROM background_tasks '
                                   'WHERE kind=%s AND deduplication_key=%s', (TASK_KIND, repertoire_id)).fetchone()
    published_id = publication['id']
    stale = bool(published_id and (identity is None or any(publication[field] != value for field, value in identity.items())))
    return {'publication_id': published_id, 'stale': stale, 'state': task['state'] if task else 'pending',
            'phase': task['phase'] if task else None, 'last_error': task['last_error'] if task else None,
            'input_identity': identity}


def _page_limit(limit):
    if not 1 <= limit <= 256:
        raise ValueError('Inventory page size must be between 1 and 256')
    return limit


def published_positions(database, repertoire_id, *, after_fen_key='', limit=PAGE_SIZE):
    """Return shared nodes once, with their exact legal-reply count, never mass."""
    progress = inventory_progress(database, repertoire_id)
    rows = database.execute_native(
        'SELECT position.* FROM inventory_positions position WHERE position.fen_key>%s '
        'AND EXISTS(SELECT 1 FROM inventory_occurrences occurrence JOIN inventory_generation_routes membership '
        'ON membership.route_id=occurrence.route_id WHERE membership.generation_id=%s '
        'AND occurrence.opponent=1 AND occurrence.fen_key=position.fen_key) '
        'ORDER BY position.fen_key LIMIT %s',
        (after_fen_key, progress['publication_id'], _page_limit(limit))).fetchall()
    return {**progress, 'positions': [dict(row) for row in rows],
            'next_fen_key': rows[-1]['fen_key'] if len(rows) == limit else None}


def legal_replies(database, fen_key, *, after_move_uci='', limit=256):
    return [dict(row) for row in database.execute_native(
        'SELECT move_uci,resulting_fen_key FROM inventory_legal_replies WHERE fen_key=%s AND move_uci>%s '
        'ORDER BY move_uci LIMIT %s', (fen_key, after_move_uci, _page_limit(limit))).fetchall()]


def route_coverage(database, generation_id, line_id, ply, *, after_move_uci='', limit=256):
    """Authored reply, route response and transposition response remain distinct.

A transposition is supported only by an actual, published learner response in
this repertoire generation. Terminal authored moves alone never imply support.
"""
    rows = database.execute_native(
        'SELECT reply.move_uci,reply.resulting_fen_key,position.legal_reply_count,'
        '(occurrence.authored_move=reply.move_uci) AS authored,'
        'EXISTS(SELECT 1 FROM inventory_occurrences answer JOIN inventory_responses response '
        'ON response.generation_id=membership.generation_id AND response.line_id=membership.line_id '
        'AND response.fen_key=answer.fen_key AND response.move_uci=answer.authored_move '
        'WHERE answer.route_id=occurrence.route_id AND answer.ply=occurrence.ply+1 '
        'AND answer.opponent=0 AND occurrence.authored_move=reply.move_uci) AS route_response,'
        'EXISTS(SELECT 1 FROM inventory_responses response WHERE response.generation_id=membership.generation_id '
        'AND response.fen_key=reply.resulting_fen_key) AS transposition_response '
        'FROM inventory_generation_routes membership JOIN inventory_occurrences occurrence '
        'ON occurrence.route_id=membership.route_id JOIN inventory_positions position ON position.fen_key=occurrence.fen_key '
        'JOIN inventory_legal_replies reply ON reply.fen_key=occurrence.fen_key '
        'WHERE membership.generation_id=%s AND membership.line_id=%s AND occurrence.ply=%s '
        'AND occurrence.opponent=1 AND reply.move_uci>%s ORDER BY reply.move_uci LIMIT %s',
        (generation_id, line_id, ply, after_move_uci, _page_limit(limit))).fetchall()
    return [{**dict(row), 'authored': bool(row['authored']),
             'transposition_response': bool(row['transposition_response'] and not row['route_response']),
             'coverage': 'route' if row['route_response'] else 'transposition' if row['transposition_response'] else 'missing'}
            for row in rows]


def source_cohort_key(fen, *, source_id, model_version, schema_version, rating_cohort, speed_cohort=None):
    """Callers supply actual source-supported cohorts; no invented bucket mapping."""
    fields = (source_id, model_version, schema_version, rating_cohort)
    if not all(isinstance(field, str) and field.strip() for field in fields):
        raise ValueError('Source identity, versions, and supported rating cohort are required')
    if speed_cohort is not None and (not isinstance(speed_cohort, str) or not speed_cohort.strip()):
        raise ValueError('Unsupported speed uses None; a supported cohort must be nonempty')
    return (canonical_fen(fen), *fields, speed_cohort or '')


def source_evidence(database, key):
    """Unknown evidence is absence, never a fabricated zero or cache row."""
    row = database.execute_native(
        'SELECT * FROM position_cohort_evidence WHERE '
        '(fen_key,source_id,model_version,schema_version,rating_cohort,speed_cohort)=(%s,%s,%s,%s,%s,%s)', key).fetchone()
    return dict(row) if row else None


def published_routes(database, repertoire_id, *, after_line_id='', limit=PAGE_SIZE):
    progress = inventory_progress(database, repertoire_id)
    rows = database.execute_native(
        'SELECT membership.line_id,membership.route_id,route.start_fen,route.trained_color,'
        'route.scope_start_ply,route.absolute_ply_offset FROM inventory_generation_routes membership '
        'JOIN inventory_routes route ON route.id=membership.route_id '
        'WHERE membership.generation_id=%s AND membership.line_id>%s ORDER BY membership.line_id LIMIT %s',
        (progress['publication_id'], after_line_id, _page_limit(limit))).fetchall()
    return {**progress, 'routes': [dict(row) for row in rows],
            'next_line_id': rows[-1]['line_id'] if len(rows) == limit else None}


def route_occurrences(database, generation_id, line_id, *, after_ply=-1, limit=PAGE_SIZE):
    return [dict(row) for row in database.execute_native(
        'SELECT occurrence.* FROM inventory_generation_routes membership JOIN inventory_occurrences occurrence '
        'ON occurrence.route_id=membership.route_id WHERE membership.generation_id=%s AND membership.line_id=%s '
        'AND occurrence.ply>%s ORDER BY occurrence.ply LIMIT %s',
        (generation_id, line_id, after_ply, _page_limit(limit))).fetchall()]
