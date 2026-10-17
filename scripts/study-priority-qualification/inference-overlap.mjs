import assert from 'node:assert/strict';
import {readFile,writeFile,rename} from 'node:fs/promises';
import {fork} from 'node:child_process';
const kind=process.argv[2],directory='/overlap',deadline=Date.now()+180000;
const fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let events=Promise.resolve();
const event=(name,extra={})=>{const payload=JSON.stringify({at:Date.now(),...extra}),path=`${directory}/${kind}-${name}.json`;return events=events.then(async()=>{await writeFile(path+'.partial',payload);await rename(path+'.partial',path);});};
async function inputSeen(){try{return JSON.parse(await readFile(directory+'/interaction.json','utf8')).started===true;}catch{return false;}}
async function allowed(){const before=performance.now();let state,error;try{const response=await fetch(process.env.TEMPO_SATURATION_CONTROL_URL,{headers:{Authorization:'Bearer '+controlCredential},signal:AbortSignal.timeout(75)});assert(response.ok);state=await response.json();}catch(failure){error=String(failure);}const now=Date.now(),value=state?.expires>now&&state.available===true&&state.active===false;const sample={at:now,read_ms:performance.now()-before,allowed:value,state,error};controlSamples.push(sample);if(!value&&traceCount++<3)void event('refused-control-'+traceCount,sample);return value;}
async function quiet(){while(!await allowed()){assert(Date.now()<deadline,'Capped inference did not resume within its bounded rehearsal');await pause(25);}}
async function noChildren(){while(true){const text=await readFile(`/proc/self/task/${process.pid}/children`,'utf8');if(!text.trim())return;assert(Date.now()<deadline,'Old engine termination remains unverified');await pause(10);}}
const controlCredential=(await readFile(directory+'/control-credential','utf8')).trim();
const controlSamples=[];
let traceCount=0;
let engine,inference,maiaChild,preInteractionDeferrals=0,generation=0;
process.once('SIGTERM',()=>{inference?.close();engine?.terminate();void writeFile(`${directory}/${kind}-control.jsonl`,controlSamples.map(value=>JSON.stringify(value)).join('\n')+'\n').finally(()=>process.exit(143));});
try{
 if(kind==='maia'){
  const {createCancellableMaia}=await import('/app/scripts/cancellable-maia.mjs');
  const create=()=>createCancellableMaia({launch:()=>{const child=fork('/app/scripts/maia-inference-child.mjs',[],{execArgv:[],stdio:['ignore','inherit','inherit','ipc']});maiaChild=child;child.on('message',m=>{if(m.started)void event('running',{running:true,generation});});return child;}});
  inference=create();
  async function analyze(){generation++;const result=await inference.analyze(fen,1500,allowed).catch(error=>error);await event('running',{running:false,generation});if(result.code==='MAIA_TERMINATION_UNVERIFIED'){
    await event('termination-unverified',{claims_suspended:!inference.terminationVerified()});
    while(maiaChild?.exitCode===null&&maiaChild?.signalCode===null){try{process.kill(maiaChild.pid,0);}catch(error){if(error.code==='ESRCH')break;throw error;}assert(Date.now()<deadline,'Quarantined Maia termination remains unverified');await pause(10);}
    inference.close();inference=create();await event('quarantine-reconciled',{old_pid:maiaChild?.pid,replacement_after_termination:true});
   }else if(result.code)assert.equal(result.code,'STUDY_PREEMPTED');else{assert.equal(result.length,20);assert(Math.abs(result.reduce((sum,move)=>sum+move.probability,0)-1)<.0001);}return result;
  }
  while(true){await quiet();const result=await analyze();if(result.code){await pause(20);if(await inputSeen()){await event('yielded',{pre_interaction_deferrals:preInteractionDeferrals,outcome:result.code});break;}preInteractionDeferrals++;}}
  while(true){await quiet();const result=await analyze();if(!result.code){await event('resumed',{legal_moves:20,rating:1500});break;}}
 }else{
  const {createStockfishProcess}=await import('/app/scripts/stockfish-process.mjs');const {createEngineSearch}=await import('/app/scripts/engine-search.mjs');
  async function start(){engine=await createStockfishProcess();engine.uci('uci');engine.uci('setoption name Threads value 1');engine.uci('setoption name Hash value 32');engine.uci('isready');}
  await start();
  async function search(depth){generation++;const searches=createEngineSearch(engine,async()=>({active:!await allowed(),available:true,study_only_mode:false}));const pending=searches.evaluate({request:{position_start_fen:fen,position_prefix_uci:[],depth,multipv:1}},'engine_game');const listen=engine.listen;let published=false;engine.listen=text=>{listen(text);if(!published&&text.startsWith('info ')&&text.includes(' pv ')){published=true;void event('running',{running:true,generation});}};
   const result=await pending.catch(error=>error);await event('running',{running:false,generation});if(result instanceof Error)assert.equal(result.diagnostics?.outcome,'preempted');if(searches.fatalEngineError){engine.terminate();await noChildren();await start();}return result;
  }
  while(true){await quiet();const result=await search(99);assert(result instanceof Error);await pause(20);if(await inputSeen()){await event('yielded',{pre_interaction_deferrals:preInteractionDeferrals});break;}preInteractionDeferrals++;}
  while(true){await quiet();const result=await search(4);if(!(result instanceof Error)){assert(result.report.complete);await event('resumed',{depth:4});break;}}
 }
}finally{inference?.close();engine?.terminate();await writeFile(`${directory}/${kind}-control.jsonl`,controlSamples.map(value=>JSON.stringify(value)).join('\n')+'\n');}
