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
