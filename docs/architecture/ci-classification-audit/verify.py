#!/usr/bin/env python3
"""Fail closed on duplicate joins, inconsistent totals and corrupted native plans."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from audit import write_json
from opportunities import replay_elapsed


def verify(output):
    attempts=json.loads((output/'attempts.json').read_text())
    jobs=json.loads((output/'jobs.json').read_text())
    revisions=json.loads((output/'revisions.json').read_text())
    summary=json.loads((output/'summary.json').read_text())
    manifest=json.loads((output/'manifest.json').read_text())
    prs=json.loads((output/'prs.json').read_text())
    assert len({(row['run_id'],row['attempt']) for row in attempts})==len(attempts), 'Duplicate attempt identity'
    assert len({row['id'] for row in jobs})==len(jobs), 'Duplicate job identity'
    assert len({row['number'] for row in manifest['cohort']})==25, 'Cohort cardinality'
    assert not ({row['number'] for row in manifest['cohort']} & {row['number'] for row in manifest['controls']}), 'Control leakage'
    assert abs(sum((row['runner_seconds'] or 0)/60 for row in jobs if row['primary'])-summary['runner_minutes'])<1e-8, 'Job total does not reconcile'
    assert abs(sum(row['runner_seconds']/60 for row in attempts if row['primary'])-summary['runner_minutes'])<1e-8, 'Attempt total does not reconcile'
    assert sum(summary['outcomes'].values())==summary['attempts'], 'Outcome denominator'
    assert sum(summary['tiers'].values())==summary['attempts'], 'Tier denominator'
    assert len({(row['pr'],row['head'],row['base'],row['integration']) for row in revisions})==len(revisions), 'Duplicate candidate record'
    for pr in prs:
        assert pr['candidate_count']==sum(revision['pr']==pr['pr'] for revision in revisions), 'Stale PR candidate count'
    for revision in revisions:
        assert revision.get('plan_paths_match_git') is not False, 'Native paths disagree with Git'
    plans=[]
    receipts=[]
    for path in (output/'artifacts').rglob('*.json'):
        record=json.loads(path.read_text())
        content=record['content']
        if isinstance(content,dict) and content.get('hash'):
            plans.append(content)
        if isinstance(content,dict) and content.get('source',{}).get('plan',{}).get('hash'):
            plans.append(content['source']['plan'])
        if isinstance(content,dict) and (content.get('hash') or content.get('layer')):
            assert record['verified_sha256'].startswith('sha256:'), 'Missing verified artifact digest'
            if record['artifact'].get('digest'):
                assert record['verified_sha256']==record['artifact']['digest'], 'Artifact digest mismatch'
            receipts.append({'path':str(path.relative_to(output)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                             'original_zip_digest':record['verified_sha256'],'original_member_sha256':record.get('raw_member_sha256')})
    source="import{createHash}from'node:crypto';let input='';for await(const chunk of process.stdin)input+=chunk;let bad=0;for(const plan of JSON.parse(input)){const{hash,...body}=plan;if(createHash('sha256').update(JSON.stringify(body)).digest('hex')!==hash)bad++;}console.log(JSON.stringify({plans:JSON.parse(input).length,bad}));process.exitCode=bad?1:0;"
    completed=subprocess.run(['node','--input-type=module','-e',source],input=json.dumps(plans),text=True,capture_output=True)
    assert completed.returncode==0, f'Native hash verification failed: {completed.stdout} {completed.stderr}'
    graph_matches=0
    graph_gaps=[]
    for attempt in attempts:
        own=[job for job in jobs if job['run_id']==attempt['run_id'] and job['attempt']==attempt['attempt']]
        replay=replay_elapsed(attempt,own,{})
        actual=attempt['event_to_gate_seconds']
        if replay is not None and actual is not None:
            # Gate time begins at attempt start; event-to-gate begins at event creation.
            from audit import seconds
            expected=actual-seconds(attempt['created_at'],attempt['started_at']) if seconds(attempt['created_at'],attempt['started_at']) is not None else None
            if expected is not None and abs(replay-expected)<1e-6:
                graph_matches+=1
            else:
                graph_gaps.append([attempt['run_id'],attempt['attempt'],replay,expected])
    result={'native_plan_hashes':json.loads(completed.stdout),'unique_attempts':len(attempts),'unique_jobs':len(jobs),
            'known_base_revision_records':sum(row['base'] is not None for row in revisions),
            'unknown_base_revision_records':sum(row['base'] is None for row in revisions),
            'exact_git_path_comparisons':sum(row.get('plan_paths_match_git') is True for row in revisions),
            'checkout_log_recoveries':len(json.loads((output/'checkout-recoveries.json').read_text())),
            'accounting_reconciled':True,'graph_replay_matches':graph_matches,'graph_replay_gaps':graph_gaps}
    write_json(output/'artifact-receipts.json',receipts)
    write_json(output/'verification.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    verify(parser.parse_args().output)
