import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';import {randomUUID} from 'node:crypto';import {writeFile} from 'node:fs/promises';
import {managedBuild,recordBudgetResource,resourceBudgets,runWithDevelopmentBudget} from './development-budget.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
if(process.argv.includes('--list')){console.log(JSON.stringify(['capped_stockfish_hard_stop_and_full_depth_resume','capped_maia_preemption_and_full_legal_distribution']));process.exit(0);}
if(!process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN){process.exit(await runWithDevelopmentBudget(process.execPath,process.argv.slice(1)));}
const evidence=resolve(process.argv[2]??resolve(root,'test-results/inference-priority-images.json'));
const run=promisify(execFile);const owner='tempo-priority-inference-'+randomUUID();const images=[];const containers=[];const results=[];let complete=false;
const docker=async args=>(await run('docker',args,{cwd:root,maxBuffer:8*1024*1024})).stdout;
async function inspectOwned(kind, identity) {
 try { return JSON.parse(await docker([kind,'inspect',identity]))[0]; }
 catch(error) { if(/No such (?:object|container|image)/i.test(error.stderr??''))return undefined; throw error; }
}
try{
 await managedBuild(async builder=>{for(const [kind,file]of[['stockfish','Dockerfile.engine'],['maia','Dockerfile.maia']]){
  const tag=`${owner}-${kind}:proof`;images.push(tag);const built=await run('docker',['buildx','build','--builder',builder,'--load','--label','tempo.owner='+owner,'-f',file,'-t',tag,'.'],{cwd:root,maxBuffer:8*1024*1024});console.log(built.stderr.slice(-1600));
 }});
 // Deliberately constrained preemption fixtures, independent of ordinary service defaults.
 for(const [index,kind,service]of[[0,'stockfish','defense-engine'],[1,'maia','maia-worker']]){
  const name=owner+'-'+kind;const budget={...resourceBudgets.optionalServices[service],cpus:kind==='stockfish'?0.25:0.3};containers.push(name);await recordBudgetResource('inference:'+name,true);
  await docker(['create','--name',name,'--label','tempo.owner='+owner,'--label','tempo.resource-class=development','--cpus',String(budget.cpus),'--memory',budget.memoryMiB+'m','--mount','type=bind,source='+resolve(root,'tests/fixtures/study-priority/inference-image.mjs')+',target=/app/probe.mjs,readonly','--entrypoint','node',images[index],'/app/probe.mjs',kind]);
  const [details]=JSON.parse(await docker(['inspect',name]));if(details.HostConfig.Memory!==budget.memoryMiB*1024**2||details.HostConfig.NanoCpus!==budget.cpus*1e9)throw new Error('Kernel cap mismatch');
  const result=await run('docker',['start','--attach',name],{maxBuffer:8*1024*1024});console.log(result.stdout);console.error(result.stderr);
  const [finished]=JSON.parse(await docker(['inspect',name]));if(finished.State.ExitCode!==0||finished.State.OOMKilled)throw new Error('Inference image proof failed');
  results.push({kind,budget,output:result.stdout});
 }
 complete=results.length===2;
}finally{
 const cleanupErrors=[];
 for(const name of containers)try{
  const details=await inspectOwned('container',name);
  if(details){if(details.Config.Labels['tempo.owner']!==owner)throw new Error('Inference cleanup ownership mismatch');await docker(['rm','--force','--volumes',name]);}
  await recordBudgetResource('inference:'+name,false);
 }catch(error){cleanupErrors.push(String(error));}
 for(const image of images)try{
  const details=await inspectOwned('image',image);
  if(details){if(details.Config.Labels['tempo.owner']!==owner)throw new Error('Inference image ownership mismatch');await docker(['image','rm',image]);}
 }catch(error){cleanupErrors.push(String(error));}
 await writeFile(evidence,JSON.stringify({owner,complete,results,cleanup:cleanupErrors.length===0,cleanupErrors},null,2));
 if(cleanupErrors.length)throw new AggregateError(cleanupErrors,'Inference resource cleanup failed');
}
