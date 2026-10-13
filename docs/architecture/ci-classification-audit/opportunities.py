#!/usr/bin/env python3
"""Measured opportunity envelopes; certification and future queue time are not inferred."""
import argparse
import gzip
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from audit import interval_union, timestamp, seconds, write_json, csv_rows


def dependencies(job_name, names):
    if job_name == 'selection':
        return []
    if job_name in ('plan','fast'):
        return ['selection'] if 'selection' in names else []
    if job_name == 'browser / reuse':
        return ['plan']
    if job_name.startswith('browser / shard-'):
        return ['browser / reuse'] if 'browser / reuse' in names else ['plan']
    if job_name == 'browser / verify':
        return (['browser / reuse'] if 'browser / reuse' in names else ['plan']) + sorted(name for name in names if name.startswith('browser / shard-'))
    if job_name in {f'{layer} / verify' for layer in ('frontend','backend','build','postgres','lifecycle','visual','quarantine')}:
        return ['plan']
    if job_name in ('quality','development'):
        return ['plan'] + sorted(name for name in names if name.endswith(' / verify'))
    if job_name == 'merge-gate':
        return [name for name in ('selection','fast','quality') if name in names]
    if job_name == 'pages-build':
        return ['quality','plan']
    if job_name == 'deploy':
        return ['pages-build']
    raise ValueError(f'Unknown workflow node {job_name}')


def replay_elapsed(attempt, jobs, replacements):
    """Retain observed scheduling slack; replace only specified job duration envelopes."""
    active = {job['name']: job for job in jobs if job['runner_seconds'] and job['started_at'] and job['completed_at']}
    if len(active) != len([job for job in jobs if job['runner_seconds'] and job['started_at'] and job['completed_at']]):
        return None
    try:
        for name in active:
            dependencies(name,active)
    except ValueError:
        return None
    gate = 'merge-gate' if 'merge-gate' in active else 'quality' if 'quality' in active else None
    if not gate or not attempt['last_job_completed_at']:
        return None
    origin = timestamp(attempt['started_at'])
    memo = {}
    def end(name):
        if name not in active:
            return origin
        if name in memo:
            return memo[name]
        job = active[name]
        parents = dependencies(name, active)
        actual_parent_end = max([timestamp(active[parent]['completed_at']) for parent in parents if parent in active] + [origin])
        slack = max(0, timestamp(job['started_at']) - actual_parent_end)
        recomputed_parent_end = max([end(parent) for parent in parents] + [origin])
        duration = replacements.get(job['id'], job['runner_seconds'])
        memo[name] = recomputed_parent_end + slack + duration
        return memo[name]
    try:
        return end(gate) - origin
    except ValueError:
        return None


def calculate(output):
    cutoff=json.loads((output/'manifest.json').read_text())['cutoff']
    def capped_seconds(start,end):
        if not start:
            return None
        return max(0,min(timestamp(end) if end else timestamp(cutoff),timestamp(cutoff))-min(timestamp(start),timestamp(cutoff)))
    attempts = json.loads((output / 'attempts.json').read_text())
    jobs = json.loads((output / 'jobs.json').read_text())
    reports = json.loads((output / 'reports.json').read_text())
    failures = json.loads((output / 'failures.json').read_text())
    job_lookup = {job['id']: job for job in jobs}
    by_attempt = defaultdict(list)
    for job in jobs:
        by_attempt[(job['run_id'],job['attempt'])].append(job)
    phase_rows=[]
    for attempt in attempts:
        own_jobs=by_attempt[(attempt['run_id'],attempt['attempt'])]
        assigned={job['name']:job for job in own_jobs if job['runner_seconds'] and job['started_at']}
        for job in assigned.values():
            execution=[step for step in job['steps'] if step['name'] in ('Run isolated verification layer','Run isolated browser shard','Run selected head qualification') and step['conclusion']!='skipped']
            parents=[assigned[parent] for parent in dependencies(job['name'],assigned) if parent in assigned and assigned[parent]['completed_at']]
            ready=max([timestamp(parent['completed_at']) for parent in parents]+[timestamp(attempt['started_at'])])
            phase_rows.append({'pr':attempt['pr'],'primary':attempt['primary'],'run_id':job['run_id'],'attempt':job['attempt'],'job_id':job['id'],'job':job['name'],
                'dependency_ready_to_start_seconds':max(0,timestamp(job['started_at'])-ready),
                'pre_execution_seconds':capped_seconds(job['started_at'],execution[0]['started_at']) if len(execution)==1 else None,
                'execution_step_seconds':capped_seconds(execution[0]['started_at'],execution[0]['completed_at']) if len(execution)==1 else None,
                'post_execution_seconds':capped_seconds(execution[0]['completed_at'],job['completed_at']) if len(execution)==1 else None,
                'runner_seconds':job['runner_seconds'],'execution_step_count':len(execution),
                'cutoff_censored':not job['completed_at'] or job['completed_at']>cutoff,
                'note':'dependency slack includes scheduling/orchestration; setup/teardown are job envelopes, not nested scenario measurements'})
    write_json(output/'job-phases.json',phase_rows)
    csv_rows(output/'job-phases.csv',phase_rows)
    failed_run_rows = []
    for attempt in attempts:
        if not attempt['primary'] or attempt['conclusion'] != 'failure':
            continue
        matched = [failure for failure in failures if (failure['run_id'],failure['attempt']) == (attempt['run_id'],attempt['attempt'])]
        execution_failures = [failure for failure in matched if failure['name'] != 'browser / verify' and any(step in ('Run isolated verification layer','Run isolated browser shard','Run selected head qualification') for step in failure['failed_steps'])]
        logs = '\n'.join(gzip.decompress((output / 'logs' / f'{failure["id"]}.txt.gz').read_bytes()).decode(errors='replace') for failure in matched)
        category = ('test_or_layer_execution_failure' if execution_failures else 'planning_failure' if any(failure['name'] == 'plan' for failure in matched)
                    else 'draft_or_readiness_rejection' if 'Development evidence or a draft PR' in logs or 'Draft or changed PR readiness' in logs
                    else 'stale_or_closed_candidate_rejection' if any(text in logs for text in ('PR identity','PR head','base changed','closed')) else 'other_aggregation_rejection')
        failed_run_rows.append({'pr':attempt['pr'],'run_id':attempt['run_id'],'attempt':attempt['attempt'],'category':category,'runner_minutes':attempt['runner_seconds']/60})
    write_json(output / 'failure-categories.json', failed_run_rows)
    csv_rows(output / 'failure-categories.csv', failed_run_rows)
    reuse_overhead = {layer: statistics.median(report['runner_seconds'] for report in reports if report['execution_kind'] == 'reused' and report['layer'] == layer and report['runner_seconds'] is not None)
                      for layer in sorted({report['layer'] for report in reports if report['execution_kind'] == 'reused'})}
    rows = []
    for report in reports:
        if not report['primary'] or not report['repeated_execution_after_matching_pass'] or report['shard']:
            continue
        own_jobs = by_attempt[(report['run_id'],report['attempt'])]
        omitted = [job_lookup[report['job_id']]] if report['job_id'] else []
        if report['layer'] == 'browser':
            omitted += [job for job in own_jobs if job['name'].startswith('browser / shard-') and job['runner_seconds']]
        gross = sum(job['runner_seconds'] or 0 for job in omitted)
        overhead = reuse_overhead.get(report['layer'])
        rows.append({'category':'exact_candidate_reuse_screen','pr':report['pr'],'run_id':report['run_id'],'attempt':report['attempt'],
                     'layer':report['layer'],'source_run':report['earlier_matching_pass_run'],
                     'job_ids':[job['id'] for job in omitted], 'gross_runner_seconds':gross,
                     'observed_median_reuse_job_seconds':overhead,
                     'conditional_net_runner_seconds':max(0,gross - overhead) if overhead is not None else None,
                     'certified':False,'prerequisite':'original metadata/window/runtime/inventory/command/report and newer-failure verification; no cross-candidate reuse'})
    reuse_path_rows=[]
    for key in sorted({(row['run_id'],row['attempt']) for row in rows}):
        group=[row for row in rows if (row['run_id'],row['attempt'])==key]
        attempt=next(attempt for attempt in attempts if (attempt['run_id'],attempt['attempt'])==key)
        own_jobs=by_attempt[key]
        replacements={}
        for row in group:
            for job_id in row['job_ids']:
                replacements[job_id]=0 if job_lookup[job_id]['name'].startswith('browser / shard-') else row['observed_median_reuse_job_seconds']
        original=replay_elapsed(attempt,own_jobs,{})
        reduced=replay_elapsed(attempt,own_jobs,replacements)
        reuse_path_rows.append({'pr':attempt['pr'],'run_id':key[0],'attempt':key[1],
            'conditional_gate_path_reduction_seconds':max(0,original-reduced) if original is not None and reduced is not None else None,
            'note':'all matching layers replaced together; retains observed scheduling slack; future queue and original eligibility remain unproved'})
    write_json(output/'reuse-path-scenarios.json',reuse_path_rows)
    csv_rows(output/'reuse-path-scenarios.csv',reuse_path_rows)
    narrowing = {
        178: {'backend','frontend'},
        180: {'postgres','backend'},
        206: {'backend','frontend'},
        207: {'frontend','postgres','browser'},
        211: {'browser'},
    }
    for attempt in attempts:
        number = attempt['pr']
        if number not in narrowing or attempt['tier'] not in ('FULL','PRE_FAST_LANE_COMPLETE'):
            continue
        own_jobs = by_attempt[(attempt['run_id'],attempt['attempt'])]
        removed = [job for job in own_jobs if job['runner_seconds'] and ' / ' in job['name'] and job['name'].split(' / ')[0] not in narrowing[number]]
        # Include browser reuse/shards in the same layer envelope; retain all affected native/browser work.
        if not removed:
            continue
        original = replay_elapsed(attempt, own_jobs, {})
        reduced = replay_elapsed(attempt, own_jobs, {job['id']:0 for job in removed})
        rows.append({'category':'classification_and_ownership_envelope','pr':number,'run_id':attempt['run_id'],'attempt':attempt['attempt'],
            'job_ids':[job['id'] for job in removed], 'gross_runner_seconds':sum(job['runner_seconds'] for job in removed),
            'retained_layers':sorted(narrowing[number]), 'gate_path_upper_reduction_seconds': max(0,original-reduced) if original is not None and reduced is not None else None,
            'certified':False,'prerequisite':'#202 reverse-dependency certification; #239/#201/#205 fixture ownership; retained full native affected tests; new selection verification overhead not measured'})
    boundary_attempt = next(attempt for attempt in attempts if attempt['run_id'] == 38387017335)
    boundary_jobs = by_attempt[(boundary_attempt['run_id'],boundary_attempt['attempt'])]
    controls = [attempt for attempt in attempts if attempt['pr']==233 and attempt['tier']=='FAST' and attempt['conclusion']=='success']
    rows.append({'category':'separate_documentation_pr','pr':229,'run_id':boundary_attempt['run_id'],'attempt':boundary_attempt['attempt'],
                 'job_ids':[job['id'] for job in boundary_jobs if job['runner_seconds']],
                 'gross_runner_seconds':boundary_attempt['runner_seconds'],
                 'observed_control_runner_seconds':[attempt['runner_seconds'] for attempt in controls],
                 'conditional_net_runner_seconds_range':[boundary_attempt['runner_seconds']-max(attempt['runner_seconds'] for attempt in controls),boundary_attempt['runner_seconds']-min(attempt['runner_seconds'] for attempt in controls)],
                 'conditional_elapsed_seconds_range':[boundary_attempt['elapsed_seconds']-max(attempt['elapsed_seconds'] for attempt in controls),boundary_attempt['elapsed_seconds']-min(attempt['elapsed_seconds'] for attempt in controls)],
                 'certified':False,'prerequisite':'keep product head unchanged; documentation does not affect a test consumer; controls have different scheduling conditions'})
    totals = {}
    for category in sorted({row['category'] for row in rows}):
        group = [row for row in rows if row['category']==category]
        unique = {job_id for row in group for job_id in row['job_ids']}
        totals[category] = {'rows':len(group),'unique_job_count':len(unique), 'gross_runner_minutes':sum(job_lookup[job_id]['runner_seconds'] or 0 for job_id in unique)/60,
            'conditional_net_runner_minutes':sum(row.get('conditional_net_runner_seconds') or 0 for row in group)/60 if category=='exact_candidate_reuse_screen' else None}
    all_ids = {job_id for row in rows for job_id in row['job_ids']}
    full = [attempt for attempt in attempts if attempt['primary'] and attempt['tier'] in ('FULL','PRE_FAST_LANE_COMPLETE')]
    unchanged_shards = [report for report in reports if report['primary'] and report['repeated_execution_after_matching_pass'] and report['shard']]
    result = {'categories':totals,'combined_deduplicated_gross_runner_minutes':sum(job_lookup[job_id]['runner_seconds'] or 0 for job_id in all_ids)/60,
        'combined_unique_job_count':len(all_ids),'combined_certified_lower_bound_runner_minutes':0,
        'reuse_overhead_seconds_by_layer':reuse_overhead,
        'conditional_reuse_gate_path_reduction_minutes':sum(row['conditional_gate_path_reduction_seconds'] or 0 for row in reuse_path_rows)/60,
        'reuse_path_scenarios_without_terminal':sum(row['conditional_gate_path_reduction_seconds'] is None for row in reuse_path_rows),
        'per_shard_repetition_not_whole_suite_reuse': {'count':len(unchanged_shards),'runner_minutes':sum(report['runner_seconds'] or 0 for report in unchanged_shards)/60},
        'complete_selection_attempts':len(full),
        'complete_selection_outcomes':dict(Counter(attempt['conclusion'] or attempt['status'] for attempt in full)),
        'failure_categories':dict(Counter(row['category'] for row in failed_run_rows)),
        'failure_category_runner_minutes':{category:sum(row['runner_minutes'] for row in failed_run_rows if row['category']==category) for category in sorted({row['category'] for row in failed_run_rows})},
        'primary_per_pr_qualification_interval_union_minutes':sum(interval_union([(timestamp(attempt['started_at']), timestamp(attempt['last_job_completed_at'])) for attempt in attempts if attempt['pr']==number])/60 for number in {attempt['pr'] for attempt in attempts if attempt['primary']}),
        'primary_repository_interval_union_minutes':interval_union([(timestamp(attempt['started_at']), timestamp(attempt['last_job_completed_at'])) for attempt in attempts if attempt['primary']])/60}
    write_json(output / 'opportunities.json', rows)
    csv_rows(output / 'opportunities.csv', rows)
    write_json(output / 'opportunity-summary.json', result)
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    calculate(parser.parse_args().output)
