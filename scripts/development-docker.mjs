// Component-local tests retain their own cleanup and runtime dependencies. The
// managed entry point supplies this Docker adapter only for shared-host budgets.
import {execFileSync,spawnSync,spawn} from 'node:child_process';
import {mkdirSync,writeFileSync,chmodSync,existsSync,realpathSync} from 'node:fs';
import {join,delimiter} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {readdir,readFile} from 'node:fs/promises';
import {assertDevelopmentCapacity,managedBuild,waitForStudyQuiet,validateDevelopmentBudgetOwner,acquireDevelopmentAdmission,recordBudgetResource} from './development-budget.mjs';

export function managedDockerEnvironment(environment,directory) {
  const docker = environment.TEMPO_REAL_DOCKER ?? environment.PATH.split(delimiter)
    .map(path=>join(path,'docker')).find(path=>existsSync(path));
  if(!docker) throw new Error('Docker is required for a managed PostgreSQL suite');
  const bin=join(directory,'managed-bin');mkdirSync(bin);
  const wrapper=join(bin,'docker');
  const capabilityNames=['TEMPO_REAL_DOCKER','TEMPO_DEVELOPMENT_BUDGET_TOKEN','TEMPO_DEVELOPMENT_BUDGET_DIRECTORY',
    'TEMPO_STUDY_ACTIVITY_URL','TEMPO_STUDY_ACTIVITY_DIRECTORY','TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES','TEMPO_STUDY_ACTIVITY_LEGACY',
    'GITHUB_ACTIONS','RUNNER_ENVIRONMENT'];
  const capability=Object.fromEntries(capabilityNames.map(name=>[name,environment[name]??null]));
  capability.TEMPO_REAL_DOCKER=realpathSync(docker);
  writeFileSync(wrapper,`#!${process.execPath}\nimport {runManagedDocker} from ${JSON.stringify(pathToFileURL(fileURLToPath(import.meta.url)).href)};\nfor(const [name,value]of Object.entries(${JSON.stringify(capability)})){if(value===null)delete process.env[name];else process.env[name]=value;}\nprocess.exitCode=await runManagedDocker(process.argv.slice(2));\n`,{mode:0o700});chmodSync(wrapper,0o700);
  return {...environment,PATH:bin+delimiter+environment.PATH,TEMPO_REAL_DOCKER:realpathSync(docker)};
}
function option(args,name) {
  const index=args.indexOf(name);
  return index>=0 ? args[index+1] : args.find(value=>value.startsWith(name+'='))?.slice(name.length+1);
}
export function managedContainerArguments(args,token) {
  if(args[0]!=='run') return args;
  const cpus=Number(option(args,'--cpus') ?? '1');
  const memory=option(args,'--memory') ?? '512m';
  const match=/^(\d+(?:\.\d+)?)([bkmgt]?)b?$/i.exec(memory);
  if(!match || !(cpus>0)) throw new Error('Managed tests require finite CPU and memory limits');
  const powers={b:1/1024**2,k:1/1024,m:1,g:1024,t:1024**2,'':1/1024**2};
  const memoryMiB=Number(match[1])*powers[match[2].toLowerCase()];
  if(!(memoryMiB>0)) throw new Error('Managed tests require a positive memory limit');
  return {args:['run','--label','tempo.resource-class=development','--label',`tempo.resource-owner=${token}`,
    ...(!option(args,'--cpus')?['--cpus',String(cpus)]:[]),...(!option(args,'--memory')?['--memory',memory]:[]),...args.slice(1)],limits:{cpus,memoryMiB}};
}
export async function runManagedDocker(args) {
  const executable=process.env.TEMPO_REAL_DOCKER;
  const token=process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
  if(!executable || !token) throw new Error('Managed Docker requires an explicit executable and budget owner');
  await validateDevelopmentBudgetOwner();
  // Expected absence is control evidence, not a worker diagnostic. Node's
  // implicit synchronous stdio forwards failing probes to the protocol stream.
  const inspect=argumentsList=>execFileSync(executable,argumentsList,{encoding:'utf8',stdio:'pipe',maxBuffer:8*1024*1024});
  if(args[0]==='build') {
    await managedBuild(builder=>execFileSync(executable,['buildx','build','--builder',builder,'--load',...args.slice(1)],{stdio:'inherit'}),{docker:async argumentsList=>inspect(argumentsList)});
    return 0;
  }
  if(args[0]==='run') {
    await waitForStudyQuiet();
    const admission=await acquireDevelopmentAdmission();
    let child,completion,settled=false,outcome,name,journaled=false,released=false;
    try {
      const directory=process.env.TEMPO_DEVELOPMENT_BUDGET_DIRECTORY??join(tmpdir(),'tempo-development-resource-budget-v1.lock');
      if(existsSync(join(directory,'builder-lane')))throw new Error('The owned builder execution lane is occupied; no container may share its allocation');
      const request=managedContainerArguments(args,token);
      assertDevelopmentCapacity(['component-test'],'new-component-container',inspect,{'component-test':request.limits});
      name=option(request.args,'--name')??'tempo-managed-'+randomUUID();
      args=option(request.args,'--name')?request.args:['run','--name',name,...request.args.slice(1)];
      await recordBudgetResource('container:'+name,true);journaled=true;
      child=spawn(executable,args,{stdio:'inherit'});
      completion=new Promise(resolve=>{
        child.once('error',error=>{settled=true;outcome={error};resolve(outcome);});
        child.once('exit',status=>{settled=true;outcome={status:status??1};resolve(outcome);});
      });
      // A sibling may check capacity only once this run is visible as running,
      // or it has finished and its absence can be verified below.
      const deadline=Date.now()+30000;
      while(!settled){
        const container=inspectManagedContainer(name,inspect,token);
        if(container?.State?.Running)break;
        if(Date.now()>=deadline)throw new Error('Managed container startup is unverified; retain its admission and resource journal');
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      await admission.release();released=true;
      const result=await completion;
      if(!inspectManagedContainer(name,inspect,token)){await recordBudgetResource('container:'+name,false);journaled=false;}
      if(result.error)throw result.error;
      return result.status;
    } finally {
      // If startup cannot be inspected, hold admission as well as ownership.
      // Other work must not assume the uncertain allocation is absent.
      if(!released&&(!child||settled)&&!journaled)await admission.release();
    }
  }
  const result=spawnSync(executable,args,{stdio:'inherit'});
  if(result.error) throw result.error;
  if(args[0]==='rm'||args[0]==='container'&&args[1]==='rm'){
    const directory=process.env.TEMPO_DEVELOPMENT_BUDGET_DIRECTORY??join(tmpdir(),'tempo-development-resource-budget-v1.lock');
    for(const filename of await readdir(join(directory,'resources'))){
      const resource=JSON.parse(await readFile(join(directory,'resources',filename),'utf8'));
      if(resource.token===token&&resource.key.startsWith('container:')){
        const name=resource.key.slice('container:'.length);
        // A failed startup can leave only its journal. A nonzero rm is then
        // expected, but only this explicitly requested identity may reconcile.
        if((result.status===0||args.includes(name))&&!inspectManagedContainer(name,inspect,token))
          await recordBudgetResource(resource.key,false);
      }
    }
  }
  return result.status ?? 1;
}

function inspectManagedContainer(name,inspect,token){
  let container;
  try{container=JSON.parse(inspect(['container','inspect',name]))[0];}
  catch(error){if(String(error.stderr??error.message).includes(name)&&/No such (?:object|container)/i.test(String(error.stderr??error.message)))return undefined;throw error;}
  if(container?.Config?.Labels?.['tempo.resource-owner']!==token)throw new Error('Managed container ownership mismatch: '+name);
  return container;
}
