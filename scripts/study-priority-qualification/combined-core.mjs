import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {readFile,writeFile,mkdir,rm,open} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createServer as portServer} from 'node:net';
const root=process.cwd(), evidence=fileURLToPath(new URL('.',import.meta.url));
const {managedBuild,recordBudgetResource,assertDevelopmentCapacity,waitForStudyQuiet}=await import(pathToFileURL(join(root,'scripts/development-budget.mjs')));
const {readHostStudyActivity}=await import(pathToFileURL(join(root,'scripts/host-study-activity.mjs')));
const {createServer}=await import(pathToFileURL(join(root,'node_modules/vite/dist/node/index.js')));
const {chromium}=await import(pathToFileURL(join(root,'node_modules/playwright/index.mjs')));
const owner='tempo-saturation-'+randomUUID(), images=[], containers=[];
const runDirectory=join(evidence,'combined-'+owner);await mkdir(runDirectory,{mode:0o700});
const report={owner,revision:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),complete:false,cleanup:false,host_cleanup_errors:[],cohorts:[],limits:{docker_cpus:2,docker_memory_mib:2048,host:'owned host API, Celery, Vite, Learning and Chromium processes inherit nice/thread settings; RSS is observed, not kernel memory capped on macOS'},missing_overlap:['The full 2 GiB builder cannot coexist with PostgreSQL and inference within the aggregate Docker allocation; its real build follows verified cleanup.','Each cohort includes one real Study UI commit/confirmed prepared successor plus 59 native HTTP commits; independent Learning durable writes remain qualified by the separate 42-case owner suite.']};
const docker=args=>execFileSync('docker',args,{encoding:'utf8',maxBuffer:16*1024*1024});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function json(path){try{return JSON.parse(await readFile(path,'utf8'));}catch(error){if(error.code==='ENOENT')return undefined;throw error;}}
async function publish(path,value){const temp=path+'.partial';await writeFile(temp,JSON.stringify(value));await import('node:fs/promises').then(fs=>fs.rename(temp,path));}
async function waitFile(path,seconds=180){const deadline=Date.now()+seconds*1000;while(Date.now()<deadline){const value=await json(path);if(value)return value;await pause(10);}throw new Error('Timed out: '+path);}
async function freePort(){return new Promise(resolve=>{const server=portServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});}
async function ownedChild(command,args,environment,log){const output=await open(log,'w');const child=spawn(command,args,{cwd:root,env:{...process.env,...environment},stdio:['ignore',output.fd,output.fd]});await output.close();child.completion=new Promise(resolve=>{child.once('error',error=>resolve({error:String(error)}));child.once('exit',(code,signal)=>resolve({code,signal}));});return child;}
async function stop(child){if(!child)return;child.kill('SIGCONT');child.kill('SIGTERM');const outcome=await Promise.race([child.completion,pause(30000).then(()=>undefined)]);if(!outcome){child.kill('SIGKILL');await child.completion;}}
async function absent(kind,name){try{docker([kind,'inspect',name]);return false;}catch(error){if(/No such|no such/.test(String(error.stderr)))return true;throw error;}}
await recordBudgetResource('saturation:'+owner,true);
try{
 await managedBuild(async builder=>{for(const [kind,file]of[['stockfish','Dockerfile.engine'],['maia','Dockerfile.maia']]){const image=owner+'-'+kind+':proof';images.push(image);docker(['buildx','build','--builder',builder,'--load','--label','tempo.owner='+owner,'-f',file,'-t',image,'.']);}});
 for(let cohort=0;cohort<Number(process.env.TEMPO_SATURATION_COHORTS??1);cohort++){
  const directory=join(runDirectory,'saturation-'+cohort);await mkdir(directory,{mode:0o700});
  const hostDirectory=join(directory,'host');await mkdir(hostDirectory,{mode:0o700});
  process.env.TEMPO_STUDY_ACTIVITY_DIRECTORY=hostDirectory;
  process.env.TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES=JSON.stringify(["saturation-control"]);
  assert(!(await readHostStudyActivity({directory:hostDirectory})).available);
  const row={cohort,complete:false,docker:[],input_ms:[],local_yield_ms:[],control_samples:[],host_samples:[],children:[]};report.cohorts.push(row);await publish(join(directory,'coordinator-in-progress.json'),{owner,coordinator_pid:process.pid,revision:report.revision,row});
  let native,learning,publisher,controlServer,host,browser,page,timer,polling=false,localActive=false;
  const inferenceChildren=[];
  try{
   native=await ownedChild(join(root,'backend/.venv/bin/python'),[join(evidence,'native-overlap.py'),'--evidence',directory],{TEMPO_SATURATION_DIRECTORY:directory},join(directory,'native.log'));row.children.push({kind:'native',pid:native.pid});await publish(join(directory,'coordinator-in-progress.json'),{owner,coordinator_pid:process.pid,revision:report.revision,row});
   const ready=await Promise.race([waitFile(join(directory,'native-ready.json')),native.completion.then(result=>{throw new Error('Native assembly exited before readiness: '+JSON.stringify(result));})]);
   row.native_resources=ready.resources;
   process.env.TEMPO_STUDY_ACTIVITY_URL=ready.origin+'/api/system/foreground-active';
   process.env.TEMPO_TARGET='local';process.env.TEMPO_PROXY_API=ready.origin;
   let routeActivityOrigin,routeCredential;
   host=await createServer({configFile:join(root,'vite.static.config.ts'),plugins:[{name:'owned-saturation-control',configureServer(server){server.middlewares.use(async(req,res,next)=>{
    if(req.url.startsWith('/__independent/activity')){try{const upstream=await fetch(routeActivityOrigin+'/api/study/activity'+new URL(req.url,'http://localhost').search,{method:'POST',headers:{Authorization:'Bearer '+routeCredential,Origin:'http://127.0.0.1:'+server.httpServer.address().port},signal:AbortSignal.timeout(500)});res.statusCode=upstream.status;res.end(await upstream.text());}catch{res.statusCode=503;res.end('{}');}return;}
    if(req.url.startsWith('/api/queue/window')&&req.url.includes('saturation_delay=1')){try{await pause(3600);const response=await fetch(ready.origin+'/api/queue/window?limit=2',{signal:AbortSignal.timeout(5000)});res.statusCode=response.status;res.setHeader('Content-Type','application/json');res.end(await response.text());}catch(error){res.statusCode=502;res.end(JSON.stringify({error:String(error)}));}return;}
    next();
   });}}],server:{host:'127.0.0.1',port:0}});await host.listen();
   const origin='http://127.0.0.1:'+host.httpServer.address().port;
   const activityPort=await freePort(),activityOrigin='http://127.0.0.1:'+activityPort;
   const credential=randomUUID()+randomUUID(),credentialFile=join(directory,'credential');await writeFile(credentialFile,credential,{mode:0o600});
   learning=await ownedChild(join(root,'services/learning/runtime/.venv/bin/python'),['-m','uvicorn','activity_app:app','--app-dir',evidence,'--host','127.0.0.1','--port',String(activityPort),'--no-access-log'],{TEMPO_SATURATION_TOKEN_FILE:credentialFile,TEMPO_SATURATION_ORIGIN:origin,TEMPO_STUDY_ACTIVITY_DIRECTORY:hostDirectory},join(directory,'learning.log'));row.children.push({kind:'learning',pid:learning.pid});
   const deadline=Date.now()+15000;while(true){try{if((await fetch(activityOrigin+'/health/live')).ok)break;}catch{}assert(Date.now()<deadline,'Independent control did not start');await pause(50);}
   routeActivityOrigin=activityOrigin;routeCredential=credential;
   browser=await chromium.launch();page=await browser.newPage();await page.context().tracing.start({screenshots:true,snapshots:true});
   await page.addInitScript(()=>{const state={events:[],input:[],started:undefined,optionalRunning:false,stopRepeating:false,usefulBulkCompletions:0,prepared:[],confirmed:undefined,entry:undefined};window.saturation=state;const Native=Worker;window.Worker=class extends Native{constructor(url,options){super(url,options);this.priorityName=options?.name;if(options?.name==='tempo-optional-preparation'){state.events.push({kind:'created',at:performance.now()});this.addEventListener('message',({data})=>{if(data.state==='running'){state.optionalRunning=true;state.events.push({kind:'running',at:performance.now()});}});}}terminate(){if(this.priorityName==='tempo-optional-preparation')state.optionalRunning=false;state.events.push({kind:'terminated',worker_name:this.priorityName??'essential',input_at:state.lastInput,at:performance.now()});super.terminate();}};window.addEventListener('keydown',()=>{state.lastInput=performance.now();},true);window.addEventListener('pointerdown',event=>{state.lastInput=performance.now();state.stopRepeating=true;if(event.target.closest('button[aria-label="Flip board"]'))state.started=performance.now();},true);const capture=value=>{if(value&&typeof value==='object'&&(value.persisted===true||value.state==='complete'&&value.response?.persisted===true))state.confirmed=performance.now();};const originalText=Response.prototype.text,originalJSON=Response.prototype.json;Response.prototype.text=async function(){const text=await originalText.call(this);try{capture(JSON.parse(text));}catch{}return text;};Response.prototype.json=async function(){const value=await originalJSON.call(this);capture(value);return value;};new MutationObserver(()=>{const entry=document.querySelector('#train')?.getAttribute('data-queue-entry-id');if(!entry)return;if(state.entry===undefined){state.entry=entry;return;}if(state.entry!==entry&&state.confirmed!==undefined){state.entry=entry;const start=state.confirmed;state.confirmed=undefined;requestAnimationFrame(()=>state.prepared.push(performance.now()-start));}}).observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['data-queue-entry-id']});new MutationObserver(records=>{if(state.started!==undefined&&records.some(record=>record.attributeName==='data-orientation')){const started=state.started;state.started=undefined;requestAnimationFrame(()=>state.input.push(performance.now()-started));}}).observe(document,{subtree:true,attributes:true,attributeFilter:['data-orientation']});});
   await page.addInitScript(()=>localStorage.setItem('tempo-notifications-v1',JSON.stringify(Array.from({length:500},(_,index)=>({id:'history-'+index,key:'history-'+index,occurredAt:'2026-09-17T12:00:00Z',updatedAt:'2026-09-17T12:00:00Z',occurrenceCount:1,resolvedAt:'2026-09-17T13:00:00Z',clearedAt:null,active:false,severity:'info',source:'synthetic history',message:'Earlier recovery status',details:{diagnostic:'x'.repeat(900)}})))));
   await page.goto(origin);const flip=page.getByRole('button',{name:'Flip board',exact:true});await flip.waitFor({timeout:30000});await flip.hover();
   const priorityURL='/@fs'+join(root,'app/lib/study-priority.ts'),backgroundURL='/@fs'+join(root,'app/lib/background-study.ts');
   await page.evaluate(async url=>{const priority=await import(url);window.saturation.priority=priority;priority.installStudyInteractionPriority((session,remaining)=>fetch('/__independent/activity?session_id='+session+'&expires_in_ms='+remaining,{method:'POST'}).then(response=>{if(!response.ok)throw new Error('Independent activity unavailable');}));},priorityURL);
   const controlPath=join(directory,'control.json');
   const overridePath=join(directory,'local-priority.json');
   async function poll(){if(polling)return;polling=true;try{await publish(overridePath,{active:localActive,expires:Date.now()+500});}finally{polling=false;}}
   await poll();
   publisher=await ownedChild('node',[join(evidence,'publish-control.mjs'),ready.origin,hostDirectory,controlPath,overridePath],{},join(directory,'control-publisher.log'));row.children.push({kind:'control-publisher',pid:publisher.pid});
   await waitFile(controlPath);timer=setInterval(()=>void poll(),100);
   const controlPort=await freePort(),controlCredentialFile=join(directory,'control-credential'),controlCredential=randomUUID()+randomUUID();
   await writeFile(controlCredentialFile,controlCredential,{mode:0o600});
   controlServer=await ownedChild('node',[join(evidence,'serve-control.mjs'),controlPath,String(controlPort),controlCredentialFile],{},join(directory,'control-server.log'));row.children.push({kind:'control-server',pid:controlServer.pid});await publish(join(directory,'coordinator-in-progress.json'),{owner,coordinator_pid:process.pid,revision:report.revision,row});
   const controlDeadline=Date.now()+15000;while(true){try{if((await fetch('http://127.0.0.1:'+controlPort+'/control',{headers:{Authorization:'Bearer '+controlCredential},signal:AbortSignal.timeout(500)})).ok)break;}catch{}assert(Date.now()<controlDeadline,'Owned HTTP control bridge did not become ready');await pause(25);}

   const pidSnapshot=execFileSync('ps',['-axo','pid=,ppid=,rss='],{encoding:'utf8'}).trim().split('\n').map(line=>line.trim().split(/\s+/).map(Number));
   row.host_samples.push({at:Date.now(),processes:pidSnapshot.filter(([pid,parent])=>pid===process.pid||parent===process.pid||pid===native.pid||parent===native.pid)});
   await page.evaluate(async url=>{const {runStudyTask}=await import(url);const fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';const lines=Array.from({length:12000},(_,i)=>({id:'overlap-'+i,repertoireId:'overlap',repertoireName:'Overlap',title:'Line '+i,side:'white',startingFen:fen,moves:['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6']}));async function prepare(){await runStudyTask({kind:'index',lines});window.saturation.usefulBulkCompletions++;if(!window.saturation.stopRepeating)return prepare();window.saturation.settled='complete';}window.saturation.bulk=prepare().catch(error=>window.saturation.settled=error.name);},backgroundURL);
   await page.waitForFunction(()=>window.saturation.events.some(event=>event.kind==='running'));
   let quietSince,quietDeadline=Date.now()+30000;
   while(true){const state=await json(controlPath);if(state?.available&&!state.active){quietSince??=Date.now();if(Date.now()-quietSince>=3200)break;}else quietSince=undefined;assert(Date.now()<quietDeadline,'Control did not establish a continuous genuine quiet period before inference');await pause(50);}
   row.before_inference_quiet_ms=Date.now()-quietSince;
   for(const [kind,index,cpus,memory]of[['stockfish',0,.25,384],['maia',1,.3,1024]]){
    const name=owner+'-'+cohort+'-'+kind;containers.push(name);
    assertDevelopmentCapacity([kind],owner,docker,{[kind]:{cpus,memoryMiB:memory}});
    docker(['create','--env','TEMPO_SATURATION_CONTROL_URL=http://host.docker.internal:'+controlPort+'/control','--name',name,'--label','tempo.owner='+owner,'--label','tempo.resource-class=development','--cpus',String(cpus),'--memory',memory+'m','--mount','type=bind,source='+directory+',target=/overlap','--mount','type=bind,source='+join(evidence,'inference-overlap.mjs')+',target=/app/overlap.mjs,readonly','--entrypoint','node',images[index],'/app/overlap.mjs',kind]);
    const details=JSON.parse(docker(['inspect',name]))[0];assert.equal(details.HostConfig.Memory,memory*1024**2);row.docker.push({name,cpus,memoryMiB:memory});
    inferenceChildren.push(await ownedChild('docker',['start','--attach',name],{},join(directory,kind+'.log')));
   }
   const runningDeadline=Date.now()+90000;
   while(true){const values=await Promise.all(['stockfish','maia'].map(kind=>json(join(directory,kind+'-running.json'))));if(values.every(value=>value?.running===true))break;assert(Date.now()<runningDeadline,'Both real capped engines did not establish simultaneous useful execution');await pause(10);}
   await page.waitForFunction(()=>window.saturation.optionalRunning);
   const runningState=await page.evaluate(()=>window.saturation.events);const baselineTerminations=runningState.filter(event=>event.kind==='terminated'&&event.worker_name==='tempo-optional-preparation').length;
   const start=await page.evaluate(()=>performance.now());await page.mouse.down();row.interaction_wall_ms=await page.evaluate(()=>performance.timeOrigin+window.saturation.lastInput);await publish(join(directory,'interaction.json'),{started:true,at:row.interaction_wall_ms});localActive=true;await poll();
   await page.waitForFunction(count=>window.saturation.events.filter(event=>event.kind==='terminated'&&event.worker_name==='tempo-optional-preparation').length>count,baselineTerminations);
   const events=await page.evaluate(()=>window.saturation.events);const localYield=events.filter(event=>event.kind==='terminated'&&event.worker_name==='tempo-optional-preparation').at(-1);row.cancellation_driver_observation_ms=localYield.at-start;row.local_yield_ms.push(localYield.at-localYield.input_at);assert(row.local_yield_ms[0]>=0&&row.local_yield_ms[0]<=50);
   await Promise.all(['stockfish','maia'].map(async kind=>{const yielded=await waitFile(join(directory,kind+'-yielded.json'));row[kind+'_yield_ms']=Date.now()-row.interaction_wall_ms;row[kind+'_container_wall_time']=yielded.at;assert(row[kind+'_yield_ms']>=0&&row[kind+'_yield_ms']<=250);}));
   const abort=new AbortController();let admitted=false;const denied=managedBuild(async()=>{admitted=true;},{quiet:()=>waitForStudyQuiet({signal:abort.signal})}).catch(error=>String(error));await pause(750);abort.abort(new Error('Bounded active-Study admission probe'));row.build_admission_reason=await denied;assert(String(row.build_admission_reason).includes('Bounded active-Study'));assert(!admitted);row.active_build_deferred=true;
   await pause(3000);assert(await page.evaluate(()=>window.saturation.priority.studyInteractionActive()));row.stationary_contact_held=true;
   await page.mouse.up();localActive=false;await poll();
   await publish(join(directory,'browser-ready.json'),{input_started_at:row.interaction_wall_ms});
   const planned=await waitFile(join(directory,'ui-review-request.json'));
   assert.equal(await page.locator('#train').getAttribute('data-queue-entry-id'),String(planned.body.queue_entry_id));
   const confirmed=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname===planned.path);
   await page.getByRole('button',{name:'Correct',exact:true}).click();
   const confirmation=await confirmed;
   const saved=await confirmation.json();assert(saved.persisted);
   await page.waitForFunction(entry=>document.querySelector('#train')?.getAttribute('data-queue-entry-id')===String(entry),planned.body.prepared_next.queue_entry_id);
   await page.waitForFunction(()=>window.saturation.prepared.length===1);
   row.prepared_next_ms=await page.evaluate(()=>window.saturation.prepared);assert(row.prepared_next_ms[0]<=250);
   const requestHeaders=await confirmation.request().allHeaders();
   await publish(join(directory,'ui-review-response.json'),{status:confirmation.status(),response:saved,request_body:confirmation.request().postDataJSON(),request_identity:requestHeaders['idempotency-key'],response_headers:await confirmation.allHeaders()});
   // Every click changes the real Study board orientation; frame timings are
   // presentation proxies, not physical display or server-confirmation timings.
   for(let i=0;i<100;i++){await flip.click();await page.waitForFunction(count=>window.saturation.input.length>=count,i+2);}
   row.input_all_ms=await page.evaluate(()=>window.saturation.input);row.input_ms=row.input_all_ms.slice(-100);assert.equal(row.input_ms.length,100);assert([...row.input_ms].sort((a,b)=>a-b)[94]<=100);
   await page.evaluate(async url=>{const {learningFetch}=await import(url);window.saturation.delayed=learningFetch('/api/queue/window?limit=2&saturation_delay=1').then(response=>{if(!response.ok)throw new Error('Delayed essential request failed: '+response.status);window.saturation.delayedComplete=true;}).catch(error=>window.saturation.delayedError=String(error));},'/@fs'+join(root,'app/lib/study-connection.ts'));
   await pause(3200);assert(await page.evaluate(()=>window.saturation.priority.studyInteractionActive()));row.delayed_essential_held=true;
   await page.waitForFunction(()=>window.saturation.delayedComplete||window.saturation.delayedError);assert.equal(await page.evaluate(()=>window.saturation.delayedError),undefined);await pause(3200);localActive=false;await poll();await publish(join(directory,'ui-inputs-complete.json'),{at:Date.now()});
   await page.waitForFunction(()=>window.saturation.settled==='complete',undefined,{timeout:60000});
   for(const kind of ['stockfish','maia'])await waitFile(join(directory,kind+'-resumed.json'));
   await waitFile(join(directory,'native-reconciled.json'));
   row.repeated_cancellation_ms=[];
   for(let repeat=0;repeat<Number(process.env.TEMPO_SATURATION_CANCELLATIONS??3);repeat++){
    await page.evaluate(async url=>{const {runStudyTask}=await import(url);window.saturation.controller=new AbortController();const fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';const lines=Array.from({length:12000},(_,index)=>({id:'repeat-'+index,repertoireId:'overlap',repertoireName:'Overlap',title:'Repeated line',side:'white',startingFen:fen,moves:['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6']}));window.saturation.repeatedOutcome='pending';window.saturation.repeated=runStudyTask({kind:'index',lines},window.saturation.controller.signal).then(()=>window.saturation.repeatedOutcome='complete',error=>window.saturation.repeatedOutcome=error.name);},backgroundURL);
    await page.waitForFunction(()=>window.saturation.optionalRunning);
    const count=await page.evaluate(()=>window.saturation.events.filter(event=>event.kind==='terminated'&&event.worker_name==='tempo-optional-preparation').length);
    await page.keyboard.press('Escape');
    await page.waitForFunction(count=>window.saturation.events.filter(event=>event.kind==='terminated'&&event.worker_name==='tempo-optional-preparation').length>count,count);
    const elapsed=await page.evaluate(()=>(()=>{const event=window.saturation.events.filter(event=>event.kind==='terminated'&&event.worker_name==='tempo-optional-preparation').at(-1);return event.at-event.input_at;})());assert(elapsed>=0&&elapsed<=50);row.repeated_cancellation_ms.push(elapsed);
    await page.evaluate(()=>window.saturation.controller.abort());await page.waitForFunction(()=>window.saturation.repeatedOutcome==='AbortError');await pause(3200);
   }

   for(let i=0;i<100;i++){const begin=performance.now();const response=await fetch(process.env.TEMPO_STUDY_ACTIVITY_URL,{headers:{'X-Tempo-Work-Class':'background'},signal:AbortSignal.timeout(200)});assert(response.ok);row.control_samples.push(performance.now()-begin);}
   await waitFile(join(directory,'native-reconciled.json'));
   const headers={Authorization:'Bearer '+credential,Origin:origin};
   await fetch(activityOrigin+'/api/study/activity?session_id=short&expires_in_ms=1',{method:'POST',headers});await fetch(activityOrigin+'/api/study/activity?session_id=long',{method:'POST',headers});await pause(50);assert((await readHostStudyActivity({directory:hostDirectory})).active);row.independent_sessions=true;
   learning.kill('SIGSTOP');await pause(16000);const unavailable=await readHostStudyActivity({directory:hostDirectory});assert(!unavailable.available);row.stale_expected_control_defers=true;learning.kill('SIGCONT');await pause(5500);
   assert((await readHostStudyActivity({directory:hostDirectory})).available);
   await publish(join(directory,'browser-done.json'),{complete:true});
   const nativeResult=await native.completion;assert.equal(nativeResult.code,0);row.browser_workload={lines:12000,moves_per_line:8,retained_synthetic_notices:500,useful_completions:await page.evaluate(()=>window.saturation.usefulBulkCompletions)};row.native=await json(join(directory,'native.json'));row.reconciliation=await json(join(directory,'native-overlap.json'));row.complete=true;
  }finally{
   clearInterval(timer);await publish(join(directory,'abort.json'),{aborted:!row.complete});
   if(page){try{if(!row.complete)await page.screenshot({path:join(directory,'failure.png')});await page.context().tracing.stop({path:join(directory,'browser-trace.zip')});}catch(error){report.host_cleanup_errors.push(String(error));}}
   const cleanup=await Promise.allSettled([browser?.close(),host?.close(),stop(learning),stop(native),stop(publisher),stop(controlServer),...inferenceChildren.map(stop)]);
   row.host_cleanup_errors=cleanup.filter(item=>item.status==='rejected').map(item=>String(item.reason));
   const nativeReceipt=await json(join(directory,'native.json'));
   if(nativeReceipt?.cleanup!==true)row.host_cleanup_errors.push('Native resource absence is unverified; retain its rehearsal journal');
   for(const resource of row.native_resources??[]){
    if(!await absent('container',resource.name))row.host_cleanup_errors.push('Native container remains: '+resource.name);
    for(const volume of resource.volumes)if(!await absent('volume',volume))row.host_cleanup_errors.push('Native volume remains: '+volume);
   }
   row.native_absence_verified=!row.host_cleanup_errors.length;
   report.host_cleanup_errors.push(...row.host_cleanup_errors);
   for(const name of row.docker.map(value=>value.name)){if(!await absent('container',name)){const details=JSON.parse(docker(['inspect',name]))[0];assert.equal(details.Config.Labels['tempo.owner'],owner);docker(['rm','--force','--volumes',name]);}assert(await absent('container',name));}
   await rm(join(directory,'credential'),{force:true});await rm(join(directory,'control-credential'),{force:true});
   await writeFile(join(directory,'coordinator.json'),JSON.stringify(row,null,2));
  }
 }
 delete process.env.TEMPO_STUDY_ACTIVITY_URL;delete process.env.TEMPO_STUDY_ACTIVITY_DIRECTORY;delete process.env.TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES;
 await managedBuild(async builder=>{const directory=join(runDirectory,'saturation-build');await mkdir(directory);await writeFile(join(directory,'Dockerfile'),'FROM alpine:3.22\nRUN cat /proc/self/cgroup\n');report.quiet_build=docker(['buildx','build','--builder',builder,'--progress=plain','--no-cache',directory]);});
 report.complete=report.cohorts.every(row=>row.complete);
}finally{
 const errors=[];
 for(const name of containers)try{if(!await absent('container',name)){assert.equal(JSON.parse(docker(['inspect',name]))[0].Config.Labels['tempo.owner'],owner);docker(['rm','--force','--volumes',name]);}assert(await absent('container',name));}catch(error){errors.push(String(error));}
 for(const name of images)try{if(!await absent('image',name)){assert.equal(JSON.parse(docker(['image','inspect',name]))[0].Config.Labels['tempo.owner'],owner);docker(['image','rm',name]);}assert(await absent('image',name));}catch(error){errors.push(String(error));}
 report.cleanup=!errors.length&&!report.host_cleanup_errors.length;report.cleanup_errors=errors;
 if(report.cleanup)await recordBudgetResource('saturation:'+owner,false);
 await writeFile(join(evidence,'combined-saturation.json'),JSON.stringify(report,null,2));
 await writeFile(join(runDirectory,'summary.json'),JSON.stringify(report,null,2));
 if(errors.length)throw new AggregateError(errors,'Retain saturation resource ownership');
}
