import { build } from 'esbuild';
import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { readFileSync, writeFileSync, mkdirSync, appendFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import os from 'node:os';
import { Chess } from 'chess.js';

const evidence = resolve(process.env.TEMPO_INVESTIGATION_DIR ?? '../../test-results/study-worker-contention-2026-10-11');
const sampleCount = Number(process.env.TEMPO_INVESTIGATION_SAMPLES ?? 50);
const probe = process.argv.includes('--probe');
const traced = process.argv.includes('--traced');
const profile = process.argv.includes('--profile');
const requestedScenario = process.env.TEMPO_INVESTIGATION_SCENARIO ?? 'all';
const requestedWorkload = process.env.TEMPO_INVESTIGATION_WORKLOAD ?? 'all';
const outputFile=join(evidence,`raw-${traced?'traced':'untraced'}-${requestedScenario}-${requestedWorkload}${probe?'-probe':''}.jsonl`);
if (existsSync(outputFile)) throw new Error('Output cohort already exists; preserve it and select a new evidence directory rather than pooling reruns.');
const buildDirectory = resolve('test-results/worker-investigation-build', `${traced?'traced':'untraced'}${profile?'-profile':''}`);
mkdirSync(buildDirectory, { recursive: true }); mkdirSync(evidence, { recursive: true });
if (profile) mkdirSync(join(evidence,'private'), {recursive:true,mode:0o700});
const snapshotPath=join(evidence,'private/repertoire-lines.json');
const publicFixtureOnly=/^(high-distinctness|duplicate-heavy)-\d+$/.test(requestedWorkload);
if (!existsSync(snapshotPath) && !publicFixtureOnly) throw new Error('Private snapshot is required for actual or representative workloads; select an explicit public stress workload for public reproduction.');
const privateSnapshot = existsSync(snapshotPath)?JSON.parse(readFileSync(snapshotPath,'utf8')):{ lines:[] };
const deckRecords = JSON.parse(readFileSync('public/data/tactics-decks.json','utf8'));
const deck = deckRecords.filter(record => record.DeckId === deckRecords[0].DeckId).slice(0,32);
const sourceFiles = ['app/lib/background-study.ts','app/lib/study.worker.ts','app/lib/study-computation.ts','app/lib/study-position-store.ts','app/lib/repertoire-alternative-index.ts','app/lib/workspace-data.ts','app/lib/position-similarity.ts','app/utils/canonical-line.ts'];
const fingerprints = Object.fromEntries(sourceFiles.map(file => [file,createHash('sha256').update(readFileSync(file)).digest('hex')]));
const phasePlugin = { name:'investigation-only-bindings', setup(builder) {
  builder.onLoad({filter:/background-study\.ts$/},args => ({contents: readFileSync(args.path,'utf8').replace('new URL("./study.worker.ts", import.meta.url)','new URL("/study.worker.js", window.location.href)'),loader:'ts'}));
  if (!traced) return;
  builder.onLoad({filter:/study\.worker\.ts$/},args => {
    const source=readFileSync(args.path,'utf8')
      .replace('const data = parseData(studyRequestSchema, raw, "study worker request");', 'const validationStartedAt = performance.now();\nglobalThis.__investigationClock = {receivedAt:performance.timeOrigin + validationStartedAt};\nconst data = parseData(studyRequestSchema, raw, "study worker request");\nglobalThis.__investigationClock.validatedAt = performance.timeOrigin + performance.now();\nglobalThis.__investigationStages.push({name:"request-validation",duration:performance.now()-validationStartedAt});')
      .replace('const computeStartedAt = performance.now();', 'const computeStartedAt = performance.now();\nglobalThis.__investigationClock.computeStartedAt = performance.timeOrigin + computeStartedAt;');
    return { contents: `const investigationStages = globalThis.__investigationStages = [];\nglobalThis.__fenNormalization = {calls:0,ms:0};\nconst originalPostMessage = self.postMessage.bind(self);\nself.postMessage = value => { const fenNormalization=globalThis.__fenNormalization; originalPostMessage({...value, __investigation: {at: performance.timeOrigin + performance.now(), ...globalThis.__investigationClock, stages: investigationStages.splice(0),fenNormalization}}); globalThis.__fenNormalization={calls:0,ms:0}; };\n` + source, loader:'ts'};
  });
  builder.onLoad({filter:/canonical-line\.ts$/},args => ({ contents:readFileSync(args.path,'utf8').replace('return fen.trim().split(/\\s+/).slice(0, 4).join(" ");','const startedAt=performance.now();\nconst result=fen.trim().split(/\\s+/).slice(0, 4).join(" ");\nif(globalThis.__fenNormalization){globalThis.__fenNormalization.calls++;globalThis.__fenNormalization.ms+=performance.now()-startedAt;}\nreturn result;'),loader:'ts'}));
  builder.onLoad({filter:/study-computation\.ts$/},args => {
    let source = readFileSync(args.path,'utf8');
    source = source.replace(/return buildRepertoireAlternativeSnapshot\(task.generation,\s*indexRepertoirePositions\(task.lines.map\(canonicalizeLine\)\)\);/, `const canonical = investigationStage("canonicalization", () => task.lines.map(canonicalizeLine));\nconst positions = investigationStage("position-index", () => indexRepertoirePositions(canonical));\nreturn investigationStage("membership", () => buildRepertoireAlternativeSnapshot(task.generation, positions));`);
    source = `function investigationStage(name, operation) { const start=performance.now(); const result=operation(); globalThis.__investigationStages?.push({name,duration:performance.now()-start}); return result; }\n` + source;
    return {contents:source, loader:'ts'};
  });
} };
await build({entryPoints:['docs/architecture/audit-218/harness.js'],bundle:true,format:'esm',platform:'browser',outfile:join(buildDirectory,'harness.js'),minify:true,plugins:[phasePlugin],define:{__TEMPO_DEMO__:'false',__TEMPO_API_URL__:'""'}});
await build({entryPoints:['app/lib/study.worker.ts'],bundle:true,format:'esm',platform:'browser',outfile:join(buildDirectory,'study.worker.js'),minify:true,plugins:[phasePlugin]});

function fixture(recordCount, distribution) {
  const lines = []; let remaining = recordCount;
  if (distribution === 'representative') {
    const repertoireGroups = [...Map.groupBy(privateSnapshot.lines, line => line.repertoire_id).values()].map(sourceLines => ({ sourceLines, sourceRecords: sourceLines.reduce((total, line) => total + line.moves.length + 1, 0), preparedRecords: 0, nextLine: 0 }));
    const sourceRecords = repertoireGroups.reduce((total, group) => total + group.sourceRecords, 0);
    let index=0;
    while (remaining) {
      const selectedGroup = repertoireGroups.toSorted((left, right) => (recordCount * right.sourceRecords / sourceRecords - right.preparedRecords) - (recordCount * left.sourceRecords / sourceRecords - left.preparedRecords))[0];
      const line = selectedGroup.sourceLines[selectedGroup.nextLine++ % selectedGroup.sourceLines.length];
      const count=Math.min(remaining,line.moves.length+1);
      lines.push({...line,id:`modeled-${index}`,moves:line.moves.slice(0,count-1)});
      selectedGroup.preparedRecords += count;
      remaining-=count;index++;
    }
    return {lines};
  }
  while (remaining) {
    const distinct=distribution==='high-distinctness';
    let startingFen=new Chess().fen();
    if (distinct) {
      const board=new Chess('7k/8/8/8/8/8/8/K7 w - - 0 1');
      let layout=(Math.imul(lines.length,40503)+360036)&65535;
      for (let file=0;file<8;file++) {board.put({type:'p',color:'w'},`${String.fromCharCode(97+file)}${2+(layout&3)}`);layout>>>=2;}
      startingFen=board.fen();
    }
    const moves=(distinct?['a1b1','h8h7','b1c1','h7g7','c1d1','g7f7','d1e1','f7e7','e1f1']:['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6']).slice(0,remaining-1);
    lines.push({id:`fixture-${lines.length}`,repertoire_id:'fixture',repertoire_name:'Fixture',name:'Fixture',trained_color:'white',start_fen:startingFen,moves});
    remaining-=moves.length+1;
  }
  return {lines};
}
let currentPayload=privateSnapshot;
const server=createServer((request,response) => {
  response.setHeader('Cross-Origin-Opener-Policy','same-origin');response.setHeader('Cross-Origin-Embedder-Policy','require-corp');
  if (request.url==='/fixture.json' || request.url==='/api/repertoire/lines') {response.setHeader('Content-Type','application/json');response.end(JSON.stringify(currentPayload));}
  else if (request.url==='/deck.json') {response.setHeader('Content-Type','application/json');response.end(JSON.stringify(deck));}
  else if (request.url==='/harness.js' || request.url==='/study.worker.js') {response.setHeader('Content-Type','application/javascript');response.end(readFileSync(join(buildDirectory,request.url.slice(1))));}
  else {response.setHeader('Content-Type','text/html');response.end(`<!doctype html><title>Private study worker investigation</title><script>window.__investigationTraced=${traced}</script><script type="module" src="/harness.js"></script>`);}
});
await new Promise(done => server.listen(0,'127.0.0.1',done));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true});
let traceSession;
if (profile) {
  traceSession = await browser.newBrowserCDPSession();
  await traceSession.send('Tracing.start', { categories: 'devtools.timeline,v8,blink.user_timing,disabled-by-default-v8.cpu_profiler', transferMode: 'ReturnAsStream' });
}
const environment={revision:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),sourceFiles:fingerprints,node:process.version,browser:browser.version(),platform:os.platform(),architecture:os.arch(),cpus:os.cpus()[0].model,logicalCpus:os.cpus().length,memoryBytes:os.totalmem(),sampleCount,traced,fixtureSeed:360036,createdAt:new Date().toISOString(),workerUrlBindingOnly:true};
environment.harnessSha256=createHash('sha256').update(readFileSync('docs/architecture/audit-218/harness.js')).digest('hex');
environment.driverSha256=createHash('sha256').update(readFileSync(import.meta.filename)).digest('hex');
environment.bundles=Object.fromEntries(['harness.js','study.worker.js'].map(name=>[name,createHash('sha256').update(readFileSync(join(buildDirectory,name))).digest('hex')]));
const environmentArtifact = `environment-${traced?'traced':'untraced'}-${requestedScenario}-${requestedWorkload}-${Date.now()}.json`;
writeFileSync(join(evidence,environmentArtifact),JSON.stringify(environment,null,2)+'\n');
const workloads=probe?[{name:'actual',payload:privateSnapshot}]:[{name:'actual',payload:privateSnapshot},...['high-distinctness','duplicate-heavy','representative'].flatMap(distribution => [1000,5000,10000,25000,50000,100000].map(size => ({name:`${distribution}-${size}`,payload:null,distribution,size})))];
const append = value => appendFileSync(outputFile,JSON.stringify(value)+'\n');
function hostActivity() {
  const processes = execFileSync('ps', ['-eo', 'pid,ppid,pcpu,rss,comm'], { encoding: 'utf8' }).trim().split('\n').slice(1).flatMap(line => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(.+)$/);
    return match ? [{ pid: Number(match[1]), parent: Number(match[2]), cpuPercent: Number(match[3]), rssKiB: Number(match[4]), executable: match[5].split('/').at(-1) }] : [];
  });
  const ownedPids = new Set([process.pid]);
  for (let pass = 0; pass < processes.length; pass++) for (const candidate of processes) if (ownedPids.has(candidate.parent)) ownedPids.add(candidate.pid);
  const external = processes.filter(candidate => !ownedPids.has(candidate.pid));
  const activeOtherTestProjects = [...new Set(execFileSync('docker', ['ps', '--format', '{{.Label "com.docker.compose.project"}}'], { encoding: 'utf8' }).trim().split('\n').filter(project => /^tempo-(?:pg-)?regressions-\d+-/.test(project)))];
  const locallyPaused = existsSync(join(evidence, 'pause-timing'));
  const busy = locallyPaused || activeOtherTestProjects.length > 0 || external.some(candidate => /^(node|npm|cargo|rustc|cc1|clang)/.test(candidate.executable) && candidate.cpuPercent > 75) || external.some(candidate => candidate.executable === 'chrome-headless-shell' && candidate.cpuPercent > 5) || external.reduce((total, candidate) => total + candidate.cpuPercent, 0) > 650;
  return { at: new Date().toISOString(), busy, locallyPaused, activeOtherTestProjects, externalCpuPercent: external.reduce((total, candidate) => total + candidate.cpuPercent, 0), competing: external.toSorted((left,right) => right.cpuPercent-left.cpuPercent).slice(0,8), ownedProcesses: processes.filter(candidate => ownedPids.has(candidate.pid)) };
}
async function awaitMeasurementWindow() {
  let activity = hostActivity();
  while (activity.busy) {
    append({ type: 'deferred-host-activity', ...activity });
    console.log('Deferred next sample for competing host activity; other tasks remain untouched.');
    await new Promise(done => setTimeout(done, 10_000));
    activity = hostActivity();
  }
  return activity;
}
async function retainedWorkerHeap() {
  const inspector = await browser.newBrowserCDPSession();
  let sessionId;
  let nextMessageId = 0;
  const pendingMessages = new Map();
  inspector.on('Target.receivedMessageFromTarget', event => {
    if (event.sessionId !== sessionId) return;
    const message = JSON.parse(event.message);
    const pending = pendingMessages.get(message.id);
    if (!pending) return;
    pendingMessages.delete(message.id);
    clearTimeout(pending.timeout);
    if (message.error) pending.reject(new Error(message.error.message));
    else pending.resolve(message.result);
  });
  const request = method => new Promise((resolve, reject) => {
    const id = ++nextMessageId;
    const timeout = setTimeout(() => { pendingMessages.delete(id); reject(new Error(`Worker profiling command ${method} did not reply within its diagnostic deadline`)); }, 5000);
    pendingMessages.set(id, {resolve,reject,timeout});
    inspector.send('Target.sendMessageToTarget', {sessionId,message:JSON.stringify({id,method})}).catch(error => {
      clearTimeout(timeout);pendingMessages.delete(id);reject(error);
    });
  });
  try {
    const {targetInfos} = await inspector.send('Target.getTargets');
    const target = targetInfos.find(target => target.type === 'worker' && target.url === `${origin}/study.worker.js`);
    if (!target) return {available:false,reason:'Dedicated study-worker target not exposed by this browser protocol'};
    ({sessionId} = await inspector.send('Target.attachToTarget', {targetId:target.targetId,flatten:false}));
    const beforeGc = await request('Runtime.getHeapUsage');
    await request('HeapProfiler.collectGarbage');
    return {available:true,beforeGc,afterGc:await request('Runtime.getHeapUsage'),scope:'Current dedicated-worker isolate heap after an idle pipeline; explicit GC; not peak or exclusive process RSS'};
  } catch (error) {
    return {available:false,reason:error.message,scope:'Worker-isolate profiling unavailable in this collector; main renderer and shared process metrics remain separate'};
  } finally {
    for (const pending of pendingMessages.values()) clearTimeout(pending.timeout);
    if (sessionId) await inspector.send('Target.detachFromTarget', {sessionId}).catch(() => undefined);
    await inspector.detach();
  }
}
try {
  for (const workload of workloads) {
    if (requestedWorkload!=='all' && workload.name!==requestedWorkload) continue;
    currentPayload=workload.payload ?? fixture(workload.size,workload.distribution);
    const context=await browser.newContext();const page=await context.newPage();
    let memorySession;
    if (profile) { memorySession=await context.newCDPSession(page);await memorySession.send('Performance.enable'); }
    page.on('pageerror',error=>console.error('Browser error:',error.message));
    await awaitMeasurementWindow();
    await page.goto(origin);await page.waitForFunction(()=>Boolean(window.investigation));
    const configured=await page.evaluate(()=>window.investigation.configure());
    if (memorySession) append({type:'renderer-memory',name:workload.name,scope:'main renderer; not exclusive worker or peak memory',metrics:(await memorySession.send('Performance.getMetrics')).metrics.filter(metric=>metric.name.includes('Heap') || metric.name.includes('TaskDuration')),workerHeap:await retainedWorkerHeap(),processMemory:hostActivity().ownedProcesses,processMemoryScope:'instantaneous RSS KiB for driver/browser descendants; shared pages may be counted more than once; not exclusive worker or peak'});
    append({type:'workload',name:workload.name,bytes:Buffer.byteLength(JSON.stringify(currentPayload)),...configured});
    console.log(`Configured ${workload.name}: ${configured.positions} positions`);
    if(workload.size && configured.positions!==workload.size) throw new Error(`Unexpected position count ${configured.positions} != ${workload.size}`);
    const scenarios=requestedScenario==='audit'?['foreground','cancel']:requestedScenario==='all'?(workload.name==='actual' || workload.name==='representative-100000'?['pipeline','foreground','cancel','invalidate','failure','decode','protocol']:['pipeline','foreground']):[requestedScenario];
    for(const scenario of scenarios) {
      const count=probe?1:sampleCount;
      let attempt = 0;
      for(let iteration=-(probe?0:10);iteration<count;iteration++) {
        const activity = await awaitMeasurementWindow();
        const common={type:'sample',workload:workload.name,scenario,iteration,attempt:attempt++,warmup:iteration<0,environmentArtifact,hostLoad:os.loadavg(),freeMemoryBytes:os.freemem(),hostActivity:activity};
        const trialRows = [];
        async function measured(operation, input) {
          const result=await page.evaluate(operation,input);
          const first=result.startedAt ?? result.requests[0]?.sentAt;
          if (first !== undefined) result.longTasks=await page.evaluate(({first,end})=>window.investigation.collectLongTasks(first,end),{first,end:first+result.durationMs});
          return result;
        }
        if(scenario==='pipeline') trialRows.push({...common,...await measured(()=>window.investigation.pipeline())});
        else {
          const order=iteration%2? [false,true]:[true,false];
          for(const contended of order) trialRows.push({...common,...await measured(({scenario,contended})=>window.investigation.contention(scenario,contended),{scenario,contended})});
        }
        const activityAtEnd = hostActivity();
        for (const row of trialRows) append({ ...row, hostActivityAtEnd: activityAtEnd, contaminated: activityAtEnd.busy });
        if (activityAtEnd.busy) {
          console.log(`Excluded contaminated ${workload.name} ${scenario} attempt; repeating the same iteration after admission clears.`);
          iteration--;
          continue;
        }
        if (memorySession && scenario === 'pipeline' && iteration >= 0) {
          await memorySession.send('HeapProfiler.collectGarbage');
          append({type:'renderer-memory',name:workload.name,iteration,scope:'after published pipeline and explicit main-renderer GC; retained main isolate only; not exclusive worker or peak',metrics:(await memorySession.send('Performance.getMetrics')).metrics.filter(metric=>metric.name.includes('Heap') || metric.name.includes('TaskDuration')),workerHeap:await retainedWorkerHeap(),processMemory:hostActivity().ownedProcesses,processMemoryScope:'instantaneous RSS KiB; browser/driver processes are shared and shared pages can be counted more than once'});
        }
        if(iteration>=0 && (probe || iteration%5===0)) console.log(`${workload.name} ${scenario} ${iteration+1}/${count}`);
      }
    }
    await context.close();
  }
} finally {
  if (traceSession) {
    const completed = new Promise(done => traceSession.once('Tracing.tracingComplete',done));
    await traceSession.send('Tracing.end');
    const {stream} = await completed;
    const tracePath=join(evidence,'private',`browser-trace-${Date.now()}.json`);
    writeFileSync(tracePath,'',{mode:0o600});
    for (;;) {
      const chunk=await traceSession.send('IO.read',{handle:stream});
      appendFileSync(tracePath,chunk.base64Encoded?Buffer.from(chunk.data,'base64'):chunk.data);
      if(chunk.eof)break;
    }
    await traceSession.send('IO.close',{handle:stream});
    console.log(`Private profiling trace preserved; timing cohort traced=${traced}`);
  }
  await browser.close();await new Promise(done=>server.close(done));
}
console.log(outputFile);
