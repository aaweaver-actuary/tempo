"""Summarize retained audit rows without importing product code."""
import collections,json,math,pathlib,re,hashlib,datetime
ROOT=pathlib.Path(__file__).resolve().parents[3]
EVIDENCE=pathlib.Path('/Users/andy/tempo/test-results/audit-218-2026-10-12')
if not EVIDENCE.exists():
    EVIDENCE=pathlib.Path(__file__).resolve().parent/'evidence'

def distribution(values):
    values=sorted(values)
    if not values:return {'n':0}
    result={'n':len(values),'p50':values[math.ceil(len(values)*.5)-1],'p95':values[math.ceil(len(values)*.95)-1],'max':values[-1]}
    if len(values)>=1000:result['p99']=values[math.ceil(len(values)*.99)-1]
    return result

def format_values(values):
    return '/'.join(f"{values[key]:.2f}" for key in ['p50','p95','max'])

http=[json.loads(line) for line in (EVIDENCE/'raw-http.jsonl').read_text().splitlines()]
summary={'http':{},'browser':{},'diagnostics':{},'resource_samples':{},'methodology_limitations':[
    'Synthetic learned tactic fixture; real HTTP/Celery/PostgreSQL, not rendering latency.',
    'Serial stateful reviews reduce the remaining queue population; crossover blocks reduce order bias but are not identical restored fixtures.',
    'Do not interpret lower loaded medians as a speedup. Backend pairs match block/iteration, not identical database snapshots.',
    'CPU/SQL faults occupy an audit-only background task; no production handler is asserted to ignore admission.',
    'Shared host desktop activity remains; host/process samples are retained. No other disposable stack was active.',
    'Clock epoch correlation is advisory; latency uses monotonic clocks and identity hashes. Compare interruption monotonic time with wall timestamps.',
    'No p99 from 200-observation cohorts; do not pool distinct conditions to manufacture p99.',
]}
lines=['# Runtime measurement results','# Cohorts and methodology','',
       'Baseline product source: `02b65f229aaccc91ae7c03fd11efc8f3aedd2883`. Main measurement tooling: `af71f0da`; native follow-up tooling: `e5e88cbd`. Production files are identical to the baseline. Ten HTTP warmups are excluded; each successful condition has 200 samples in four 50-review blocks with alternating baseline/load order. Browser cohorts have ten excluded paired warmups each, then 200 paired trials, grouped into four consecutive blocks. Instrumented cohorts are separate.','',
       'Quantiles are nearest rank. All milliseconds are caller-observed monotonic durations. Paired differences match condition/block/iteration (backend) or iteration (browser). They describe this workstation and these fixtures. Stateful queue size and cache changes mean backend pairs are not identical database restores; no observed lower median is claimed as a speedup.','',
       '## Native server latency','',
       '| Condition | n load/control | Review p50/p95/max ms | Control review p50/p95/max ms | Next-head p50/p95/max ms | Control next-head p50/p95/max ms | Paired added next-head p95 ms |',
       '| --- | --- | --- | --- | --- | --- | --- |']
for condition in ['cpu','database','optional_stopped','study_only','redis_delayed','cancelled_cpu']:
    loaded=[row for row in http if row.get('condition')==condition and row.get('ok')]
    controls=[row for row in http if row.get('condition')=='baseline_'+condition and row.get('ok')]
    assert len(loaded)==len(controls)==200
    by_identity={(row['block'],row['iteration']):row for row in controls}
    entry={'load':{},'control':{},'paired':{},'block_p95':{}}
    for metric in ['review_ms','next_available_ms','total_ms']:
        entry['load'][metric]=distribution([row[metric] for row in loaded])
        entry['control'][metric]=distribution([row[metric] for row in controls])
        entry['paired'][metric]=distribution([row[metric]-by_identity[row['block'],row['iteration']][metric] for row in loaded])
        entry['block_p95'][metric]=[distribution([row[metric] for row in loaded if row['block']==block])['p95'] for block in range(4)]
    summary['http'][condition]=entry
    lines.append(f"| {condition} | 200/200 | {format_values(entry['load']['review_ms'])} | {format_values(entry['control']['review_ms'])} | {format_values(entry['load']['next_available_ms'])} | {format_values(entry['control']['next_available_ms'])} | {entry['paired']['next_available_ms']['p95']:.2f} |")
lines+=['','`review_ms` spans POST through durable confirmation; `next_available_ms` spans confirmation through native successor GET/head validation. `total_ms` also includes initial queue retrieval. `redis_delayed` pauses Redis writes for 100 ms before each cycle, so its delay can occur in initial foreground admission rather than the POST itself. The full JSON retains total latency and per-block variability. No failed review occurred in the 2,400 main observations (1,200 load, 1,200 controls). All received prepared continuations and validated the expected successor head.','',
        'The native verifier counted **2,411 reviews = 2,411 distinct reviewed cards = 2,411 logical receipts** after main cohorts, ten warmups and Redis recovery. Twenty idle and twenty CPU instrumentation reviews were run afterward and are excluded from the primary latency distributions. The three-review native browser fixture is separate.','',
        '## Browser singleton-worker capacity','',
        '| Scenario | Paired observations | Control foreground p50/p95/max ms | Loaded foreground p50/p95/max ms | Added foreground p50/p95/max ms | Abort acknowledgment p50/max ms |',
        '| --- | --- | --- | --- | --- | --- |']
browser_path=EVIDENCE/'browser-worker/raw-untraced-audit-high-distinctness-10000.jsonl'
browser=[json.loads(line) for line in browser_path.read_text().splitlines()]
groups=collections.defaultdict(list)
for row in browser:
    if row.get('type')=='sample':groups[(row['scenario'],row['iteration'])].append(row)
excluded=[]
for (scenario,iteration),rows in groups.items():
    if len(rows)!=2:continue
    start=datetime.datetime.fromisoformat(rows[0]['hostActivity']['at'].replace('Z','+00:00'))
    end=datetime.datetime.fromisoformat(rows[0]['hostActivityAtEnd']['at'].replace('Z','+00:00'))
    unexplained=(end-start).total_seconds()*1000-sum(row['durationMs'] for row in rows)
    if unexplained>5000:
        excluded.append({'scenario':scenario,'iteration':iteration,'unaccounted_wall_ms':unexplained,'reason':'Wall/monotonic discontinuity exceeds 5 s, inconsistent with active execution; retained, not pooled'})
        for row in rows:row['audit_excluded_clock_discontinuity']=True
replacement_path=EVIDENCE/'browser-replacement/worker/raw-untraced-foreground-high-distinctness-10000.jsonl'
replacements=[json.loads(line) for line in replacement_path.read_text().splitlines()]
for row in replacements:
    if row.get('type')=='sample' and not row.get('warmup'):
        row['iteration']+=200
        row['replacement_cohort']=True
        browser.append(row)
assert len(excluded)==3 and all(row['scenario']=='foreground' for row in excluded)
summary['excluded_browser_pairs']=excluded
(EVIDENCE/'excluded-browser-pairs.json').write_text(json.dumps(excluded,indent=2))
table_headers=lines[-2:]
del lines[-2:]
lines+=['Three original foreground pairs (65, 78, 83) had wall/monotonic discontinuities of approximately 131 s, 993 s and 11 s beyond measured execution. They are retained and excluded under a fixed >5 s discrepancy rule; three independent fresh-browser paired trials replace them. The cancellation cohort needed no replacement. This also explains why wall-clock study interruption exceeds monotonic runner time; neither quantity is silently relabeled.','',*table_headers]
for scenario in ['foreground','cancel']:
    samples=[row for row in browser if row.get('type')=='sample' and row.get('scenario')==scenario and not row.get('warmup') and not row.get('contaminated') and not row.get('audit_excluded_clock_discontinuity')]
    loaded=[row for row in samples if row['contended']];controls=[row for row in samples if not row['contended']]
    assert len(loaded)==len(controls)==200
    def foreground_duration(row):
        requests=[request for request in row['requests'] if request['kind']!='repertoireAlternatives']
        return max(request['settledAt'] for request in requests)-min(request['sentAt'] for request in requests)
    baseline={row['iteration']:foreground_duration(row) for row in controls}
    values=[foreground_duration(row) for row in loaded]
    added=[foreground_duration(row)-baseline[row['iteration']] for row in loaded]
    entry={'control_foreground':distribution(list(baseline.values())),'load_foreground':distribution(values),'paired_added':distribution(added),'abort_ack':distribution([row['cancelledAt']-row['cancelAt'] for row in loaded if row.get('cancelAt') is not None]),
           'background_after_abort':distribution([next(request for request in row['requests'] if request['kind']=='repertoireAlternatives')['replyAt']-row['cancelAt'] for row in loaded if row.get('cancelAt') is not None]),
           'queued_foreground_after_background_count':sum(min(request['runningAt'] for request in row['requests'] if request['kind']!='repertoireAlternatives')>=next(request for request in row['requests'] if request['kind']=='repertoireAlternatives')['replyAt'] for row in loaded),
           'block_added_p95':[distribution([foreground_duration(row)-baseline[row['iteration']] for row in loaded if row['iteration']//50==block])['p95'] for block in range(4)]}
    summary['browser'][scenario]=entry
    abort=f"{entry['abort_ack']['p50']:.2f}/{entry['abort_ack']['max']:.2f}" if entry['abort_ack']['n'] else 'n/a'
    lines.append(f"| {scenario} | 200 | {format_values(entry['control_foreground'])} | {format_values(entry['load_foreground'])} | {format_values(entry['paired_added'])} | {abort} |")
lines+=['','The public seeded workload contains **10,000 distinct synthetic prepared positions**. This is a native browser worker using the unchanged product dispatcher/computation and a fixture harness; it is not a current 250,503-position real application navigation measurement. Foreground capacity duration covers queue/workspace/route/deck dispatch through settlement. Cancellation occurs only after the real worker `running` event. Quick caller abort and continued background computation are recorded independently.','',
        'No p99 is reported for these 200-observation cohorts. Raw environment fingerprints, request ordering, compute/queue times, external process activity and output digest are preserved.','',
        '## Diagnostic spans and resource occupancy','']
logs=(EVIDENCE/'http-runtime.log').read_text()
spans=[]
for line in logs.splitlines():
    if 'training_timing {' in line:
        try:spans.append(json.loads(line.split('training_timing ',1)[1]))
        except json.JSONDecodeError:pass
review_spans=[span for span in spans if span.get('label')=='cards.review']
summary['diagnostics']['review_span_count']=len(review_spans)
for key,values in {
    'pool_acquisition_ms':[connection['acquisition_ms'] for span in review_spans for connection in span['connections']],
    'transaction_execution_ms':[connection['execution_ms'] for span in review_spans for connection in span['connections']],
    'foreground_admission_ms':[span['stages_ms'].get('foreground_admission',0) for span in review_spans],
    'broker_wait_ms':[span['broker_wait_ms'] for span in review_spans if 'broker_wait_ms' in span],
}.items():
    summary['diagnostics'][key]={'n':len(values),'minimum':min(values) if values else None,'maximum':max(values) if values else None}
    if values:lines.append(f"- Separate instrumented {key}: n={len(values)}, range {min(values):.3f}–{max(values):.3f} ms. These are diagnostic ranges, not uninstrumented percentile claims.")
resources=[json.loads(line) for line in (EVIDENCE/'resource-samples.jsonl').read_text().splitlines()]
for service in ['background-worker','postgres','foreground-worker','api']:
    values=[float(sample['CPUPerc'].rstrip('%')) for row in resources if '/load/' in row['label'] and row['label'].startswith('cpu/') for sample in row['stats'] if sample['Name'].endswith('-'+service+'-1')]
    summary['resource_samples'][service]={'cpu_load_samples':len(values),'cpu_percent_min':min(values),'cpu_percent_max':max(values)}
    lines.append(f"- CPU-cohort {service}: {len(values)} boundary snapshots, {min(values):.2f}–{max(values):.2f}% Docker CPU. CPU percentages are per-core usage, not a whole-host percentage or continuous peak measurement.")
lines+=['','PostgreSQL snapshot: `max_connections=100`, six application sessions (two reader, four writer) plus the snapshot query. No production pool-exhaustion mechanism was reproduced. A one-slot synthetic database task is sustained SQL pressure, not an eight-connection pool-exhaustion proof.','',
        '## Native qualification and failure handling','',
        '- Existing PostgreSQL Study-only proof: eight named PASS lines, including migration replay, saved-source verification, queue eligibility, exactly-once receipts and preserved/resumed optional work.',
        '- Existing Chromium `study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption`: one case passed, 1.1 minutes reported; three actual UI reviews, reload, worker restart, duplicate replay and resumed maintenance.',
        '- Redis stop/recovery: the main driver observed a non-JSON error in 74.63 ms; reissuing the same intent after recovery persisted once. The direct native follow-up records the actual HTTP status/body and exception trace separately.',
        '- First driver attempt failed before the API request after a pause; retained separately under `failed-driver-01`. No product defect is attributed to that transport failure. Fresh HTTP connections completed all main cohorts.',
        '- No full release gate was run; no product repair or release qualification is claimed.','',
        '## Limitations and remaining proof','',*['- '+item for item in summary['methodology_limitations']]]
fault=json.loads((EVIDENCE/'native-fault-probe/findings.json').read_text())
admission=json.loads((EVIDENCE/'native-fault-probe/raw-refused-admission.json').read_text())
summary['native_fault_probe']=fault
summary['redis_unavailable_admission']={'samples':distribution([row['milliseconds'] for row in admission]),'error_classes':dict(collections.Counter(row['error_class'] for row in admission)), 'fault':'Stopped Redis container removes Docker DNS name; unavailable/DNS fault, not silent nonreply or TCP-refusal proof'}
lines+=['','## Direct native fault follow-up','',
        f"- Essential `daily_queue` publication remained `refreshing/pending`, with the durable task `queued` and zero attempts, for **{fault['cold_publication']['blocked_seconds']:.2f} s** behind confirmed synthetic CPU occupancy. A prepared foreground review saved in **{fault['cold_publication']['foreground_review']['milliseconds']:.2f} ms** during that interval. After native child termination, the production publication slice completed; command-start-to-ready wall time was **{fault['cold_publication']['revoke_to_ready_wall_ms']:.2f} ms**, including temporary producer startup. This proves the shared capacity boundary under injection, not a specific unbounded production handler.",
        f"- Redis unavailability returned **HTTP {fault['redis_http_outage']['status']}**, plain body **`{fault['redis_http_outage']['body']}`**, in **{fault['redis_http_outage']['milliseconds']:.2f} ms**. The retained server trace reaches `activity_gate.foreground` → `redis_admission_gate.foreground_lease` → unhandled Redis `ConnectionError` before review dispatch. No false successful save was returned. Recovery and logical replay produced one review for that attempt; the fixture verified two distinct reviews/two receipts across the cold and outage cases.",
        f"- Separate native unavailable-admission cohort: 200/200 `ConnectionError`; direct lease-call p50/p95/max **{format_values(summary['redis_unavailable_admission']['samples'])} ms**. Redis was stopped and its Docker DNS name became unavailable; this is not a silent socket-nonreply proof.",'',
        '## Study interruption and restoration','',
        '| Window | Wall-clock elapsed seconds | Runner monotonic seconds | Exact image/container restoration |',
        '| --- | --- | --- | --- |']
interruptions=[]
for name,folder in [('driver probe',EVIDENCE/'failed-driver-01'),('main matrix',EVIDENCE),('native fault follow-up',EVIDENCE/'native-fault-probe'),('three browser replacements',EVIDENCE/'browser-replacement')]:
    row=json.loads((folder/'interruption.json').read_text());start=datetime.datetime.fromisoformat(row['started_utc']);end=datetime.datetime.fromisoformat(row['restored_utc']);row.update(window=name,wall_elapsed_seconds=(end-start).total_seconds());interruptions.append(row)
    assert json.loads((folder/'live-restored.json').read_text())['identical_image_ids']
    lines.append(f"| {name} | {row['wall_elapsed_seconds']:.2f} | {row['seconds']:.2f} | verified |")
summary['interruptions']=interruptions
lines+=['','The main window contains the documented wall/monotonic discontinuity. Actual recorded wall interruption is reported alongside active monotonic time. Live settings, volumes and release images were preserved; restoration checked original image/container IDs, running services, health and reader-only queue SQL. No further shutdown is required.']
(EVIDENCE/'summary.json').write_text(json.dumps(summary,indent=2))
(ROOT/'docs/architecture/audit-218/RESULTS.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'http_conditions':len(summary['http']),'browser_scenarios':len(summary['browser']),'diagnostic_review_spans':len(review_spans)}))
