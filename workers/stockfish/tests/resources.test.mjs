import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectResources, validateResourceBudget } from '../resources.mjs';
import { resources } from './helpers.mjs';
test('whole-worker resource admission requires finite cgroup memory, CPU and zero swap',async()=>{
  assert.throws(()=>validateResourceBudget(null,{...resources,memory_enforced:false}),{code:'memory_limit_unavailable'});
  for(const changes of [{memory_limit_bytes:256*1024*1024},{memory_limit_bytes:1024*1024*1024},{cpu_limit:null},{cpu_limit:2},{swap_limit_bytes:1}]) assert.throws(()=>validateResourceBudget(null,{...resources,...changes}));
  assert.equal(validateResourceBudget({max_memory_mb:512},resources),resources);
  assert.equal((await inspectResources({platform:'darwin'})).memory_enforced,false);
  const texts={'/proc/self/cgroup':'0::/','/proc/self/mountinfo':'1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw','/sys/fs/cgroup/memory.max':String(512*1024*1024),'/sys/fs/cgroup/cpu.max':'100000 100000','/sys/fs/cgroup/memory.swap.max':'0','/sys/fs/cgroup/memory.events':'oom 0\noom_kill 0'};
  const observed=await inspectResources({platform:'linux',readText:async p=>{if(!(p in texts))throw Error(p);return texts[p];}});
  assert.equal(validateResourceBudget(null,observed).cpu_limit,1);
});
