// Only run after the resource coordinator grants the explicit finite window.
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {runWithDevelopmentBudget} from './development-budget.mjs';
import {managedDockerEnvironment} from './development-docker.mjs';

const args=process.argv.slice(2);
if(args[0]!=='--managed-child') {
  if(args[0]!=='--python'||!args[1]||!args.includes('--approved-window'))throw new Error('Explicit host Python and approved finite window are required');
  process.exitCode=await runWithDevelopmentBudget(process.execPath,[fileURLToPath(import.meta.url),'--managed-child',...args]);
} else {
  if(!process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)throw new Error('Managed development ownership required');
  const python=args[2], directory=await mkdtemp(join(tmpdir(),'tempo-legacy-bridge-docker-'));
  try {
    const env=managedDockerEnvironment(process.env,directory);
    env.PYTHONDONTWRITEBYTECODE='1';env.PYTEST_DISABLE_PLUGIN_AUTOLOAD='1';
    for(const key of ['PYTHONPATH','PYTHONHOME','PYTEST_ADDOPTS','PYTEST_PLUGINS'])delete env[key];
    const child=spawn(python,[fileURLToPath(new URL('./qualify_legacy_bridge.py',import.meta.url)),...args.slice(3)],{env,stdio:'inherit'});
    const mode=args[args.indexOf('--mode')+1], totalSeconds={artifacts:1200,native:900}[mode];
    if(!totalSeconds){child.kill('SIGTERM');throw new Error('Explicit finite artifact/native mode required');}
    // Last-resort watchdog. Python owns the work deadline and five-minute
    // verified cleanup phase. A forced exit leaves managed resource journals,
    // so the shared owner cannot silently release an uncertain allocation.
    const timer=setTimeout(()=>child.kill('SIGKILL'),totalSeconds*1000);
    for(const name of ['SIGINT','SIGTERM'])process.once(name,()=>child.kill(name));
    try { process.exitCode=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(code,signal)=>signal?reject(new Error('Qualification interrupted by '+signal)):resolve(code));}); }
    finally {clearTimeout(timer);}
  } finally { await rm(directory,{recursive:true,force:true}); }
}
