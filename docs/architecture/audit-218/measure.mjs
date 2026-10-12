// Audit-only HTTP/Celery/PostgreSQL characterization. Run serially under runtime.py.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {appendFileSync,writeFileSync,readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';
import os from 'node:os';
const evidence='/Users/andy/tempo/test-results/audit-218-2026-10-12';
const project='tempo-pg-regressions-21800-a2182026';
const state=resolve('.audit-218-state');
const compose=['compose','-p',project,'-f','docker-compose.postgres.test.yml','-f',`${state}/override.json`];
const env={...process.env,TEMPO_PG_TEST_SECRETS:state,TEMPO_PG_TEST_PORT:'4183'};
const docker=(...args)=>execFileSync('docker',[...compose,...args],{env,encoding:'utf8',timeout:180000});
const raw=[];
const append=value=>{raw.push(value);appendFileSync(`${evidence}/raw-http.jsonl`,JSON.stringify(value)+'\n');};
const delay=ms=>new Promise(done=>setTimeout(done,ms));
async function request(path, options={}) {
  const response=await fetch(`http://127.0.0.1:4182/api/${path}`,{signal:AbortSignal.timeout(15000),...options,headers:{Connection:'close',...options.headers}});
  const value=await response.json();
  if(!response.ok) throw Object.assign(new Error(`${path}: HTTP ${response.status}: ${JSON.stringify(value)}`),{status:response.status});
  if(response.status!==202) return value;
  const deadline=performance.now()+5000;
  while(performance.now()<deadline) {
    const receipt=await request(`operations/${value.operation_id}`);
    if(receipt.state==='complete') return receipt.response;
    if(receipt.state==='failed') throw new Error(JSON.stringify(receipt.error));
    await delay(10);
  }
  throw new Error('Operation watchdog exceeded');
}
const post=(path,payload,id=randomUUID(),method='POST')=>request(path,{method,headers:{'Content-Type':'application/json','Idempotency-Key':id},body:JSON.stringify(payload)});
async function sample(condition,block,iteration,warmup=false) {
  const started=performance.now();
  let row={condition,block,iteration,warmup,epoch_ms:Date.now(),host_load:os.loadavg(),free_memory_bytes:os.freemem()};
  try {
    const queue=await request('queue/window?limit=2');
    const [card,next]=queue.cards;
    assert(card&&next,'Fixture queue has two cards');
    const operation=randomUUID();
    const payload={outcome:'correct',queue_entry_id:card.queue_entry_id,attempt_id:`audit-${operation}`,expected_revision:card.revision,
      prepared_next:{queue_entry_id:next.queue_entry_id,card_id:next.id,revision:next.revision,cycle:next.queue_cycle??next.cycle??0,local_date:queue.local_date}};
    const saveStarted=performance.now();
    const result=await post(`cards/${card.id}/review`,payload,operation);
    const saved=performance.now();
    assert.equal(result.persisted,true);
    const successor=await request('queue/window?limit=2');
    assert.equal(successor.cards[0].id,next.id);
    row={...row,operation_id:operation,card_id:card.id,review_ms:saved-saveStarted,next_available_ms:performance.now()-saved,total_ms:performance.now()-started,
      continuation_authorized:Boolean(result.continuation??result.prepared_continuation??result.prepared_next),result_keys:Object.keys(result),ok:true};
    if(iteration===0&&!warmup) {
      assert.deepEqual(await post(`cards/${card.id}/review`,payload,operation),result);
      assert.deepEqual(await post(`cards/${card.id}/review`,payload,randomUUID()),result);
    }
  } catch(error) {row={...row,ok:false,error:String(error),cause:error.cause?{message:error.cause.message,code:error.cause.code}:null,total_ms:performance.now()-started};}
  append(row);
  if(!row.ok) throw new Error(row.error);
}
function schema(code) {return docker('run','--rm','--no-deps','-e','TEMPO_REDIS_URL=redis://redis:6379/0','schema','python','-c',code);}
function submit(kind) {
  return schema(`import sys; sys.path.insert(0,'/source/backend'); from app.celery_app import celery_app; r=celery_app.send_task('audit.${kind}',args=[180],queue='background'); print(r.id)`).trim();
}
async function waitRunning(id) {
  const deadline=performance.now()+10000;
  while(performance.now()<deadline) {
    if(docker('exec','-T','redis','redis-cli','GET',`audit:running:${id}`).trim()) return;
    await delay(50);
  }
  throw new Error('Injected task did not start: '+id);
}
function revoke(id) {return schema(`import sys;sys.path.insert(0,'/source/backend');from app.celery_app import celery_app;celery_app.control.revoke('${id}',terminate=True,signal='SIGTERM')`);}
async function mode(enabled) {const settings=await request('settings');return post('settings',{...settings,study_only_mode:enabled},randomUUID(),'PUT');}
function activity(label) {
  appendFileSync(`${evidence}/resource-samples.jsonl`,JSON.stringify({label,at:new Date().toISOString(),stats:execFileSync('docker',['stats','--no-stream','--format','{{json .}}',...['api','foreground-worker','background-worker','postgres','redis'].map(s=>`${project}-${s}-1`)],{encoding:'utf8'}).trim().split('\n').map(s=>JSON.parse(s)),processes:execFileSync('ps',['-eo','pid,ppid,pcpu,rss,comm'],{encoding:'utf8'})})+'\n');
}
writeFileSync(`${evidence}/methodology.json`,JSON.stringify({revision:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),node:process.version,host:{platform:os.platform(),arch:os.arch(),cpus:os.cpus().length,memory:os.totalmem()},samples_per_condition:200,blocks:4,block_size:50,warmups:10,clock:'performance.now monotonic; epoch timestamp for correlation',transport:'HTTP to native API, real Celery and PostgreSQL; not browser rendering',fixtures:'5000 synthetic learned tactic cards',synthetic_tasks:'audit.cpu and audit.database; no production handler claims'},null,2));
try {
  for(let index=0;index<10;index++) await sample('warmup',-1,index,true);
  for(const condition of ['cpu','database','optional_stopped','study_only','redis_delayed','cancelled_cpu']) {
    console.log('Starting condition',condition);
    for(let block=0;block<4;block++) {
      const order=block%2?['load','baseline']:['baseline','load'];
      for(const treatment of order) {
        let task;
        if(treatment==='load') {
          if(condition==='cpu'||condition==='database'||condition==='cancelled_cpu') {
            task=submit(condition==='database'?'database':'cpu'); await waitRunning(task);
            if(condition==='cancelled_cpu') {const started=performance.now(); revoke(task); append({type:'cancellation',task_id:task,revoke_sent_ms:performance.now()-started});task=null;}
          }
          if(condition==='optional_stopped') docker('stop','background-worker','background-scheduler');
          if(condition==='study_only') await mode(true);
        }
        activity(`${condition}/${block}/${treatment}/start`);
        try {
          for(let index=0;index<50;index++) {
            if(treatment==='load'&&condition==='redis_delayed') docker('exec','-T','redis','redis-cli','CLIENT','PAUSE','100','WRITE');
            await sample(treatment==='load'?condition:`baseline_${condition}`,block,index);
          }
        } finally {
          if(task) revoke(task);
          if(treatment==='load'&&condition==='optional_stopped') docker('start','background-worker','background-scheduler');
          if(treatment==='load'&&condition==='study_only') await mode(false);
        }
        activity(`${condition}/${block}/${treatment}/end`);
      }
    }
  }
  // Outage is a failure/recovery proof rather than a successful-latency cohort.
  const queue=await request('queue/window?limit=2');const card=queue.cards[0];const operation=randomUUID();
  const payload={outcome:'correct',queue_entry_id:card.queue_entry_id,attempt_id:`audit-${operation}`,expected_revision:card.revision};
  docker('stop','redis');
  try {
    const started=performance.now();let error;
    try {await post(`cards/${card.id}/review`,payload,operation);}catch(caught){error=String(caught);}
    append({type:'redis_outage',duration_ms:performance.now()-started,error,operation_id:operation});assert(error,'No false success during Redis outage');
  }finally {docker('start','redis');}
  let resumed;const deadline=performance.now()+45000;
  while(performance.now()<deadline) {try{resumed=await post(`cards/${card.id}/review`,payload,operation);break;}catch{await delay(100);}}
  assert(resumed?.persisted,'Same review intent recovers after Redis outage');
  assert.deepEqual(await post(`cards/${card.id}/review`,payload,randomUUID()),resumed);
  append({type:'redis_recovery',persisted_once_verified_by_fixture:true,operation_id:operation});
  console.log(docker('run','--rm','--no-deps','schema','python','/audit/fixture.py','verify'));
  // Existing native Study-only proof owns a separate temporary database.
  console.log(docker('run','--rm','--no-deps','schema','python','/source/scripts/check_postgres_study_only.py'));
  console.log(execFileSync('npx',['playwright','test','tests/browser/study-only-mode.spec.ts','--project=chromium'],{encoding:'utf8',timeout:240000,env:{...env,TEMPO_DOCKER_URL:'http://127.0.0.1:4183',TEMPO_TEST_COMPOSE_PROJECT:project,TEMPO_TEST_OUTPUT_DIR:`${evidence}/native-browser`}}));
  // Separate opt-in instrumentation cohort; never pooled with latency measurements.
  const override=JSON.parse(readFileSync(`${state}/override.json`,'utf8'));
  for(const service of ['api','foreground-worker','background-worker']) override.services[service].environment.TEMPO_TRAINING_TIMINGS='1';
  writeFileSync(`${state}/override.json`,JSON.stringify(override));
  docker('up','-d','--no-deps','api','foreground-worker','background-worker');
  const readyDeadline=performance.now()+60000;
  while(performance.now()<readyDeadline){try{await request('health');break;}catch{await delay(100);}}
  for(let iteration=0;iteration<20;iteration++) await sample('instrumented_idle',0,iteration);
  const tracedTask=submit('cpu');await waitRunning(tracedTask);
  try{for(let iteration=0;iteration<20;iteration++) await sample('instrumented_cpu',0,iteration);}finally{revoke(tracedTask);}
} finally {
  try{docker('start','redis');}catch{}
  try{await mode(false);}catch{}
}
