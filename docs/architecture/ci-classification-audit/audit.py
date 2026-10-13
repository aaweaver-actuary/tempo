#!/usr/bin/env python3
"""Issue 238: read-only GitHub collection and offline CI accounting (stdlib only)."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import subprocess
import threading
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = 'aaweaver-actuary/tempo'
PREFIX = f'repos/{REPOSITORY}'
LOCK = threading.Lock()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else None


def seconds(start, end):
    if not start or not end:
        return None
    duration = timestamp(end) - timestamp(start)
    return duration if duration >= 0 else None


def interval_union(intervals):
    intervals = sorted((start, end) for start, end in intervals if start is not None and end is not None and end >= start)
    if not intervals:
        return 0
    total = 0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start > end:
            total += end - start
            start, end = next_start, next_end
        else:
            end = max(end, next_end)
    return total + end - start


def job_duration(job, cutoff=None):
    # Skipped and unassigned jobs can have synthetic timestamps; these are not runner use.
    if job.get('conclusion') == 'skipped' or not job.get('runner_id'):
        return 0
    if cutoff and job.get('started_at'):
        if timestamp(job['started_at']) >= timestamp(cutoff):
            return 0
        end = min(timestamp(job['completed_at']), timestamp(cutoff)) if job.get('completed_at') else timestamp(cutoff)
        return max(0, end - timestamp(job['started_at']))
    return seconds(job.get('started_at'), job.get('completed_at'))


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Native plan hashes use JavaScript insertion order; never sort nested source objects.
    path.write_text(json.dumps(value, indent=2) + '\n')


class Evidence:
    def __init__(self, directory, offline=False):
        self.directory = Path(directory)
        self.offline = offline
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, route, binary=False):
        key = hashlib.sha256(route.encode()).hexdigest()
        cache = self.directory / f'{key}.json.gz'
        if cache.exists():
            record = json.loads(gzip.decompress(cache.read_bytes()))
            recover_escape_refusal = binary and not self.offline and 'terminal escape sequences' in record.get('error', '')
            if binary and 'error' not in record:
                return (self.directory / f'{key}.bin').read_bytes()
            if 'error' in record and not recover_escape_refusal:
                raise RuntimeError(record['error'])
            if not recover_escape_refusal:
                return record['response']
        if self.offline:
            raise RuntimeError(f'Uncached source: {route}')
        completed = subprocess.run(['gh', 'api', *(['--allow-escape-sequences'] if binary else []), route], capture_output=True)
        record = {'route': route, 'url': f'https://api.github.com/{route}',
                  'collected_at': datetime.now(timezone.utc).isoformat()}
        if completed.returncode:
            record['error'] = completed.stderr.decode(errors='replace')
        elif binary:
            record['sha256'] = hashlib.sha256(completed.stdout).hexdigest()
            (self.directory / f'{key}.bin').write_bytes(completed.stdout)
        else:
            record['response'] = json.loads(completed.stdout)
        cache.write_bytes(gzip.compress(json.dumps(record).encode(), mtime=0))
        if 'error' in record:
            raise RuntimeError(record['error'])
        return completed.stdout if binary else record['response']

    def pages(self, route, field=None):
        rows = []
        for page in range(1, 1000):
            response = self.get(f'{route}{"&" if "?" in route else "?"}per_page=100&page={page}')
            batch = response[field] if field else response
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise RuntimeError('Pagination exceeded safety bound')


def prose_files(files):
    return bool(files) and all(item['filename'].endswith('.md') for item in files)


def collect(evidence, output, cutoff):
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        recent = evidence.pages(f'{PREFIX}/pulls?state=all&sort=created&direction=desc')
        cohort, controls = [], []
        for pr in recent:
            if pr['created_at'] > cutoff:
                continue
            files = evidence.pages(f'{PREFIX}/pulls/{pr["number"]}/files')
            temporary = pr['number'] in (215, 216)
            pr['audit_files'] = files
            (controls if temporary or prose_files(files) else cohort).append(pr)
            if len(cohort) == 25:
                break
        if len(cohort) != 25:
            raise RuntimeError('Insufficient substantive PRs')
        manifest = {'repository': REPOSITORY, 'cutoff': cutoff, 'selection': 'newest 25 substantive by creation; markdown-only and temporary validation controls',
                    'cohort': cohort, 'controls': controls,
                    'base_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
        write_json(manifest_path, manifest)
    prs = manifest['cohort'] + manifest['controls']
    def collect_pr(pr):
        number = pr['number']
        for suffix in ('commits',):
            evidence.pages(f'{PREFIX}/pulls/{number}/{suffix}')
        for suffix in ('timeline', 'comments'):
            evidence.pages(f'{PREFIX}/issues/{number}/{suffix}')
        evidence.get(f'{PREFIX}/pulls/{number}')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(collect_pr, prs))
    oldest = min(pr['created_at'] for pr in manifest['cohort'])
    runs = evidence.pages(f'{PREFIX}/actions/runs?event=pull_request&created={oldest}..{manifest["cutoff"]}', 'workflow_runs')
    branches = {pr['head']['ref']: pr['number'] for pr in prs}
    selected = []
    for run in runs:
        candidates = set(pr['number'] for pr in run.get('pull_requests', []) if pr['number'] in {p['number'] for p in prs})
        if run['head_branch'] in branches:
            candidates.add(branches[run['head_branch']])
        if len(candidates) == 1:
            run['audit_pr'] = candidates.pop()
            selected.append(run)
    write_json(output / 'runs.json', selected)
    print(f'Frozen {len(manifest["cohort"])} substantive + {len(manifest["controls"])} controls; {len(selected)} associated runs', flush=True)
    def collect_run(run):
        run_id = run['id']
        for attempt in range(1, run['run_attempt'] + 1):
            route = f'{PREFIX}/actions/runs/{run_id}/attempts/{attempt}'
            evidence.get(route)
            evidence.pages(f'{route}/jobs', 'jobs')
        artifacts = evidence.pages(f'{PREFIX}/actions/runs/{run_id}/artifacts', 'artifacts')
        for artifact in artifacts:
            if artifact['expired']:
                continue
            if artifact['name'] in ('fast-lane-plan', 'ci-plan', 'merge-gate-verdict', 'fast-lane-results') or artifact['name'].startswith('ci-result-'):
                try:
                    archive = evidence.get(f'{PREFIX}/actions/artifacts/{artifact["id"]}/zip', binary=True)
                    digest = 'sha256:' + hashlib.sha256(archive).hexdigest()
                    if artifact.get('digest') and artifact['digest'] != digest:
                        raise ValueError('Artifact digest mismatch')
                    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                        for member in zipped.namelist():
                            if member.endswith('.json'):
                                try:
                                    value = json.loads(zipped.read(member))
                                except (ValueError, UnicodeDecodeError):
                                    continue
                                path = output / 'artifacts' / str(run_id) / str(artifact['id']) / member
                                # Archive members are data, never filesystem paths outside output.
                                if '..' in Path(member).parts or member.startswith('/'):
                                    raise ValueError('Unsafe archive member')
                                write_json(path, {'artifact': artifact, 'member': member, 'verified_sha256': digest, 'content': value})
                except (RuntimeError, ValueError, zipfile.BadZipFile) as error:
                    write_json(output / 'gaps' / f'artifact-{artifact["id"]}.json', {'run': run_id, 'error': str(error)})
        with LOCK:
            print(f'Collected run {run_id}, PR {run["audit_pr"]}, attempts {run["run_attempt"]}', flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(collect_run, selected))
    for number in (238, 202, 203, 192, 199, 201, 205, 239):
        evidence.get(f'{PREFIX}/issues/{number}')
        evidence.pages(f'{PREFIX}/issues/{number}/comments')


def csv_rows(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='') as destination:
        writer = csv.DictWriter(destination, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def analyze(evidence, output):
    manifest = json.loads((output / 'manifest.json').read_text())
    runs = json.loads((output / 'runs.json').read_text())
    run_rows, jobs, revisions, pr_rows = [], {}, {}, []
    cohort = {pr['number'] for pr in manifest['cohort']}
    cutoff = manifest['cutoff']
    for run in runs:
        run_id, number = run['id'], run['audit_pr']
        artifact_records = [json.loads(path.read_text()) for path in (output / 'artifacts' / str(run_id)).rglob('*.json')]
        candidates = [record for record in artifact_records if record['artifact']['name'] in ('fast-lane-plan', 'ci-plan') and isinstance(record['content'], dict) and record['content'].get('hash')]
        fast_plan = next((r['content'] for r in candidates if r['artifact']['name'] == 'fast-lane-plan'), None)
        full_plan = next((r['content'] for r in candidates if r['artifact']['name'] == 'ci-plan'), None)
        plan = fast_plan or full_plan
        if plan and plan.get('pullRequest') != number:
            raise ValueError(f'PR association mismatch for run {run_id}')
        if plan and plan.get('head') != run['head_sha']:
            raise ValueError(f'Head association mismatch for run {run_id}')
        if fast_plan:
            tier = fast_plan['tier']
        elif full_plan:
            tier = 'PRE_FAST_LANE_COMPLETE' if full_plan.get('scope') == 'complete' else 'PRE_FAST_LANE_DEVELOPMENT'
        else:
            tier = 'UNKNOWN'
        for attempt in range(1, run['run_attempt'] + 1):
            route = f'{PREFIX}/actions/runs/{run_id}/attempts/{attempt}'
            detail = evidence.get(route)
            attempt_jobs = evidence.pages(f'{route}/jobs', 'jobs')
            if detail.get('run_started_at') and detail['run_started_at'] > cutoff:
                continue
            # Artifact names can be replaced on rerun. Do not attach a later artifact to an earlier attempt.
            current_tier = tier if attempt == run['run_attempt'] else 'UNKNOWN_EARLIER_ATTEMPT'
            own_jobs = [job for job in attempt_jobs if job.get('run_attempt', attempt) == attempt]
            measured = [job for job in own_jobs if job_duration(job, cutoff) is not None]
            ends = [job['completed_at'] for job in attempt_jobs if job.get('completed_at') and job.get('runner_id')]
            end = max(ends) if ends and detail['status'] == 'completed' else None
            timestamp_observed_at_cutoff = bool(end and end <= cutoff)
            completed_at_cutoff = detail['status'] == 'completed' and (timestamp_observed_at_cutoff or detail.get('updated_at', '9999') <= cutoff)
            if not timestamp_observed_at_cutoff:
                end = None
            started = detail.get('run_started_at')
            row = {'pr': number, 'primary': number in cohort, 'run_id': run_id, 'attempt': attempt,
                   'workflow': run['path'], 'head': run['head_sha'], 'base': plan.get('base') if plan else None,
                   'integration': full_plan.get('commit') if full_plan else None,
                   'tier': current_tier, 'draft': plan.get('draft') if plan else None,
                   'scope': full_plan.get('scope') if full_plan else None,
                   'classification_reasons': fast_plan.get('reasons') if fast_plan else None,
                   'plan_hash': plan.get('hash') if plan else None,
                   'inventory_revision': full_plan.get('inventoryRevision') if full_plan else None,
                   'status': detail['status'] if completed_at_cutoff else 'incomplete_at_cutoff', 'conclusion': detail['conclusion'] if completed_at_cutoff else None, 'created_at': detail['created_at'],
                   'started_at': started, 'last_job_completed_at': end,
                   'elapsed_seconds': seconds(started, end), 'event_to_terminal_seconds': seconds(detail['created_at'], end),
                   'runner_seconds': sum(job_duration(job, cutoff) for job in measured),
                   'missing_job_timing': sum(job_duration(job, cutoff) is None for job in own_jobs),
                   'url': f'https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}',
                   'artifact_ids': sorted({r['artifact']['id'] for r in artifact_records}),
                   'gaps': [] if plan else ['immutable_plan_unavailable']}
            if current_tier == 'UNKNOWN_EARLIER_ATTEMPT':
                for field in ('base', 'integration', 'draft', 'scope', 'classification_reasons', 'plan_hash', 'inventory_revision'):
                    row[field] = None
                row['gaps'].append('earlier_attempt_plan_replaced_or_unbound')
            if not completed_at_cutoff:
                row['gaps'].append('incomplete_or_unobservable_terminal_at_cutoff')
            elif end is None:
                row['gaps'].append('terminal_job_timestamp_unavailable')
            gates = [job for job in attempt_jobs if job['name'] in ('merge-gate', 'quality') and job.get('runner_id')]
            gate = next((job for job in gates if job['name'] == 'merge-gate'), gates[0] if gates else None)
            row['gate_conclusion'] = gate.get('conclusion') if gate else None
            row['event_to_gate_seconds'] = seconds(detail['created_at'], gate.get('completed_at')) if gate and gate['status'] == 'completed' and gate.get('completed_at') and gate['completed_at'] <= cutoff else None
            row['selected_jobs'] = {job['name']: job['conclusion'] for job in attempt_jobs}
            run_rows.append(row)
            for job in attempt_jobs:
                if job['id'] in jobs:
                    continue
                job_row = {'pr': number, 'primary': number in cohort, 'run_id': run_id, 'attempt': job.get('run_attempt', attempt),
                           'id': job['id'], 'name': job['name'], 'status': job['status'], 'conclusion': job['conclusion'],
                           'started_at': job['started_at'], 'completed_at': job['completed_at'],
                           'runner_seconds': job_duration(job, cutoff), 'runner_labels': job.get('labels', []),
                           'steps': job['steps'], 'url': job['html_url']}
                jobs[job['id']] = job_row
            revision_key = (number, run['head_sha'], row['base'], row['integration'])
            revision = revisions.setdefault(revision_key, {'pr': number, 'primary': number in cohort, 'head': run['head_sha'],
                    'base': row['base'], 'integration': row['integration'], 'draft': row['draft'], 'paths': plan.get('paths') if plan and current_tier != 'UNKNOWN_EARLIER_ATTEMPT' else None,
                    'runs': [], 'tiers': [], 'first_event': detail['created_at'], 'gaps': row['gaps']})
            revision['runs'].append([run_id, attempt])
            revision['tiers'] = sorted(set(revision['tiers'] + [current_tier]))
            revision['first_event'] = min(revision['first_event'], detail['created_at'])
    for pr in manifest['cohort'] + manifest['controls']:
        number = pr['number']
        relevant = [row for row in run_rows if row['pr'] == number]
        relevant_jobs = [row for row in jobs.values() if row['pr'] == number]
        commits = evidence.pages(f'{PREFIX}/pulls/{number}/commits')
        timeline = evidence.pages(f'{PREFIX}/issues/{number}/timeline')
        pr_rows.append({'pr': number, 'primary': number in cohort, 'title': pr['title'], 'state': pr['state'], 'merged_at': pr['merged_at'],
            'created_at': pr['created_at'], 'snapshot_head': pr['head']['sha'], 'branch': pr['head']['ref'],
            'changed_files': [{key: item.get(key) for key in ('filename','status','previous_filename','sha','additions','deletions')} for item in pr['audit_files']],
            'current_commit_count': len(commits), 'current_commits': [commit['sha'] for commit in commits],
            'observed_heads': sorted(set(row['head'] for row in relevant)),
            'candidate_count': sum(row['pr'] == number for row in revisions.values()),
            'readiness_events': [{key: event.get(key) for key in ('event','created_at','commit_id')} for event in timeline if event['event'] in ('ready_for_review','convert_to_draft','head_ref_force_pushed') and event.get('created_at', '9999') <= cutoff],
            'attempt_count': len(relevant), 'tiers': dict(Counter(row['tier'] for row in relevant)),
            'outcomes': dict(Counter(row['conclusion'] or row['status'] for row in relevant)),
            'runner_minutes': sum(row['runner_seconds'] or 0 for row in relevant_jobs) / 60,
            'qualification_interval_union_minutes': interval_union([(timestamp(row['started_at']), timestamp(row['last_job_completed_at'])) for row in relevant]) / 60,
            'url': pr['html_url']})
    qualification = [row for row in run_rows if row['primary'] and row['workflow'] == '.github/workflows/pages.yml']
    summary = {'cutoff': manifest['cutoff'], 'primary_pr_count': len(cohort), 'control_pr_count': len(manifest['controls']),
        'attempts': len(qualification), 'tiers': dict(Counter(row['tier'] for row in qualification)),
        'outcomes': dict(Counter(row['conclusion'] or row['status'] for row in qualification)),
        'runner_minutes': sum(row['runner_seconds'] or 0 for row in jobs.values() if row['primary'] and any(run['id'] == row['run_id'] and run['path'] == '.github/workflows/pages.yml' for run in runs)) / 60,
        'by_tier': {tier: {'count': len(group), 'runner_minutes': sum(row['runner_seconds'] for row in group) / 60,
            'elapsed_median_minutes': percentile([row['elapsed_seconds'] / 60 for row in group if row['elapsed_seconds'] is not None], .5),
            'elapsed_p90_minutes': percentile([row['elapsed_seconds'] / 60 for row in group if row['elapsed_seconds'] is not None], .9)}
            for tier in sorted(set(row['tier'] for row in qualification)) for group in [[row for row in qualification if row['tier'] == tier]]},
        'missing_plan_attempts': sum('immutable_plan_unavailable' in row['gaps'] for row in qualification),
        'earlier_attempt_plan_gaps': sum(row['tier'] == 'UNKNOWN_EARLIER_ATTEMPT' for row in qualification),
        'outcome_timing': {outcome: {'count': len(group), 'runner_minutes': sum(row['runner_seconds'] for row in group) / 60,
            'elapsed_median_minutes': percentile([row['elapsed_seconds'] / 60 for row in group if row['elapsed_seconds'] is not None], .5),
            'elapsed_p90_minutes': percentile([row['elapsed_seconds'] / 60 for row in group if row['elapsed_seconds'] is not None], .9)}
            for outcome in sorted(set(row['conclusion'] or row['status'] for row in qualification)) for group in [[row for row in qualification if (row['conclusion'] or row['status']) == outcome]]},
        'runner_classes': dict(Counter({','.join(row['runner_labels']): sum((job['runner_seconds'] or 0) / 60 for job in jobs.values() if job['primary'] and job['runner_labels'] == row['runner_labels']) for row in jobs.values() if row['primary']})),
        'distinct_job_count': len(jobs), 'revision_count': len(revisions)}
    for name, rows in [('prs', pr_rows), ('attempts', run_rows), ('jobs', list(jobs.values())), ('revisions', list(revisions.values()))]:
        write_json(output / f'{name}.json', rows)
        csv_rows(output / f'{name}.csv', rows)
    write_json(output / 'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['collect', 'analyze'])
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cutoff', default='2026-10-13T12:18:16Z')
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    source = Evidence(arguments.evidence, offline=arguments.mode == 'analyze')
    if arguments.mode == 'collect':
        collect(source, arguments.output, arguments.cutoff)
    else:
        analyze(source, arguments.output)
