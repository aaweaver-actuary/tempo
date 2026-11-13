#!/usr/bin/env python3
"""Read selected original task cards without loading the whole packet into context."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('task_ids', nargs='+')
parser.add_argument('--prompt', action='store_true', help='include each original full prompt')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
data = json.loads((root / 'task-reconciliation.json').read_text())
by_id = {row['task_id']: row for row in data['tasks']}
missing = [key for key in args.task_ids if key not in by_id]
if missing:
    parser.error('unknown task IDs: ' + ', '.join(missing))
fields = ('task_id', 'title', 'status', 'observed', 'remaining', 'original_acceptance',
          'original_start_requires', 'original_release_events', 'original_owner_conditions',
          'draft_prs', 'declared_write_paths')
for key in args.task_ids:
    row = by_id[key]
    result = {field: row[field] for field in fields}
    if args.prompt:
        result['original_prompt_path'] = row['original_prompt']
        result['original_prompt'] = (root / row['original_prompt']).read_text()
        result['supporting_material'] = 'Extract tempo-completion-20261113.zip into a new directory for this original prompt’s supporting reference paths; read the checkpoint overlay first.'
    print(json.dumps(result, ensure_ascii=False, indent=2))
