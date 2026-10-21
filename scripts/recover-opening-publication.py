#!/usr/bin/env python3
"""Validate first-publication recovery or finish one pinned queued rebuild in Study-only."""
from pathlib import Path
import argparse
import json
import sys
import signal
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'database'))
from app.services.opening_graph_recovery import preflight, recover, capture_evidence, target_id, validate_staged_graph, scope_token, verify_preservation
from app.services.opening_graph_recovery import QUEUED_REBUILD_MODE, preflight_queued_rebuild, finish_queued_rebuild
from app.services.opening_graph_recovery import assert_selected_execution_not_started, _selected_scope_key


def prepare_execution_baseline(output, prepared):
    """Capture after quiescence, then retain the same baseline across partial resumes."""
    path = output / 'execution-before.json'
    scope = _selected_scope_key(prepared)
    if not path.exists():
        assert_selected_execution_not_started(prepared)
        before = capture_evidence(prepared['repertoire_id'], prepared['day'], selected_rebuild=True)
        assert_selected_execution_not_started(prepared)
        before['maintenance_scope_key'] = scope
        with path.open('x') as destination:
            destination.write(json.dumps(before, indent=2) + '\n')
        return before
    before = json.loads(path.read_text())
    if before.get('maintenance_scope_key') != scope:
        raise ValueError('Execution baseline belongs to a different pinned maintenance plan')
    current = capture_evidence(prepared['repertoire_id'], prepared['day'], selected_rebuild=True)
    preservation = verify_preservation(before, current)
    if not preservation['passed']:
        raise ValueError('Protected evidence changed before resume; no new task claims permitted')
    return before


def selected_rebuild_main(args):
    if args.source_revision is None or args.prefix_revision is None or args.day is None:
        raise ValueError('queued-rebuild requires explicit --source-revision, --prefix-revision and --day')
    if not 0 < args.max_seconds <= 1200 or not 0 < args.max_slices <= 20000:
        raise ValueError('Use at most 1200 seconds and 20000 slices; reserve verification/restart time')
    started = time.monotonic()
    output = Path(args.output)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.stat().st_mode & 0o077:
        raise ValueError('Maintenance output directory must be private (mode 0700)')
    plan_path = output / 'preflight.json'

    def save_plan(prepared):
        temporary = output / 'preflight.tmp'
        temporary.write_text(json.dumps(prepared, indent=2) + '\n')
        temporary.replace(plan_path)

    if args.resume:
        prepared = json.loads(plan_path.read_text())
        json.loads((output / 'before.json').read_text())
        actual = (prepared.get('mode'), prepared['target'], prepared['task_id'], prepared['repertoire_id'],
                  prepared['generation'], prepared['scope_token']['scope_source_revision'],
                  prepared['scope_token']['canonical_prefix_revision'], prepared.get('day'))
        expected = (args.mode, args.target, args.task_id, args.repertoire_id, args.generation,
                    args.source_revision, args.prefix_revision, args.day)
        if actual != expected:
            raise ValueError('Saved maintenance scope differs from command arguments')
    else:
        if any(output.iterdir()):
            raise ValueError('Use a fresh private output directory or --resume; preserve prior evidence')
        prepared = preflight_queued_rebuild(args.task_id, args.repertoire_id, args.generation, args.target,
                                           source_revision=args.source_revision, prefix_revision=args.prefix_revision, day=args.day)
        save_plan(prepared)
        before = capture_evidence(args.repertoire_id, args.day, selected_rebuild=True)
        (output / 'before.json').write_text(json.dumps(before, indent=2) + '\n')
    # Resume preflight checks the saved day/source/target without taking a task.
    from tempo_legacy_database.adapters import postgres_store
    from app.services.opening_graph_recovery import validate_selected_scope, background_lease
    with background_lease(), postgres_store.connection(read_only=True, authoritative=True, background=True) as database:
        validate_selected_scope(database, prepared, lock=False)
    if not args.apply:
        print(json.dumps({'state': 'validated', 'mode': args.mode, 'tasks': prepared['tasks'], 'day': args.day,
                          'queue_refresh': 'shared ordinary refresh; may change other unissued queue entries'}))
        return
    # The app may legitimately have served study between dry-run and downtime.
    # Preserve before.json as preflight evidence; compare all writes against a
    # fresh quiesced baseline, never one recreated after a claim/partial write.
    baseline = prepare_execution_baseline(output, prepared)
    remaining = args.max_seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise RuntimeError('Preflight consumed the maintenance budget; no tasks claimed')
    attempt = output / ('attempt-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    attempt.mkdir(mode=0o700)
    stopped = False

    def request_stop(_signal, _frame):
        nonlocal stopped
        stopped = True

    previous = {number: signal.signal(number, request_stop) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        with (attempt / 'leases.jsonl').open('x') as events:
            def progress(value):
                events.write(json.dumps(value) + '\n')
                events.flush()
                print(json.dumps(value), flush=True)
            result = finish_queued_rebuild(prepared, progress=progress, save_plan=save_plan,
                                           max_slices=args.max_slices, max_seconds=remaining, stop_requested=lambda: stopped)
        (attempt / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    except BaseException as error:
        (attempt / 'error.json').write_text(json.dumps({'type': type(error).__name__, 'message': str(error)}, indent=2) + '\n')
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        after = capture_evidence(args.repertoire_id, args.day, selected_rebuild=True)
        (attempt / 'after.json').write_text(json.dumps(after, indent=2) + '\n')
        preservation = verify_preservation(baseline, after)
        (attempt / 'preservation.json').write_text(json.dumps(preservation, indent=2) + '\n')
    if not preservation['passed']:
        raise RuntimeError('Maintenance preservation differs; stop and inspect private evidence')
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--repertoire-id', required=True)
    parser.add_argument('--generation', required=True, type=int)
    parser.add_argument('--target', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Resume this output directory’s saved, previously validated recovery')
    parser.add_argument('--mode', choices=('failed-first-publication', QUEUED_REBUILD_MODE), default='failed-first-publication')
    parser.add_argument('--source-revision', type=int)
    parser.add_argument('--prefix-revision', type=int)
    parser.add_argument('--day', help='Pinned America/New_York study day; queued-rebuild rejects midnight rollover')
    parser.add_argument('--max-seconds', type=float, default=1200)
    parser.add_argument('--max-slices', type=int, default=20000)
    args = parser.parse_args()
    if args.mode == QUEUED_REBUILD_MODE:
        return selected_rebuild_main(args)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    day = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    if args.resume:
        prepared = json.loads((output / 'preflight.json').read_text())
        if (prepared['target'], prepared['task_id'], prepared['repertoire_id'], prepared['generation']) != (args.target, args.task_id, args.repertoire_id, args.generation):
            raise ValueError('Saved recovery scope differs from command arguments')
        if target_id() != args.target or scope_token(args.repertoire_id) != prepared['scope_token']:
            raise ValueError('Database, source, study mode, or Maia activation changed')
        if validate_staged_graph(args.repertoire_id, args.generation) != prepared['validation']:
            raise ValueError('Saved recovery no longer matches staged graph and current cards')
    else:
        if (output / 'before.json').exists():
            raise ValueError('Use a fresh output directory or --resume; never overwrite recovery evidence')
        prepared = preflight(args.task_id, args.repertoire_id, args.generation, args.target)
        (output / 'before.json').write_text(json.dumps(capture_evidence(args.repertoire_id, day), indent=2) + '\n')
        (output / 'preflight.json').write_text(json.dumps(prepared, indent=2) + '\n')
    if not args.apply:
        print(json.dumps({'state': 'validated', **prepared['validation']}))
        return
    try:
        result = recover(prepared, day=day,
                         progress=lambda value: print(json.dumps(value), flush=True))
    finally:
        (output / 'after.json').write_text(json.dumps(capture_evidence(args.repertoire_id, day), indent=2) + '\n')
    preservation = verify_preservation(json.loads((output / 'before.json').read_text()), json.loads((output / 'after.json').read_text()))
    (output / 'preservation.json').write_text(json.dumps(preservation, indent=2) + '\n')
    if not preservation['passed']:
        raise RuntimeError('Recovery finished with preservation differences; inspect the private evidence')
    (output / 'recovery.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
