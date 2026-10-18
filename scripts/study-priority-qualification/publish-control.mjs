import {readFile,writeFile,rename,appendFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const [origin,directory,destination,override]=process.argv.slice(2);
const {readHostStudyActivity}=await import(pathToFileURL(join(process.cwd(),'scripts/host-study-activity.mjs')));
let stopped=false;for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{stopped=true;});
while(!stopped){
 let legacy={active:true,available:false},local={active:false,expires:0};
 try{legacy=await(await fetch(origin+'/api/system/foreground-active',{headers:{'X-Tempo-Work-Class':'background'},signal:AbortSignal.timeout(200)})).json();}catch{}
 try{local=JSON.parse(await readFile(override,'utf8'));}catch{}
 const independent=await readHostStudyActivity({directory});
 const snapshot={legacy,independent,local,active:legacy.active||independent.active||local.active&&local.expires>Date.now(),available:legacy.available===true&&independent.available,expires:Date.now()+500};
 const temporary=destination+'.'+process.pid+'.partial';await writeFile(temporary,JSON.stringify(snapshot));await rename(temporary,destination);await appendFile(destination+'.samples.jsonl',JSON.stringify({at:Date.now(),...snapshot})+'\n');
 await new Promise(resolve=>setTimeout(resolve,25));
}
