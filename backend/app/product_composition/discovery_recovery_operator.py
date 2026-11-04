"""Finite local operator for already accepted discovery holds; no HTTP entry."""
from __future__ import annotations

import argparse
import json
import re

_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
_REASON = re.compile(r'^[a-z0-9_]{1,96}$')
_LIMIT = 50
_RETRY_REASONS = frozenset({
    'graph_prerequisite_missing', 'graph_authority_busy_retry_exhausted',
    'current_integrity_missing', 'current_integrity_stale', 'current_integrity_busy_retry_exhausted',
    'current_integrity_pending_retry_exhausted', 'published_card_unavailable',
    'discovery_target_unavailable', 'queue_receipt_capacity_exceeded',
})


def _identity(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError('invalid_identity')
    return value


def list_parked(reader, after_task_id=""):
    from tempo_legacy_database.adapters.commands.activity import parked_discovery_tuples
    with reader() as database:
        rows = parked_discovery_tuples(database, _LIMIT + 1, after_task_id)
    result = []
    invalid = 0
    for row in rows[:_LIMIT]:
        item = dict(row)
        if (not item['identity_valid'] or not _ID.fullmatch(item['task_id'] or '')
                or not _ID.fullmatch(item['intent_id'] or '')
                or type(item['generation']) is not int or item['generation'] < 1
                or not _REASON.fullmatch(item['hold_reason'] or '')):
            invalid += 1
            continue
        result.append({**{key: item[key] for key in ('task_id', 'generation', 'intent_id', 'hold_reason')},
                       'status': 'held' if item['hold_reason'] in _RETRY_REASONS else 'unsupported'})
    cursor = dict(rows[_LIMIT - 1])['task_id'] if len(rows) > _LIMIT else None
    valid_cursor = cursor is None or bool(_ID.fullmatch(cursor))
    return {'status': 'ok' if valid_cursor else 'incomplete', 'parked': result,
            'truncated': len(rows) > _LIMIT, 'next_after': cursor if valid_cursor else None,
            'unavailable_count': invalid,
            'worker': 'disabled', 'note': 'Retry can requeue the original task; recovery execution is unavailable.'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    status = sub.add_parser('status')
    status.add_argument('--after-task-id', default='')
    retry = sub.add_parser('retry')
    for name in ('task-id', 'intent-id', 'hold-reason', 'operation-id'):
        retry.add_argument('--' + name, required=True)
    retry.add_argument('--generation', type=int, required=True)
    args = parser.parse_args(argv)
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.connections import read_connection
    if not postgres_store.configured():
        print(json.dumps({'status': 'unsupported', 'worker': 'disabled'})); return 2
    if args.action == 'status':
        if args.after_task_id: _identity(args.after_task_id)
        print(json.dumps(list_parked(read_connection, args.after_task_id))); return 0
    _identity(args.task_id); _identity(args.intent_id); _identity(args.operation_id)
    if args.generation < 1 or not _REASON.fullmatch(args.hold_reason):
        raise ValueError('invalid_retry_tuple')
    payload = {'task_id': args.task_id, 'generation': args.generation,
               'intent_id': args.intent_id, 'hold_reason': args.hold_reason}
    from app.command_dispatch import dispatch_command
    from fastapi.responses import JSONResponse
    from fastapi import HTTPException
    try:
        result = dispatch_command('activity.task.retry', payload, idempotency_key=args.operation_id)
    except HTTPException as error:
        details = error.detail
        code = details.get('code') if isinstance(details, dict) else None
        status = ('busy' if code == 'discovery_authority_busy' else
                  'pending' if code == 'discovery_authority_pending' else
                  'stale' if code in ('discovery_authority_stale', 'discovery_authority_missing') else
                  'unsupported' if details in ('SQLite discovery authority is unavailable; owner disposition required', 'Discovery hold is not eligible for owner retry') else
                  'held' if error.status_code in (404, 409, 410, 422) else 'pending_or_unavailable')
        print(json.dumps({'status': status, 'http_status': error.status_code,
                          'operation_id': args.operation_id, 'worker': 'disabled'})); return 2
    except Exception:
        print(json.dumps({'status': 'pending_or_unavailable', 'operation_id': args.operation_id,
                          'worker': 'disabled'})); return 2
    if isinstance(result, JSONResponse):
        try:
            state = json.loads(result.body).get('state')
        except (ValueError, AttributeError):
            state = None
        status = 'pending' if state in ('pending', 'queued', 'running', 'retrying') else 'pending_or_unavailable'
        print(json.dumps({'status': status, 'operation_id': args.operation_id,
                          'worker': 'disabled'})); return 0
    if not isinstance(result, dict) or result.get('id') != args.task_id or result.get('generation') != args.generation:
        print(json.dumps({'status': 'unavailable', 'operation_id': args.operation_id, 'worker': 'disabled'})); return 2
    print(json.dumps({'status': 'requeued', 'operation_id': args.operation_id,
                      'task_id': args.task_id, 'generation': args.generation,
                      'worker': 'disabled'})); return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({'status': 'unavailable', 'worker': 'disabled'}))
        raise SystemExit(2)
