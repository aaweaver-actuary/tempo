import { readFileSync, readdirSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';

const directory=resolve(process.argv[2] ?? '../../test-results/study-worker-contention-2026-10-11');
const summarize=values=>{
  const ordered=values.toSorted((left,right)=>left-right);
  return ordered.length ? {n:ordered.length,p50:ordered[Math.ceil(ordered.length*.5)-1],p95:ordered[Math.ceil(ordered.length*.95)-1],maximum:ordered.at(-1),minimum:ordered[0]}:null;
};
const rows=readdirSync(directory).filter(name=>name.startsWith('raw-')&&name.endsWith('.jsonl')&&!name.endsWith('-probe.jsonl')).flatMap(name=>readFileSync(join(directory,name),'utf8').trim().split('\n').filter(Boolean).map(line=>({...JSON.parse(line),artifact:name})));
const groups=new Map();
for(const row of rows) {
  if(row.type!=='sample'||row.warmup)continue;
  const key=`${row.artifact}:${row.workload}:${row.scenario}`;
  const group=groups.get(key)??[];group.push(row);groups.set(key,group);
}
const results=[];
for(const [key,samples]of groups) {
  const [first]=samples;
  if(first.scenario==='pipeline') {
    results.push({key,workload:first.workload,scenario:first.scenario,total:summarize(samples.map(row=>row.durationMs)),longTaskMaximum:summarize(samples.map(row=>Math.max(0,...row.longTasks.map(task=>task.duration)))),phases:Object.fromEntries(['workspace','transportLines','repertoireAlternatives'].map(kind=>[kind,summarize(samples.flatMap(row=>row.requests.filter(request=>request.kind===kind&&request.computeMs!==undefined).map(request=>request.computeMs)))]))});
  } else {
    const tasks=[];
    for(const kind of ['queue','workspace','resolveTrainingRoute','deck']) {
      const pairs=[];
      for(const row of samples.filter(sample=>sample.contended)) {
        const baseline=samples.find(sample=>sample.iteration===row.iteration&&!sample.contended);
        if(!baseline)continue;
        const contended=row.requests.find(request=>request.kind===kind && request.replyAt!==undefined && request.outcome!=='Error');
        const unblocked=baseline.requests.find(request=>request.kind===kind);
        if(!contended||!unblocked||contended.replyAt===undefined||unblocked.replyAt===undefined)continue;
        const bg=row.requests.find(request=>request.kind==='repertoireAlternatives');
        const verifiedOverlap=bg?.workerEndAt!==undefined?contended.sentAt<bg.workerEndAt:bg?.computeMs!==undefined && contended.sentAt < bg.sentAt + bg.computeMs;
        pairs.push({iteration:row.iteration,baselineTotal:unblocked.replyAt-unblocked.sentAt,contendedTotal:contended.replyAt-contended.sentAt,baselineStart:unblocked.runningAt-unblocked.sentAt,contendedStart:contended.runningAt-contended.sentAt,extraTotal:(contended.replyAt-contended.sentAt)-(unblocked.replyAt-unblocked.sentAt),extraStart:(contended.runningAt-contended.sentAt)-(unblocked.runningAt-unblocked.sentAt),verifiedOverlap,overlapProof:bg?.workerEndAt!==undefined?'synchronized-worker-clock':'conservative-end-bound: dispatch epoch + measured computation duration'});
      }
      tasks.push({kind,pairs,baselineTotal:summarize(pairs.map(pair=>pair.baselineTotal)),contendedTotal:summarize(pairs.map(pair=>pair.contendedTotal)),extraTotal:summarize(pairs.map(pair=>pair.extraTotal)),baselineStart:summarize(pairs.map(pair=>pair.baselineStart)),contendedStart:summarize(pairs.map(pair=>pair.contendedStart)),extraStart:summarize(pairs.map(pair=>pair.extraStart))});
    }
    results.push({key,workload:first.workload,scenario:first.scenario,tasks,replacementTotal:summarize(samples.filter(row=>row.contended).map(row=>row.durationMs)),cancelSettlement:summarize(samples.flatMap(row=>row.cancelAt!==undefined&&row.cancelledAt!==undefined?[row.cancelledAt-row.cancelAt]:[])),publishedGenerations:[...new Set(samples.map(row=>row.publishedGeneration).filter(value=>value!==undefined))]});
  }
}
const navigationPath=join(directory,'browser-actual/study-worker-navigation.jsonl');
let navigation=null;
if(existsSync(navigationPath)) {
  const samples=readFileSync(navigationPath,'utf8').trim().split('\n').filter(Boolean).map(JSON.parse).filter(row=>!row.warmup);
  const pairs=samples.filter(row=>row.contended).flatMap(row=>{const baseline=samples.find(other=>other.iteration===row.iteration&&!other.contended);return baseline?[{iteration:row.iteration,baseline:baseline.navigationToUsableMs,contended:row.navigationToUsableMs,extra:row.navigationToUsableMs-baseline.navigationToUsableMs}]:[];});
  navigation={pairs,baseline:summarize(pairs.map(pair=>pair.baseline)),contended:summarize(pairs.map(pair=>pair.contended)),extra:summarize(pairs.map(pair=>pair.extra))};
}
const report={generatedAt:new Date().toISOString(),statistics:'Nearest-rank percentiles; warmups and initial/stopped probes excluded; matched iteration pairs only.',results,navigation};
writeFileSync(join(directory,'summary.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({groups:results.length,navigationPairs:navigation?.pairs.length??0,summary:join(directory,'summary.json')}));
