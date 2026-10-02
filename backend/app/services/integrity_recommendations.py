"""One-source-per-slice repair previews; no source edits or training writes."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

import chess
from fastapi import HTTPException

from .. import postgres_store
from ..database import background_read_connection, connection, read_connection
from .activity_gate import activity_gate
from .durable_tasks import (advance_task_slice_in_transaction, complete_task_slice_in_transaction,
                            enqueue_task_in_transaction, lock_current_slice)
from .discovery_admission import (_full_history_request, _rank_candidates, _repertoire_positions,
                                  continuation_recommendation)
from .threat_pipeline import report_from_json, validate_analysis_report


def _now():
    return datetime.now(timezone.utc).isoformat()


def issue_snapshot(database, repertoire_id, issue_id, signature):
    row = database.execute(
        """SELECT issue.*,state.scan_generation,state.scan_status,
             COALESCE(graph.generation,0) graph_generation
           FROM repertoire_integrity_issues issue
           JOIN repertoire_integrity_state state ON state.repertoire_id=issue.repertoire_id
           LEFT JOIN background_tasks graph ON graph.kind='opening_graph_rebuild'
             AND graph.deduplication_key=issue.repertoire_id
           WHERE issue.id=? AND issue.repertoire_id=?""", (issue_id, repertoire_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Integrity issue not found; refresh repertoire repair")
    if row['signature'] != signature:
        raise HTTPException(409, "This integrity issue changed; refresh repertoire repair")
    if row['scan_status'] != 'idle':
        raise HTTPException(409, "Repertoire validation is in progress; resume repair after it finishes")
    return dict(row)


def _current(database, record):
    try:
        issue = issue_snapshot(database, record['repertoire_id'], record['issue_id'], record['signature'])
    except HTTPException:
        return False
    return (issue['scan_generation'] == record['scan_generation']
            and issue['graph_generation'] == record['graph_generation'])


def _source(database, repertoire_id, descriptor):
    if descriptor['type'] == 'line':
        row = database.execute(
            'SELECT id,name,start_fen,moves_json,trained_color FROM repertoire_lines WHERE id=? AND repertoire_id=?',
            (descriptor['id'], repertoire_id),
        ).fetchone()
    else:
        row = database.execute(
            """SELECT id,id name,start_fen,moves_json,trained_color FROM cards
               WHERE id=? AND archived=0 AND content_type='opening'
                 AND (repertoire_id=? OR EXISTS(SELECT 1 FROM repertoire_cards
                     WHERE card_id=cards.id AND repertoire_id=?))""",
            (descriptor['id'], repertoire_id, repertoire_id),
        ).fetchone()
    return dict(row) if row else None


def _source_current(database, record):
    if not record['source_json']:
        return True
    expected = json.loads(record['source_json'])
    actual = _source(database, record['repertoire_id'], expected)
    return actual is not None and all(actual[field] == expected[field]
        for field in ('start_fen', 'moves_json', 'trained_color'))


def admit_recommendation(database, payload):
    repertoire_id, issue_id, signature = (payload[key] for key in ('repertoire_id', 'issue_id', 'signature'))
    issue = issue_snapshot(database, repertoire_id, issue_id, signature)
    prior = database.execute('SELECT * FROM integrity_recommendation_requests WHERE issue_id=?', (issue_id,)).fetchone()
    if prior and _current(database, prior) and _source_current(database, prior):
        task = database.execute('SELECT state FROM background_tasks WHERE id=?', (prior['task_id'],)).fetchone()
        engine = database.execute('SELECT state FROM threat_analysis_requests WHERE id=?', (prior['request_id'],)).fetchone()
        if (task and task['state'] != 'failed' and (not engine or engine['state'] != 'failed')
                and prior['state'] != 'failed'):
            return {'task_id': prior['task_id'], 'repertoire_id': repertoire_id,
                    'issue_id': issue_id, 'signature': signature, 'state': prior['state']}
    task = enqueue_task_in_transaction(database, 'integrity_recommendation', issue_id,
        {'repertoire_id': repertoire_id, 'issue_id': issue_id, 'signature': signature,
         'phase': 'route', 'source_offset': 0}, priority=85)
    database.execute(
        """INSERT INTO integrity_recommendation_requests(
             issue_id,repertoire_id,signature,scan_generation,graph_generation,state,
             task_id,created_at,updated_at) VALUES(?,?,?,?,?,'waiting',?,?,?)
           ON CONFLICT(issue_id) DO UPDATE SET signature=excluded.signature,
             scan_generation=excluded.scan_generation,graph_generation=excluded.graph_generation,
             state='waiting',request_id=NULL,source_json=NULL,accumulation_json=NULL,
             preview_json=NULL,error=NULL,task_id=excluded.task_id,updated_at=excluded.updated_at""",
        (issue_id, repertoire_id, signature, issue['scan_generation'], issue['graph_generation'],
         task['id'], _now(), _now()),
    )
    return {'task_id': task['id'], 'repertoire_id': repertoire_id, 'issue_id': issue_id,
            'signature': signature, 'state': 'waiting'}


def recommendation_status(repertoire_id, issue_id, signature):
    with read_connection() as database:
        issue_snapshot(database, repertoire_id, issue_id, signature)
        record = database.execute('SELECT * FROM integrity_recommendation_requests WHERE issue_id=?', (issue_id,)).fetchone()
        identity = {'repertoire_id': repertoire_id, 'issue_id': issue_id, 'signature': signature}
        if record is None:
            return {**identity, 'state': 'waiting', 'reason': 'Preparing a repertoire recommendation', 'candidates': []}
        if not _current(database, record) or not _source_current(database, record):
            raise HTTPException(409, 'Recommendation evidence changed; refresh repertoire repair')
        engine = database.execute('SELECT state,last_error FROM threat_analysis_requests WHERE id=?', (record['request_id'],)).fetchone()
        task = database.execute('SELECT state,last_error FROM background_tasks WHERE id=?', (record['task_id'],)).fetchone()
        failure = (engine['last_error'] if engine and engine['state'] == 'failed' else
                   task['last_error'] if task and task['state'] == 'failed' else record['error'])
        if failure:
            return {**identity, 'state': 'failed', 'reason': failure, 'candidates': []}
        if record['preview_json']:
            return {**identity, **json.loads(record['preview_json'])}
        return {**identity, 'state': record['state'], 'candidates': [],
                'reason': 'Docker Stockfish is preparing a full-history continuation search'}


def wake_report_previews(database, request_id):
    # A report can have multiple readers. A durable cursor wakes one reader at a time.
    if database.execute('SELECT 1 FROM integrity_recommendation_requests WHERE request_id=? LIMIT 1', (request_id,)).fetchone():
        enqueue_task_in_transaction(database, 'integrity_recommendation', 'report:' + request_id,
            {'phase': 'wake', 'request_id': request_id, 'after_issue_id': ''}, priority=85)


def _lock(database, task):
    if postgres_store.configured() and not lock_current_slice(database, task):
        return False
    lease = database.execute("SELECT generation,lease_token,state,lease_expires_at FROM background_tasks WHERE id=?", (task["id"],)).fetchone()
    return bool(lease and lease["generation"] == task["generation"]
                and lease["lease_token"] == task["lease_token"] and lease["state"] == "leased"
                and lease['lease_expires_at'] and lease['lease_expires_at'] > _now())


def _finish(database, task, record, preview):
    database.execute('UPDATE integrity_recommendation_requests SET state=?,preview_json=?,updated_at=? WHERE issue_id=?',
        (preview['state'], json.dumps(preview), _now(), record['issue_id']))
    complete_task_slice_in_transaction(database, task)
    return False


def _merge(accumulated, page):
    if not accumulated:
        return page
    for candidate, addition in zip(accumulated['candidates'], page['candidates'], strict=True):
        candidate['repertoire_line_count'] += addition['repertoire_line_count']
        if (addition['exact_transposition'] and not candidate['exact_transposition'] or
            addition['familiar'] and not candidate['familiar']):
            for field in ('similarity', 'familiar', 'exact_transposition', 'example_line_id', 'example_line_name'):
                candidate[field] = addition[field]
        if len(addition['preview_moves_uci']) < len(candidate['preview_moves_uci']):
            candidate['preview_moves_uci'] = addition['preview_moves_uci']
    accumulated['accepted_moves_uci'] = sorted(set(accumulated['accepted_moves_uci'] + page['accepted_moves_uci']))
    return accumulated


def execute_integrity_recommendation_slice(task):
    activity_gate.wait_for_foreground()
    payload = task['payload']
    read_section = background_read_connection if postgres_store.configured() else read_connection
    if payload['phase'] == 'wake':
        with read_section() as database:
            row = database.execute('SELECT * FROM integrity_recommendation_requests WHERE request_id=? AND issue_id>? ORDER BY issue_id LIMIT 1',
                (payload['request_id'], payload['after_issue_id'])).fetchone()
            record = dict(row) if row else None
        with connection(background=True) as database:
            if not _lock(database, task): return False
            if record is None:
                complete_task_slice_in_transaction(database, task)
                return False
            if _current(database, record) and _source_current(database, record):
                queued = enqueue_task_in_transaction(database, 'integrity_recommendation', record['issue_id'],
                    {**{key: record[key] for key in ('repertoire_id', 'issue_id', 'signature')},
                     'phase': 'rank', 'after_line_id': ''}, priority=85)
                database.execute('UPDATE integrity_recommendation_requests SET task_id=? WHERE issue_id=?', (queued['id'], record['issue_id']))
            advance_task_slice_in_transaction(database, task, next_phase='wake',
                next_payload={**payload, 'after_issue_id': record['issue_id']})
        return True
    with read_section() as database:
        row = database.execute('SELECT * FROM integrity_recommendation_requests WHERE issue_id=?', (payload['issue_id'],)).fetchone()
        record = dict(row) if row else None
        if not record or record['signature'] != payload['signature'] or not _current(database, record):
            record = None
        if record:
            issue = issue_snapshot(database, record['repertoire_id'], record['issue_id'], record['signature'])
            if payload['phase'] == 'route':
                descriptors = sorted(json.loads(issue['sources_json']), key=lambda source:
                    (source['type'] != 'line', source['id'], source.get('move_index', 0)))
                offset = payload['source_offset']
                descriptor = descriptors[offset] if offset < len(descriptors) else None
                source = _source(database, record['repertoire_id'], descriptor) if descriptor else None
                engine = None
            else:
                source = json.loads(record['source_json'])
                line = database.execute('SELECT id,name,start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=? AND id>? ORDER BY id LIMIT 1',
                    (record['repertoire_id'], payload['after_line_id'])).fetchone()
                line = dict(line) if line else None
                engine = database.execute('SELECT state,report_json FROM threat_analysis_requests WHERE id=?', (record['request_id'],)).fetchone()
                engine = dict(engine) if engine else None
    if record is None:
        with connection(background=True) as database:
            if _lock(database, task): complete_task_slice_in_transaction(database, task)
        return False
    if payload['phase'] == 'route':
        prepared = None
        if source and descriptor:
            try:
                board, request = _full_history_request({'start_fen': source['start_fen'],
                    'moves_json': source['moves_json'], 'color': source['trained_color'], 'ply': descriptor['move_index']})
                if ' '.join(board.fen().split()[:4]) != issue['fen_key'] or source['trained_color'] != issue['trained_color']:
                    raise ValueError('Source does not reach this learner decision')
                prepared = (board, request)
            except (ValueError, KeyError, TypeError):
                pass
        with connection(background=True) as database:
            if not _lock(database, task): return False
            if not _current(database, record):
                complete_task_slice_in_transaction(database, task)
                return False
            if prepared is None:
                if descriptor:
                    advance_task_slice_in_transaction(database, task, next_phase='route', next_payload={**payload, 'source_offset': offset + 1})
                    return True
                return _finish(database, task, record, {'state': 'unavailable', 'candidates': [],
                    'reason': 'No legal saved source reaches this position. Edit or remove its invalid source; you can still choose a legal response.'})
            board, request = prepared
            expected = {**source, 'type': descriptor['type'], 'move_index': descriptor['move_index']}
            actual = _source(database, record['repertoire_id'], descriptor)
            if actual != source:
                complete_task_slice_in_transaction(database, task)
                return False
            database.execute('INSERT OR IGNORE INTO threat_analysis_requests(id,request_json,created_at,updated_at) VALUES(?,?,?,?)',
                (request.request_id, json.dumps(asdict(request)), _now(), _now()))
            # This path is entered only after an explicit preparation/retry request.
            database.execute("UPDATE threat_analysis_requests SET state='queued',attempts=0,last_error=NULL WHERE id=? AND state='failed'", (request.request_id,))
            database.execute('UPDATE integrity_recommendation_requests SET request_id=?,source_json=?,updated_at=? WHERE issue_id=?',
                (request.request_id, json.dumps(expected), _now(), record['issue_id']))
            engine = database.execute('SELECT state FROM threat_analysis_requests WHERE id=?', (request.request_id,)).fetchone()
            if engine['state'] == 'complete':
                advance_task_slice_in_transaction(database, task, next_phase='rank', next_payload={**payload, 'phase': 'rank', 'after_line_id': ''})
                return True
            complete_task_slice_in_transaction(database, task)
        return False
    if not engine or engine['state'] != 'complete':
        return False
    board, request = _full_history_request({'start_fen': source['start_fen'], 'moves_json': source['moves_json'],
        'color': source['trained_color'], 'ply': source['move_index']})
    report = report_from_json(json.loads(engine['report_json']))
    validate_analysis_report(request, report)
    examples, _ = _repertoire_positions([line] if line else [], source['trained_color'])
    page = continuation_recommendation(board, request, report, examples, source['trained_color'],
        {'source_type': source['type'], 'source_id': source['id'], 'source_ply': source['move_index']}, rank=False)
    accumulated = _merge(json.loads(record['accumulation_json']) if record['accumulation_json'] else None, page)
    with connection(background=True) as database:
        if not _lock(database, task): return False
        if not _current(database, record) or not _source_current(database, record):
            complete_task_slice_in_transaction(database, task)
            return False
        if line:
            database.execute('UPDATE integrity_recommendation_requests SET accumulation_json=? WHERE issue_id=?',
                (json.dumps(accumulated), record['issue_id']))
            advance_task_slice_in_transaction(database, task, next_phase='rank', next_payload={**payload, 'after_line_id': line['id']})
            return True
        candidates = _rank_candidates(accumulated['candidates'])
        for candidate in candidates: candidate.pop('familiar', None)
        preview = {**accumulated, 'candidates': candidates, 'state': 'ready' if candidates else 'unavailable',
            'suggested_move_uci': candidates[0]['move_uci'] if candidates else None,
            'suggestion_reason': candidates[0]['similarity'] if candidates else None,
            'reason': None if candidates else 'No compatible sound continuation is available. Choose a legal response or retry analysis.',
            'trained_color': source['trained_color'], 'route_start_fen': source['start_fen'],
            'route_uci': list(request.position_prefix_uci)}
        return _finish(database, task, record, preview)
