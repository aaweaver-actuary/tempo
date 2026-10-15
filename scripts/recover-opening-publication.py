#!/usr/bin/env python3
"""Validate or explicitly recover one failed first opening-graph publication."""
from pathlib import Path
import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.opening_graph_recovery import preflight, recover, capture_evidence, target_id, validate_staged_graph, scope_token, verify_preservation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--repertoire-id', required=True)
    parser.add_argument('--generation', required=True, type=int)
    parser.add_argument('--target', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Resume this output directory’s saved, previously validated recovery')
    args = parser.parse_args()
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
