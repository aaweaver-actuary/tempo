#!/usr/bin/env python3
"""Recover missing candidate SHAs from checkout logs, without inventing CI tiers."""
import argparse
import gzip
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from audit import Evidence, PREFIX, write_json, csv_rows


def checkout_sha(text):
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if 'git log -1 --format=%H' in line:
            match = re.search(r'\b([0-9a-f]{40})\s*$', lines[index + 1])
            if match:
                return match.group(1)
    return None


def revision_records(attempts, revisions):
    # Rebuild from individual attempts. A recovered base cannot be attributed to
    # other planless runs that happened to share the same head.
    historical = {(row['pr'],row['head'],row['base'],row['integration']):row for row in revisions}
    rebuilt = {}
    for attempt in attempts:
        key=(attempt['pr'],attempt['head'],attempt['base'],attempt['integration'])
        template=historical.get(key,{})
        revision=rebuilt.setdefault(key,{'pr':attempt['pr'],'primary':attempt['primary'],'head':attempt['head'],
            'base':attempt['base'],'integration':attempt['integration'],'draft':attempt['draft'],
            'paths':template.get('paths'),'runs':[],'tiers':[],'first_event':attempt['created_at'],'gaps':[], 'draft_states':[]})
        revision['runs'].append([attempt['run_id'],attempt['attempt']])
        revision['tiers']=sorted(set(revision['tiers']+[attempt['tier']]))
        revision['first_event']=min(revision['first_event'],attempt['created_at'])
        revision['gaps']=sorted(set(revision['gaps']+attempt['gaps']))
        if attempt['draft'] not in revision['draft_states']:
            revision['draft_states'].append(attempt['draft'])
        if 'candidate_source' in attempt:
            revision['candidate_source']=attempt['candidate_source']
    return list(rebuilt.values())


def recover(evidence, output):
    attempts = json.loads((output / 'attempts.json').read_text())
    revisions = json.loads((output / 'revisions.json').read_text())
    jobs = json.loads((output / 'jobs.json').read_text())
    unresolved = {(row['run_id'], row['attempt']): row for row in attempts if row['base'] is None}
    selected = [job for job in jobs if (job['run_id'], job['attempt']) in unresolved and job['name'] == 'plan' and job['runner_seconds']]
    def inspect(job):
        try:
            destination = output / 'logs' / f'{job["id"]}.txt.gz'
            log = gzip.decompress(destination.read_bytes()) if destination.exists() else evidence.get(f'{PREFIX}/actions/jobs/{job["id"]}/logs', binary=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(gzip.compress(log, mtime=0))
            return job, checkout_sha(log.decode(errors='replace'))
        except RuntimeError:
            return job, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        checked = list(pool.map(inspect, selected))
    recoveries = [{'pr':attempt['pr'],'head':attempt['head'],'base':attempt['base'],'integration':attempt['integration'],
                   'run_id':attempt['run_id'],'attempt':attempt['attempt'],
                   'job_id':int(re.search(r'job (\d+)',attempt['candidate_source']).group(1))}
                  for attempt in attempts if attempt.get('candidate_source')]
    for job, sha in checked:
        if not sha:
            continue
        result = subprocess.run(['git','cat-file','-e',sha],capture_output=True)
        if result.returncode:
            result = subprocess.run(['git','fetch','--no-tags','origin',sha],capture_output=True)
            if result.returncode:
                continue
        parents = subprocess.check_output(['git','show','-s','--format=%P',sha],text=True).strip().split()
        attempt = unresolved[(job['run_id'],job['attempt'])]
        if len(parents) != 2 or parents[1] != attempt['head']:
            continue
        attempt['base'], attempt['integration'] = parents[0], sha
        attempt['candidate_source'] = f'checkout log job {job["id"]}; second parent verified against run head'
        recovery = {'pr': attempt['pr'],'head': attempt['head'],'base': parents[0],'integration':sha,'run_id':job['run_id'],'attempt':job['attempt'],'job_id':job['id']}
        recoveries.append(recovery)
    revisions=revision_records(attempts,revisions)
    write_json(output / 'attempts.json', attempts)
    csv_rows(output / 'attempts.csv', attempts)
    write_json(output / 'revisions.json', revisions)
    csv_rows(output / 'revisions.csv', revisions)
    write_json(output / 'checkout-recoveries.json', recoveries)
    summary=json.loads((output/'summary.json').read_text())
    summary['revision_count']=len(revisions)
    write_json(output/'summary.json',summary)
    prs=json.loads((output/'prs.json').read_text())
    for pr in prs:
        pr['candidate_count']=sum(revision['pr']==pr['pr'] for revision in revisions)
    write_json(output/'prs.json',prs)
    csv_rows(output/'prs.csv',prs)
    print(f'Recovered {len(recoveries)} attempt candidates from {len(selected)} checkout logs', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    recover(Evidence(options.evidence), options.output)
