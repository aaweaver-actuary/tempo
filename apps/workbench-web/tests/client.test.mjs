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
