#!/usr/bin/env python3
"""Recover historical Git paths and add attempt-bound report observations."""
import argparse
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from audit import Evidence, PREFIX, csv_rows, write_json


def git(*arguments, binary=False):
    result = subprocess.run(['git', *arguments], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout if binary else result.stdout.decode().strip()


def changed_files(start, head, whole_pr=False):
    comparison = f'{start}...{head}' if whole_pr else f'{start}..{head}'
    tokens = git('diff', '--name-status', '-z', '--find-renames', comparison, binary=True).decode().split('\0')
    paths = []
    offset = 0
    while offset < len(tokens) and tokens[offset]:
        status = tokens[offset]
        offset += 1
        first = tokens[offset]
        offset += 1
        if status[0] in 'RC':
            second = tokens[offset]
            offset += 1
            paths.append({'status': status, 'filename': second, 'previous_filename': first})
        else:
            paths.append({'status': status, 'filename': first})
    return paths


def enrich(evidence, output, fetch=False):
    attempts = json.loads((output / 'attempts.json').read_text())
    revisions = json.loads((output / 'revisions.json').read_text())
    jobs = json.loads((output / 'jobs.json').read_text())
    manifest = json.loads((output / 'manifest.json').read_text())
    shas = {value for row in revisions for value in (row['head'], row['base'], row['integration']) if value}
    missing = []
    for sha in sorted(shas):
        try:
            git('cat-file', '-e', f'{sha}^{{commit}}')
        except RuntimeError:
            missing.append(sha)
    if fetch:
        for offset in range(0, len(missing), 12):
            # One unavailable SHA must not discard the rest of a batch.
            try:
                git('fetch', '--no-tags', 'origin', *missing[offset:offset + 12])
            except RuntimeError:
                for sha in missing[offset:offset + 12]:
                    try:
                        git('fetch', '--no-tags', 'origin', sha)
                    except RuntimeError as error:
                        write_json(output / 'gaps' / f'git-{sha}.json', {'sha': sha, 'error': str(error)})
            print(f'Fetched historical objects {offset + 12}/{len(missing)}', flush=True)
    file_rows, policy_rows = [], {}
    previous = {}
    for revision in sorted(revisions, key=lambda row: (row['pr'], row['first_event'])):
        head, base = revision['head'], revision['base']
        try:
            if base:
                files = changed_files(base, head, whole_pr=True)
                revision['whole_pr_files'] = files
                revision['merge_base'] = git('merge-base', base, head)
                for item in files:
                    file_rows.append({'pr': revision['pr'], 'head': head, 'base': base, 'kind': 'whole_pr', **item})
                reconstructed = {item['filename'] for item in files} | {item['previous_filename'] for item in files if 'previous_filename' in item}
                revision['plan_paths_match_git'] = reconstructed == set(revision['paths']) if revision['paths'] is not None else None
            else:
                revision['whole_pr_files'] = None
            preceding = previous.get(revision['pr'])
            if preceding and preceding != head:
                changes = changed_files(preceding, head)
                revision['previous_observed_head'] = preceding
                revision['incremental_files'] = changes
                revision['previous_is_ancestor'] = subprocess.run(['git','merge-base','--is-ancestor',preceding,head],capture_output=True).returncode == 0
                for item in changes:
                    file_rows.append({'pr': revision['pr'], 'head': head, 'base': preceding, 'kind': 'incremental_tree_delta', **item})
            previous[revision['pr']] = head
            policy_sha = revision['integration'] or head
            if policy_sha not in policy_rows:
                blobs = {}
                for path in ('.github/workflows/pages.yml', 'scripts/ci-fast-lane.mjs', 'scripts/ci-fast-lane-inventory.json', 'scripts/ci-evidence.mjs', 'scripts/ci-verification-plan.mjs'):
                    try:
                        content = git('show', f'{policy_sha}:{path}')
                        blob = git('rev-parse', f'{policy_sha}:{path}')
                        blobs[path] = blob
                        destination = output / 'policy' / blob
                        if not destination.exists():
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_text(content + '\n')
                    except RuntimeError:
                        blobs[path] = None
                policy_rows[policy_sha] = {'revision': policy_sha, 'blobs': blobs}
            revision['policy_blobs'] = policy_rows[policy_sha]['blobs']
        except RuntimeError as error:
            revision['git_gap'] = str(error)
    write_json(output / 'revisions.json', revisions)
    csv_rows(output / 'revisions.csv', revisions)
    write_json(output / 'changed-files.json', file_rows)
    csv_rows(output / 'changed-files.csv', file_rows)
    write_json(output / 'policy.json', list(policy_rows.values()))
    reports, seen = [], set()
    for path in (output / 'artifacts').rglob('*.json'):
        record = json.loads(path.read_text())
        report = record['content']
        if not isinstance(report, dict) or not report.get('layer') or not report.get('execution'):
            continue
        execution = report['execution']
        run_id = record['artifact']['workflow_run']['id']
        matching = next((row for row in attempts if row['run_id'] == run_id and row['attempt'] == execution.get('attempt')), None)
        # Reused receipts have current run/attempt at top level in some versions.
        if not matching:
            matching = next((row for row in attempts if row['run_id'] == run_id and row['attempt'] == max(r['attempt'] for r in attempts if r['run_id'] == run_id)), None)
        if not matching:
            continue
        identity = (run_id, matching['attempt'], report['layer'], report.get('shardId'))
        if identity in seen:
            continue
        seen.add(identity)
        layer = report['layer']
        job_name = f'{layer} / verify' if not report.get('shardId') else f'browser / shard-{report["shardId"]}'
        candidate_jobs = [job for job in jobs if job['run_id'] == run_id and job['attempt'] == matching['attempt'] and job['name'] == job_name]
        job = candidate_jobs[0] if len(candidate_jobs) == 1 else None
        reports.append({'pr': matching['pr'], 'primary': matching['primary'], 'run_id': run_id, 'attempt': matching['attempt'],
            'layer': layer, 'shard': report.get('shardId'), 'execution_kind': execution.get('kind'), 'execution_key': report.get('executionKey'),
            'report_status': report.get('status'), 'report_completed': report.get('completed'),
            'head': matching['head'], 'base': matching['base'], 'integration': matching['integration'],
            'workflow_conclusion': matching['conclusion'], 'workflow_terminal_at': matching['last_job_completed_at'],
            'plan_hash': report.get('planHash'), 'report_environment': report.get('environment'),
            'commands': report.get('commands'), 'test_count': len(report.get('tests', [])),
            'job_id': job['id'] if job else None, 'job_conclusion': job['conclusion'] if job else None,
            'runner_seconds': job['runner_seconds'] if job else None,
            'started_at': job['started_at'] if job else None,
            'completed_at': job['completed_at'] if job else None,
            'artifact_id': record['artifact']['id'], 'verified_zip_digest': record['verified_sha256'], 'member': record['member']})
    reports.sort(key=lambda row: row['started_at'] or '')
    previous_pass = {}
    for report in reports:
        identity = (report['pr'], report['layer'], report['shard'], report['execution_key'])
        previous_report = previous_pass.get(identity) if report['execution_key'] else None
        report['earlier_matching_pass_run'] = previous_report['run_id'] if previous_report and previous_report['workflow_terminal_at'] and report['started_at'] and previous_report['workflow_terminal_at'] <= report['started_at'] else None
        report['repeated_execution_after_matching_pass'] = bool(report['earlier_matching_pass_run'] and report['execution_kind'] == 'executed')
        if (report['report_status'] == 'success' and report['job_conclusion'] == 'success' and report['report_completed']
                and report['execution_kind'] == 'executed' and report['workflow_conclusion'] in ('success', 'failure')):
            previous_pass[identity] = report
        elif report['execution_kind'] == 'executed':
            previous_pass.pop(identity, None)
    write_json(output / 'reports.json', reports)
    csv_rows(output / 'reports.csv', reports)
    print('Git path mismatches:', sum(row.get('plan_paths_match_git') is False for row in revisions))
    print('Git gaps:', sum('git_gap' in row for row in revisions))
    print('Report kinds:', Counter(row['execution_kind'] for row in reports))
    repeated = [row for row in reports if row['primary'] and row['repeated_execution_after_matching_pass']]
    print('Repeated same-key executed jobs after passing original:', len(repeated), 'gross runner minutes', sum(row['runner_seconds'] or 0 for row in repeated) / 60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true')
    options = parser.parse_args()
    enrich(Evidence(options.evidence, offline=True), options.output, options.fetch)
