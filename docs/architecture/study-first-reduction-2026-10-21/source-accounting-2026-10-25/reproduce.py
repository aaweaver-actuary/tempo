"""Reproduce this pinned documentation receipt; no network or application execution.

Run from the repository: python3 <this-file> --output /tmp/tempo-accounting-repro
The existing source_delta.py is invoked unchanged, once. This is not a new census.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

BASE = '7d77ed1b25198dcd320e0265f777cc630f23775e'
HEAD = '782bf5374b87f223a023568305056415d5b63cea'
AUDIT = '2c55dfab8004cba2fe3ab3b0497570f1609aaea9'
RECEIPT_DIRECTORY = Path(__file__).resolve().parent
INDEX = 'docs/architecture/study-first-reduction-2026-10-21/MODULE-INDEX.md'
INDEX_SHA256 = '907df27c2b2067882b2135e79a6854ae3e2fd2c3923de1770819b307cf6f4995'
TOOL = 'tools/component-build/source_delta.py'
TOOL_SHA256 = 'ef2ca42d1636e8d56ce76db99ee015c889a5c7eb088db7dfc8a87d9f1660c2cf'
HELPERS = (
    'packages/board-ui/build.mjs', 'packages/board-ui/consumer/build.mjs',
    'packages/board-ui/qualify.mjs', 'packages/board-ui/vitest.config.ts',
    'workers/maia/benchmark.mjs', 'workers/maia/build.mjs', 'workers/maia/qualify.mjs',
    'workers/stockfish/build.mjs', 'workers/stockfish/qualify.mjs',
)


def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data)


def tree(revision):
    result = {}
    for row in git('ls-tree', '-rz', revision).split(b'\0'):
        if row:
            meta, name = row.split(b'\t', 1)
            mode, kind, oid = meta.split()
            assert kind == b'blob', 'This receipt expects source blobs, not submodules'
            result[name.decode()] = oid.decode()
    return result


def blobs(ids):
    stream = io.BytesIO(git('cat-file', '--batch', data=''.join(x+'\n' for x in sorted(ids)).encode()))
    result = {}
    while row := stream.readline():
        oid, kind, length = row.split()
        assert kind == b'blob'
        result[oid.decode()] = stream.read(int(length))
        assert stream.read(1) == b'\n'
    return result


def lines(raw):
    assert b'\0' not in raw, 'Binary files have bytes, not physical source-line counts'
    return raw.count(b'\n') + int(bool(raw) and not raw.endswith(b'\n'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(git('rev-parse', '--show-toplevel').decode().strip())
    index = git('show', AUDIT+':'+INDEX)
    assert hashlib.sha256(index).hexdigest() == INDEX_SHA256
    assert hashlib.sha256((root/TOOL).read_bytes()).hexdigest() == TOOL_SHA256
    ledger = re.findall(r'^\| \[`([^`]+)`\]\([^\n]*?\) \| (\d+) \|', index.decode(), re.M)
    categories = {}
    for path, count in ledger:
        assert path not in categories
        narrow = (path.startswith('backend/app/') and path.endswith('.py')) or (
            path.startswith('database/') and path.endswith(('.py', '.sql')))
        categories[path] = 'narrow-production' if narrow else 'broad-production'
    assert len(categories) == 881
    with (RECEIPT_DIRECTORY/'supplement.tsv').open() as file:
        supplement = list(csv.DictReader(file, delimiter='\t'))
    for row in supplement:
        assert row['path'] not in categories, 'Supplement must not relabel original membership'
        categories[row['path']] = row['category']
    with (RECEIPT_DIRECTORY/'binary-fixtures.tsv').open() as file:
        binary = list(csv.DictReader(file, delimiter='\t'))
    before, after = tree(BASE), tree(HEAD)
    changed = git('diff', '--name-only', '--no-renames', '-z', BASE, HEAD).decode().strip('\0').split('\0')
    assert set(categories) <= set(before)|set(after)
    assert set(changed)-set(categories) == {row['path'] for row in binary}
    selected = set(categories)|{row['path'] for row in binary}
    objects = blobs({mapping[p] for mapping in (before, after) for p in selected if p in mapping})
    for path, count in ledger:
        assert lines(objects[before[path]]) == int(count), path
    assert sum(int(n) for p, n in ledger) == 97217
    assert sum(1 for p, n in ledger if categories[p] == 'narrow-production') == 597
    assert sum(int(n) for p, n in ledger if categories[p] == 'narrow-production') == 69686
    for row in binary:
        for mapping, key in ((before, 'base_bytes'), (after, 'head_bytes')):
            raw = objects[mapping[row['path']]] if row['path'] in mapping else b''
            assert len(raw) == int(row[key]) and (not raw or b'\0' in raw)
    def total(paths, mapping):
        return sum(lines(objects[mapping[p]]) for p in paths if p in mapping)
    def pair(paths):
        b, h = total(paths, before), total(paths, after)
        return {'before': b, 'after': h, 'net': h-b,
                'before_files': sum(p in before for p in paths),
                'after_files': sum(p in after for p in paths)}
    groups = collections.defaultdict(list)
    for row in supplement:
        groups[row['kind']].append(row['path'])
    groups['historical-helpers'] = list(HELPERS)
    summary = {key: pair(paths) for key, paths in sorted(groups.items())}
    summary['narrow-production'] = pair([p for p, c in categories.items() if c == 'narrow-production'])
    summary['broad-production-inclusive'] = pair([p for p, c in categories.items() if c in ('narrow-production','broad-production')])
    summary['baseline_verification'] = {'rows': len(ledger), 'mismatches': 0, 'index_sha256': INDEX_SHA256}
    summary['binary-test-fixtures'] = {'before_bytes': sum(int(r['base_bytes']) for r in binary),
                                       'after_bytes': sum(int(r['head_bytes']) for r in binary), 'paths': len(binary)}
    rename_rows = {}
    for percent in (50, 20, 10):
        rows = git('diff', '--name-status', '--no-ext-diff', f'--find-renames={percent}%', BASE, HEAD).decode().splitlines()
        rename_rows[str(percent)] = [row for row in rows if row.startswith('R')]
        assert not rename_rows[str(percent)], 'Re-review classifications if moves appear'
    deleted_production = [p for p, c in categories.items() if c.endswith('production') and p in before and p not in after]
    exact_survivors = {p: sorted(q for q, oid in after.items() if oid == before[p])
                       for p in deleted_production if before[p] in set(after.values())}
    assert not exact_survivors, 'Re-review unchanged relocated production blobs'
    summary['moves'] = {'git_rename_rows_by_similarity_percent': rename_rows,
                        'deleted_production_blobs_surviving_anywhere': exact_survivors,
                        'whole_file_move_adjustment_lines': 0}
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'production-changes.tsv').open('w') as file:
        writer = csv.writer(file, delimiter='\t', lineterminator='\n')
        writer.writerow(['path', 'category', 'base_lines', 'head_lines', 'net_lines', 'historical_helper'])
        for path in sorted(set(changed)&{p for p, c in categories.items() if c.endswith('production')}):
            b = total([path], before)
            h = total([path], after)
            writer.writerow([path, categories[path], b, h, h-b, 'yes' if path in HELPERS else 'no'])
    manifest = '{\n  "schema_version":1,\n  "paths":[\n' + ',\n'.join(
        '    '+json.dumps({'path': p, 'category': c}, separators=(',', ':'))
        for p, c in sorted(categories.items())) + '\n  ]\n}\n'
    (args.output/'classification.json').write_text(manifest)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
    with (args.output/'delta.json').open('wb') as file:
        subprocess.run(['python3', str(root/TOOL), '--base', BASE, '--head', HEAD,
                        '--classification', str(args.output/'classification.json')], check=True, stdout=file)
    print(json.dumps({'base': BASE, 'head': HEAD, 'paths': len(categories), 'summary': summary}, sort_keys=True))


if __name__ == '__main__':
    main()
