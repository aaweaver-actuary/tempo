import test from 'node:test';
import assert from 'node:assert/strict';
import { IntelligenceClient } from '@tempo/workbench-web';
import { z } from 'zod';
const bootstrapSchema=z.object({schema_version:z.literal(1),workspace_id:z.string(),capabilities:z.array(z.string()),policy_version:z.string()});
const connection={origin:'http://127.0.0.1:8049',workspace:'fixture',credential:'synthetic-caller'};
const bootstrap={schema_version:1,workspace_id:'fixture',capabilities:['analysis-v1'],policy_version:'c-analysis-v1'};
test('workspace and exact authenticated origin are validated',async()=>{
  assert.throws(()=>new IntelligenceClient({...connection,origin:'http://host/path'}));
  let called=false;
  const client=new IntelligenceClient(connection,async(url,options)=>{called=true;assert.equal(url,connection.origin+'/v1/analysis/bootstrap');assert.equal(options.headers.Authorization,'Bearer synthetic-caller');assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');return Response.json(bootstrap);});
  assert.deepEqual(await client.bootstrap(),bootstrap);assert.ok(called);
  await assert.rejects(()=>client.request('//other.invalid/v1/analysis/bootstrap',bootstrapSchema));
});
test('foreign response identity fails',async()=>{
  const client=new IntelligenceClient(connection,async()=>Response.json({...bootstrap,workspace_id:'other'}));
  await assert.rejects(()=>client.bootstrap(),/another workspace/);
});
test('connection replacement fences delayed responses',async()=>{
  let deliver;const client=new IntelligenceClient(connection,()=>new Promise(resolve=>{deliver=resolve;}));
  const request=client.bootstrap();client.disconnect();deliver(Response.json(bootstrap));await assert.rejects(()=>request,/response ignored/);
});
test('authorization failure stays explicit',async()=>{
  const client=new IntelligenceClient(connection,async()=>Response.json({error:'unauthorized'},{status:401}));
  await assert.rejects(()=>client.bootstrap(),/Reconnect/);
});

test('disconnect during response decoding fences the decoded body',async()=>{
  let decode;const client=new IntelligenceClient(connection,async()=>({ok:true,json:()=>new Promise(resolve=>{decode=resolve;})}));
  const request=client.bootstrap();await new Promise(resolve=>setImmediate(resolve));client.disconnect();decode(bootstrap);await assert.rejects(()=>request,/response ignored/);
  await assert.rejects(()=>client.bootstrap(),/Reconnect/);
});

test('bootstrap requires the declared Analysis capability and policy',async()=>{
  for(const value of [{...bootstrap,capabilities:[]},{...bootstrap,policy_version:'unsupported'}]){
    const client=new IntelligenceClient(connection,async()=>Response.json(value));await assert.rejects(()=>client.bootstrap(),/incompatible/);
  }
});

const {PendingCommands}=await import('@tempo/workbench-web');
const {createHash}=await import('node:crypto');
function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values};}
function commandHarness(connectionOverride={}){
  const saved=storage();const calls=[];const receipts=new Map();let failure='lost';
  const authority={...connection,...connectionOverride};
  const client=new IntelligenceClient(authority,async(url,options)=>{
    calls.push({url,body:options.body});
    if(options.method==='GET')return receipts.has(url.split('/').at(-1))?Response.json(receipts.get(url.split('/').at(-1))):Response.json({error:'missing'},{status:404});
    if(failure==='auth')return Response.json({error:'unauthorized'},{status:401});
    const command=JSON.parse(options.body);const run_id=new URL(url).pathname.split('/')[4];
    const payload={run_id,schema_version:1,budget:250,...command};
    const request_digest='sha256:'+createHash('sha256').update(JSON.stringify(payload,Object.keys(payload).sort())).digest('hex');
    const receipt={schema_version:1,workspace_id:authority.workspace,accepted:true,command_id:command.command_id,request_digest};
    const prior=receipts.get(command.command_id);
    if(prior && prior.request_digest!==request_digest)return Response.json({error:'analysis_command_conflict'},{status:409});
    receipts.set(command.command_id,receipt);
    if(failure==='lost')throw new Error('response lost after commit');
    return Response.json(receipt);
  });
  return {saved,calls,receipts,client,commands:new PendingCommands(saved),setFailure:value=>{failure=value;}};
}
const runPath='/v1/analysis/runs/run/commands';
const durableCommand={schema_version:1,command_id:'immutable-id',action:'continue',expected_revision:1,budget:250};

test('R4 unresolved command cannot be overwritten and exact retry reuses bytes',async()=>{
  const h=commandHarness();await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand),/response lost/);
  const pending=await h.commands.pending(h.client);const bytes=h.calls[0].body;
  await assert.rejects(()=>h.commands.submit(h.client,runPath,{...durableCommand,command_id:'other',action:'cancel'}),/pending command/);
  assert.equal(h.calls.length,1);h.setFailure('success');await h.commands.retry(h.client,pending);
  assert.equal(h.calls[1].body,bytes);assert.equal(await h.commands.pending(h.client),null);assert.equal(h.receipts.size,1);
});
test('R4 reload uses authoritative receipt and missing receipt preserves uncertainty',async()=>{
  const h=commandHarness();await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand));
  const pending=await h.commands.pending(h.client);const coordinator=new PendingCommands(h.saved);
  const receipt=h.receipts.get(durableCommand.command_id);h.receipts.clear();
  await assert.rejects(()=>coordinator.recover(h.client,pending));assert.deepEqual(await coordinator.pending(h.client),pending);
  h.receipts.set(durableCommand.command_id,receipt);await coordinator.recover(h.client,pending);assert.equal(await coordinator.pending(h.client),null);
});
test('R4 workspace and origin switching retain separate unresolved commands',async()=>{
  const h=commandHarness();await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand));
  for(const change of [{workspace:'workspace-b'},{origin:'http://127.0.0.1:8050'}]){
    const other=new IntelligenceClient({...connection,...change},async()=>{throw new Error('other response lost');});
    assert.equal(await h.commands.pending(other),null);
    await assert.rejects(()=>h.commands.submit(other,runPath,{...durableCommand,command_id:'other'}));
    assert.equal((await h.commands.pending(h.client)).origin,connection.origin);
    assert.equal((await h.commands.pending(other)).workspace,other.connection.workspace);
  }
  assert.equal(h.calls.length,1);
});
test('R4 authentication rejection and receipt mismatch retain pending evidence',async()=>{
  const h=commandHarness();h.setFailure('auth');await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand),/Reconnect/);
  const pending=await h.commands.pending(h.client);h.receipts.set(durableCommand.command_id,{schema_version:1,workspace_id:'fixture',accepted:true,command_id:durableCommand.command_id,request_digest:'sha256:'+'0'.repeat(64)});
  await assert.rejects(()=>h.commands.recover(h.client,pending),/identity mismatch/);assert.deepEqual(await h.commands.pending(h.client),pending);
});
test('R4 storage failure prevents transmission and legacy command migrates intact',async()=>{
  const h=commandHarness();h.saved.setItem=()=>{throw new Error('storage unavailable');};
  await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand),/storage unavailable/);assert.equal(h.calls.length,0);
  const other=commandHarness();other.saved.setItem('tempo-analysis-command-v1',JSON.stringify({...connection,path:runPath,command:durableCommand}));
  const pending=await other.commands.pending(other.client);assert.equal(pending.body,JSON.stringify(durableCommand));
  assert.equal(other.saved.getItem('tempo-analysis-command-v1'),null);
  assert.ok(!JSON.stringify([...other.saved.values.values()]).includes('synthetic-caller'));
});
for(const action of ['continue','retry','cancel','resume'])test(`R4 ${action} command survives loss and receipt recovery`,async()=>{
  const h=commandHarness();await assert.rejects(()=>h.commands.submit(h.client,runPath,{...durableCommand,action}));
  const pending=await h.commands.pending(h.client);await h.commands.recover(h.client,pending);assert.equal(h.receipts.size,1);
});

const creation={schema_version:1,workspace_id:'fixture',revision:1,input_id:'accepted-input',input_revision:2,position_budget:250};
function runHarness(){
  const saved=storage(),calls=[],runs=new Map(),receipts=new Map();
  let transmitted;const transmission=new Promise(resolve=>{transmitted=resolve;});
  let postMode='lost',getMode='success',hold,transform=value=>value;
  const client=new IntelligenceClient(connection,async(url,options)=>{
    calls.push({url,method:options.method,body:options.body});
    transmitted();
    const path=new URL(url).pathname;
    if(options.method==='GET'){
      if(getMode==='auth')return Response.json({error:'unauthorized'},{status:401});
      const id=decodeURIComponent(path.split('/').at(-1));
      if(path.startsWith('/v1/analysis/commands/'))return receipts.has(id)?Response.json(receipts.get(id)):Response.json({error:'missing'},{status:404});
      if(getMode==='missing'||!runs.has(id))return Response.json({error:'analysis_run_not_found'},{status:404});
      return Response.json(transform(structuredClone(runs.get(id))));
    }
    if(postMode==='auth')return Response.json({error:'unauthorized'},{status:401});
    if(hold)await hold;
    const request=JSON.parse(options.body);
    let result;
    if(path==='/v1/analysis/runs'){
      const scope={expected_revision:null,policy_version:'c-analysis-v1',ranking_source:'actual',source_evidence_ids:[],ranking_evidence_id:null,...request};
      if(runs.has(request.run_id))assert.deepEqual(runs.get(request.run_id).scope,scope);
      result={schema_version:1,workspace_id:request.workspace_id,run_id:request.run_id,revision:request.revision,scope};
      runs.set(request.run_id,result);
    }else{
      const payload={run_id:path.split('/')[4],schema_version:1,budget:250,...request};
      result={schema_version:1,workspace_id:'fixture',accepted:true,command_id:request.command_id,
        request_digest:'sha256:'+createHash('sha256').update(JSON.stringify(payload,Object.keys(payload).sort())).digest('hex')};
      receipts.set(request.command_id,result);
    }
    if(postMode==='lost')throw new Error('response lost after commit');
    return Response.json(transform(structuredClone(result)));
  });
  return {saved,calls,runs,client,transmission,commands:new PendingCommands(saved),
    mode:({post,get,gate,change}={})=>{if(post)postMode=post;if(get)getMode=get;if(gate!==undefined)hold=gate;if(change)transform=change;}};
}

test('R7 run creation survives response loss with original identity',async()=>{
  const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation),/response lost/);
  const pending=await h.commands.pending(h.client);assert.equal(pending.kind,'run_create');
  assert.equal(h.runs.size,1);assert.ok(h.runs.has(pending.run_id));
  const coordinator=new PendingCommands(h.saved);
  assert.equal(await coordinator.recoverRun(h.client,pending),pending.run_id);
  assert.equal(await coordinator.pending(h.client),null);
  assert.equal(h.calls.filter(call=>call.method==='POST').length,1);
});

test('R7 repeated Start analysis cannot create a second run',async()=>{
  const h=runHarness();let release;h.mode({post:'success',gate:new Promise(resolve=>{release=resolve;})});
  const original=crypto.randomUUID;let generated=0;
  crypto.randomUUID=()=>{generated++;return original.call(crypto);};
  try{
    const first=h.commands.submitRun(h.client,creation);
    await h.transmission;
    await assert.rejects(()=>h.commands.submitRun(h.client,creation),/creation is unconfirmed/);
    assert.equal(h.calls.length,1);assert.equal(generated,1);
    release();const id=await first;assert.ok(h.runs.has(id));assert.equal(h.runs.size,1);
  }finally{crypto.randomUUID=original;release();}
});

test('R7 missing run status preserves original creation',async()=>{
  const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation));
  const pending=await h.commands.pending(h.client);h.mode({get:'missing'});
  await assert.rejects(()=>h.commands.recoverRun(h.client,pending),/analysis_run_not_found/);
  assert.deepEqual(await h.commands.pending(h.client),pending);
  h.mode({post:'success',get:'success'});
  assert.equal(await h.commands.retryRun(h.client,pending),pending.run_id);
  const posts=h.calls.filter(call=>call.method==='POST');assert.equal(posts.length,2);
  assert.equal(posts[0].body,posts[1].body);assert.equal(h.runs.size,1);
});

test('R7 conflicting run identity fails closed',async()=>{
  const changes={schema_version:2,workspace_id:'foreign',run_id:'different',revision:2,expected_revision:1,input_id:'different',input_revision:3,position_budget:251,
    policy_version:'different',ranking_source:'lichess',ranking_evidence_id:'different',source_evidence_ids:['different']};
  for(const [field,value] of Object.entries(changes)){
    const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation));
    const pending=await h.commands.pending(h.client);
    h.mode({change:status=>({...status,scope:{...status.scope,[field]:value}})});
    await assert.rejects(()=>h.commands.recoverRun(h.client,pending),undefined,field);
    assert.deepEqual(await h.commands.pending(h.client),pending,field);
    assert.equal(h.calls.filter(call=>call.method==='POST').length,1);
  }
});

test('R7 run creation and command mutations share authority gate',async()=>{
  const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation));
  const run=await h.commands.pending(h.client);
  await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand),/creation is unconfirmed/);
  assert.equal(h.calls.length,1);await h.commands.recoverRun(h.client,run);
  await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand),/response lost/);
  const command=await h.commands.pending(h.client);
  await assert.rejects(()=>h.commands.submitRun(h.client,creation),/pending command/);
  await h.commands.recover(h.client,command);h.mode({post:'success'});
  await h.commands.submitRun(h.client,creation);assert.equal(h.runs.size,2);
});

test('R7 origin and workspace switching preserve pending creation',async()=>{
  const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation));
  const pending=await h.commands.pending(h.client),bytes=JSON.stringify([...h.saved.values]);
  for(const change of [{origin:'http://127.0.0.1:8050'},{workspace:'foreign'}]){
    const foreign=new IntelligenceClient({...connection,...change},async()=>assert.fail('Foreign authority received the original creation'));
    assert.equal(await h.commands.pending(foreign),null);
    await assert.rejects(()=>h.commands.recoverRun(foreign,pending),/another authority/);
    await assert.rejects(()=>h.commands.retryRun(foreign,pending),/another authority/);
    assert.equal(JSON.stringify([...h.saved.values]),bytes);
  }
  assert.ok(!bytes.includes('synthetic-caller'));
  assert.equal(await h.commands.recoverRun(h.client,pending),pending.run_id);
});

test('R7 storage failure prevents run creation transmission',async()=>{
  for(const failure of ['write','readback']){
    const h=runHarness();
    if(failure==='write')h.saved.setItem=()=>{throw new Error('storage unavailable');};
    else h.saved.setItem=()=>{};
    await assert.rejects(()=>h.commands.submitRun(h.client,creation),/storage unavailable|Nothing was sent/);
    assert.equal(h.calls.length,0);assert.equal(h.runs.size,0);
  }
});

test('R7 authentication rejection retains creation evidence',async()=>{
  const h=runHarness();h.mode({post:'auth'});
  await assert.rejects(()=>h.commands.submitRun(h.client,creation),/Reconnect/);
  const pending=await h.commands.pending(h.client);h.mode({get:'auth'});
  await assert.rejects(()=>h.commands.recoverRun(h.client,pending),/Reconnect/);
  assert.deepEqual(await h.commands.pending(h.client),pending);
  h.mode({post:'success',get:'success'});
  assert.equal(await h.commands.retryRun(h.client,pending),pending.run_id);
  assert.equal(h.runs.size,1);
});

test('R7 malformed or changed pending creation retains evidence and sends nothing',async()=>{
  const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation));
  const pending=await h.commands.pending(h.client),key=[...h.saved.values.keys()][0],original=h.saved.getItem(key);
  for(const changed of [{...pending,path:'https://foreign.invalid/v1/analysis/runs'},
    {...pending,body:pending.body.replace('accepted-input','changed-input')},{...pending,run_id:'changed'},
    {...pending,kind:'unknown'},{...pending,origin:'http://foreign.invalid'}]){
    const raw=JSON.stringify(changed);h.saved.setItem(key,raw);
    await assert.rejects(()=>h.commands.retryRun(h.client,pending));
    assert.equal(h.saved.getItem(key),raw);assert.equal(h.calls.length,1);
  }
  h.saved.setItem(key,original);await h.commands.recoverRun(h.client,pending);
});

test('R7 disconnected response cannot clear pending creation or another authority',async()=>{
  const h=runHarness();let release;h.mode({post:'success',gate:new Promise(resolve=>{release=resolve;})});
  const sent=h.commands.submitRun(h.client,creation);await h.transmission;
  const pending=await h.commands.pending(h.client);h.client.disconnect();release();
  await assert.rejects(()=>sent,/response ignored/);assert.deepEqual(await h.commands.pending(h.client),pending);
});

test('R7 original v2 command records remain recoverable',async()=>{
  const h=commandHarness();await assert.rejects(()=>h.commands.submit(h.client,runPath,durableCommand));
  const pending=await h.commands.pending(h.client),key=[...h.saved.values.keys()][0];
  const {kind,...old}=pending;old.version=2;h.saved.setItem(key,JSON.stringify(old));
  const restored=await new PendingCommands(h.saved).pending(h.client);
  assert.equal(restored.version,2);assert.equal(restored.body,JSON.stringify(durableCommand));
  await h.commands.recover(h.client,restored);assert.equal(await h.commands.pending(h.client),null);
});

test('R7 incomplete authoritative scope cannot borrow client request defaults',async()=>{
  for(const field of ['expected_revision','policy_version','position_budget','ranking_source','source_evidence_ids','ranking_evidence_id']){
    const omit=status=>{delete status.scope[field];return status;};
    const h=runHarness();await assert.rejects(()=>h.commands.submitRun(h.client,creation));
    const pending=await h.commands.pending(h.client);h.mode({post:'success',change:omit});
    await assert.rejects(()=>h.commands.recoverRun(h.client,pending));
    assert.deepEqual(await h.commands.pending(h.client),pending,field);
    await assert.rejects(()=>h.commands.retryRun(h.client,pending));
    assert.deepEqual(await h.commands.pending(h.client),pending,field);
    const first=runHarness();first.mode({post:'success',change:omit});
    await assert.rejects(()=>first.commands.submitRun(first.client,creation));
    assert.equal((await first.commands.pending(first.client)).kind,'run_create',field);
    assert.equal(first.calls.filter(call=>call.method==='GET').length,0,'Malformed POST scope reached confirmation');
  }
});
