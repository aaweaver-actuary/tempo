// Freeze this task's coordinator inputs after admission and before any effects.
import {readFile,writeFile,mkdir,lstat} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join,isAbsolute} from 'node:path';
import {runWithDevelopmentBudget,validateDevelopmentBudgetOwner} from '../development-budget.mjs';
const argumentsList=process.argv.slice(2);
if(argumentsList.length===1&&argumentsList[0]==='--list'){
 console.log(JSON.stringify({scenarios:['useful optional progress','held input and pending essential work','real review and immutable replay','prepared-next confirmation','outage and independent sessions','active build deferral and capped quiet build','durable reconciliation'],scope:'Private resources; finite measurements; no live deployment'}));process.exit(0);
}
if(argumentsList.length!==2||argumentsList[0]!=='--evidence'||!isAbsolute(argumentsList[1]))throw new Error('Supply --evidence with an absolute private evidence directory');
if(!process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)process.exit(await runWithDevelopmentBudget(process.execPath,[fileURLToPath(import.meta.url),...argumentsList]));
await validateDevelopmentBudgetOwner();
const source=fileURLToPath(new URL('.',import.meta.url)),output=argumentsList[1],snapshot=join(output,'coordinator-inputs-'+randomUUID());
await mkdir(output,{recursive:true,mode:0o700});
const metadata=await lstat(output);
if(!metadata.isDirectory()||metadata.isSymbolicLink()||(metadata.mode&0o077))throw new Error('Qualification evidence must be an owned private directory');
await mkdir(snapshot,{mode:0o700});
const runtimeCode="import hashlib,json,pathlib,tempo_learning_runtime;root=pathlib.Path(tempo_learning_runtime.__file__).parent;print(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest()for name in ['api.py','config.py','host_activity.py']}))";
const installedRuntime=JSON.parse(execFileSync(join(process.cwd(),'services/learning/runtime/.venv/bin/python'),['-I','-c',runtimeCode],{encoding:'utf8'}));
for(const[name,digest]of Object.entries(installedRuntime)){const source=await readFile(join(process.cwd(),'services/learning/runtime/src/tempo_learning_runtime',name));if(createHash('sha256').update(source).digest('hex')!==digest)throw new Error('Reinstall the final noneditable Learning runtime before saturation: '+name);}

const manifest={entry_sha256:createHash('sha256').update(await readFile(fileURLToPath(import.meta.url))).digest('hex'),revision:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),files:{},installed_runtime_sha256:installedRuntime,snapshot,parameters:{cohorts:Number(process.env.TEMPO_SATURATION_COHORTS??1),repeated_cancellations_per_cohort:Number(process.env.TEMPO_SATURATION_CANCELLATIONS??3)}};
for(const [from,to]of [['combined-core.mjs','combined.mjs'],['native-overlap.py','native-overlap.py'],['inference-overlap.mjs','inference-overlap.mjs'],['inference-yield.mjs','inference-yield.mjs'],['activity_app.py','activity_app.py'],['publish-control.mjs','publish-control.mjs'],['serve-control.mjs','serve-control.mjs'],['process-lifecycle.mjs','process-lifecycle.mjs']]){
 const content=await readFile(join(source,from));await writeFile(join(snapshot,to),content,{mode:0o600});manifest.files[to]=createHash('sha256').update(content).digest('hex');
}
await writeFile(join(snapshot,'inputs.json'),JSON.stringify(manifest,null,2));
await writeFile(join(output,'combined-saturation.json'),JSON.stringify({complete:false,pending_snapshot:snapshot}));
try{await import(pathToFileURL(join(snapshot,'combined.mjs')));}
finally{
 try{const report=JSON.parse(await readFile(join(snapshot,'combined-saturation.json'),'utf8'));report.input_snapshot=manifest;await writeFile(join(output,'combined-saturation.json'),JSON.stringify(report,null,2));}
 catch(error){await writeFile(join(output,'combined-saturation.json'),JSON.stringify({complete:false,input_snapshot:manifest,unverified:String(error)},null,2));}
}
