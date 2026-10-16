// Component-local tests retain their own cleanup and runtime dependencies. The
// managed entry point supplies this Docker adapter only for shared-host budgets.
import {execFileSync,spawnSync} from 'node:child_process';
import {mkdirSync,writeFileSync,chmodSync,existsSync,realpathSync} from 'node:fs';
import {join,delimiter} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {assertDevelopmentCapacity,managedBuild,waitForStudyQuiet} from './development-budget.mjs';

export function managedDockerEnvironment(environment,directory) {
  const docker = environment.TEMPO_REAL_DOCKER ?? environment.PATH.split(delimiter)
    .map(path=>join(path,'docker')).find(path=>existsSync(path));
  if(!docker) throw new Error('Docker is required for a managed PostgreSQL suite');
  const bin=join(directory,'managed-bin');mkdirSync(bin);
  const wrapper=join(bin,'docker');
  writeFileSync(wrapper,`#!${process.execPath}\nimport {runManagedDocker} from ${JSON.stringify(pathToFileURL(fileURLToPath(import.meta.url)).href)};\nprocess.exitCode=await runManagedDocker(process.argv.slice(2));\n`,{mode:0o700});chmodSync(wrapper,0o700);
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
  const inspect=argumentsList=>execFileSync(executable,argumentsList,{encoding:'utf8',maxBuffer:8*1024*1024});
  if(args[0]==='build') {
    await managedBuild(builder=>execFileSync(executable,['buildx','build','--builder',builder,'--load',...args.slice(1)],{stdio:'inherit'}),{docker:async argumentsList=>inspect(argumentsList)});
    return 0;
  }
  if(args[0]==='run') {
    await waitForStudyQuiet();
    const request=managedContainerArguments(args,token);
    assertDevelopmentCapacity(['component-test'],'new-component-container',inspect,{'component-test':request.limits});
    args=request.args;
  }
  const result=spawnSync(executable,args,{stdio:'inherit'});
  if(result.error) throw result.error;
  return result.status ?? 1;
}
