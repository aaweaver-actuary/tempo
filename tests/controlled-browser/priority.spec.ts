import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
type BrowserProofWindow = Window & {
  priorityProof:{events:{kind:string;at:number}[];active:()=>boolean;settled:()=>string;start:()=>void;cancel:()=>void;essential:()=>Promise<number>};
  studyLatency:{samples:number[];running:number};preparedLatency:{samples:number[]};
};
async function snapshot(page:Page) { return page.evaluate(()=>{
  const proof=(window as unknown as BrowserProofWindow).priorityProof; return {events:proof.events,active:proof.active(),settled:proof.settled()};
}); }
test('study priority cancels a running browser worker during a held drag and resumes useful work', async ({page}, testInfo)=>{
  await page.goto('/study-priority-proof.html');
  await page.waitForFunction(()=>!!(window as unknown as BrowserProofWindow).priorityProof);
  await page.evaluate(()=>(window as unknown as BrowserProofWindow).priorityProof.start());
  await expect.poll(async()=>(await snapshot(page)).events.filter((event)=>event.kind==='running').length).toBe(1);
  const before=await page.evaluate(()=>performance.now());
  await page.mouse.move(45,15); await page.mouse.down();
  await expect.poll(async()=>(await snapshot(page)).events.filter((event)=>event.kind==='terminated').length).toBe(1);
  const stopped=await snapshot(page);
  expect(stopped.events.find((event)=>event.kind==='terminated')!.at-before).toBeLessThan(50);
  await page.waitForTimeout(3500);
  expect((await snapshot(page)).active).toBe(true);
  expect((await snapshot(page)).events.filter((event)=>event.kind==='created')).toHaveLength(1);
  const essential=await page.evaluate(()=>(window as unknown as BrowserProofWindow).priorityProof.essential());
  expect(essential).toBeLessThan(250);
  await page.mouse.up();
  await expect.poll(async()=>(await snapshot(page)).events.filter((event)=>event.kind==='created').length,{timeout:6000}).toBe(2);
  await expect.poll(async()=>(await snapshot(page)).settled,{timeout:30000}).toBe('complete');
  await testInfo.attach('priority-evidence',{body:JSON.stringify({cancellation_ms:stopped.events.find(event=>event.kind==='terminated')!.at-before,essential,...await snapshot(page)}),contentType:'application/json'});
});
test('study priority repeated cancellation keeps essential queue work responsive', async({page},testInfo)=>{
  await page.goto('/study-priority-proof.html'); await page.waitForFunction(()=>!!(window as unknown as BrowserProofWindow).priorityProof);
  const samples:number[]=[];
  for(let attempt=0;attempt<3;attempt++) {
    await page.evaluate(()=>(window as unknown as BrowserProofWindow).priorityProof.start());
    await expect.poll(async()=>(await snapshot(page)).events.filter((event)=>event.kind==='running').length).toBe(attempt+1);
    await page.evaluate(()=>(window as unknown as BrowserProofWindow).priorityProof.cancel());
    await expect.poll(async()=>(await snapshot(page)).settled).toBe('AbortError');
    samples.push(await page.evaluate(()=>(window as unknown as BrowserProofWindow).priorityProof.essential()));
  }
  expect(Math.max(...samples)).toBeLessThan(250);
  await testInfo.attach('cancellation-evidence',{body:JSON.stringify({samples,...await snapshot(page)}),contentType:'application/json'});
});

test('study priority keeps native study input below the declared latency budget in idle and loaded runs',async({page},testInfo)=>{
  await page.addInitScript(()=>localStorage.setItem('tempo-notifications-v1',JSON.stringify(Array.from({length:500},(_,index)=>({
    id:'history-'+index,key:'history-'+index,occurredAt:'2026-09-17T12:00:00Z',updatedAt:'2026-09-17T12:00:00Z',occurrenceCount:1,
    resolvedAt:'2026-09-17T13:00:00Z',clearedAt:null,active:false,severity:'info',source:'synthetic history',message:'Earlier recovery status',details:{diagnostic:'x'.repeat(900)}
  })))));
  const report:Record<string,number[]>={};
  for(const lane of ['idle','loaded']) {
    const {prepareControlledUI,openingCard}=await import('./fixtures');
    const api=await prepareControlledUI(page,[openingCard('latency','Latency','e2e4',1)],false);
    if(lane==='loaded') {
      const lines=Array.from({length:12000},(_,index)=>({id:'load-'+index,repertoire_id:'load',repertoire_name:'Load',name:'Load '+index,
        trained_color:'white',start_fen:'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',moves:['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6']}));
      api.register({method:'GET',path:'/api/repertoire/lines',query:{}},route=>route.fulfill({json:{lines}}));
    }
    await page.addInitScript(()=>{
      const state={samples:[] as number[],started:undefined as number|undefined,running:0};
      Object.assign(window,{studyLatency:state});
      const NativeWorker=window.Worker;
      window.Worker=class extends NativeWorker {
        constructor(url:string|URL,options?:WorkerOptions){super(url,options);if(options?.name==='tempo-optional-preparation')this.addEventListener('message',({data})=>{if(data.state==='running')state.running++;});}
      };
      document.addEventListener('pointerdown',event=>{if((event.target as Element).closest('button[aria-label="Flip board"]'))state.started=performance.now();},true);
      new MutationObserver(records=>{if(state.started!==undefined && records.some(record=>record.attributeName==='data-orientation')){
        const started=state.started;state.started=undefined;requestAnimationFrame(()=>state.samples.push(performance.now()-started));
      }}).observe(document,{subtree:true,attributes:true,attributeFilter:['data-orientation']});
    });
    await page.goto('/');
    if(lane==='loaded')await page.waitForFunction(()=>(window as unknown as BrowserProofWindow).studyLatency.running>0);
    const flip=page.getByRole('button',{name:'Flip board',exact:true});await expect(flip).toBeVisible();
    for(let sample=0;sample<20;sample++) {
      await flip.click();
      await expect.poll(()=>page.evaluate(()=>(window as unknown as BrowserProofWindow).studyLatency.samples.length)).toBe(sample+1);
    }
    const samples:number[]=await page.evaluate(()=>(window as unknown as BrowserProofWindow).studyLatency.samples);report[lane]=samples;
    const ordered=[...samples].sort((a,b)=>a-b);expect(ordered[18]).toBeLessThanOrEqual(100);
  }
  await testInfo.attach('input-latency-evidence',{body:JSON.stringify({workload:{repertoire_lines:12000,moves_per_line:8,input_samples_per_lane:20,retained_notifications:500},samples_ms:report}),contentType:'application/json'});
});

test('study priority prepared next appears within the declared budget after truthful confirmation',async({page},testInfo)=>{
  const {prepareControlledUI,openingCard}=await import('./fixtures');
  await page.addInitScript(()=>localStorage.setItem('tempo-notifications-v1',JSON.stringify(Array.from({length:500},(_,index)=>({
    id:'history-'+index,key:'history-'+index,occurredAt:'2026-09-17T12:00:00Z',updatedAt:'2026-09-17T12:00:00Z',occurrenceCount:1,
    resolvedAt:'2026-09-17T13:00:00Z',clearedAt:null,active:false,severity:'info',source:'synthetic history',message:'Earlier recovery status',details:{diagnostic:'x'.repeat(900)}
  })))));
  const cards=Array.from({length:11},(_,index)=>({...openingCard('prepared-'+index,'Prepared '+index,'e2e4',400+index),revision:1,cycle:0}));
  const api=await prepareControlledUI(page,cards,false);let confirmed=0;
  for(const query of ([{limit:'20'},{limit:'20',include_opening_evidence:'true'}] as Record<string,string>[]))api.register({method:'GET',path:'/api/queue/window',query},route=>route.fulfill({json:{cards:cards.slice(confirmed),count:cards.length-confirmed,local_date:'2026-09-18'}}));
  for(let index=0;index<10;index++)api.register({method:'POST',path:`/api/cards/prepared-${index}/review`,query:{},validate:request=>{
    const body=request.body as {outcome:string;attempt_id:string;queue_entry_id:number;prepared_next:{card_id:string;queue_entry_id:number;revision:number;cycle:number;local_date:string}};
    expect(body.outcome).toBe('correct');expect(body.queue_entry_id).toBe(400+index);
    expect(request.headers['idempotency-key']).toBe('review-attempt:'+body.attempt_id);
    expect(body.prepared_next).toEqual({card_id:'prepared-'+(index+1),queue_entry_id:401+index,revision:1,cycle:0,local_date:'2026-09-18'});
  }},route=>{confirmed++;return route.fulfill({json:{persisted:true,prepared_continuation:{card_id:'prepared-'+confirmed,queue_entry_id:400+confirmed,revision:1,cycle:0,local_date:'2026-09-18'}}});});
  await page.addInitScript(()=>{
    const state={confirmed:undefined as number|undefined,samples:[] as number[],entry:undefined as string|undefined};Object.assign(window,{preparedLatency:state});
    const capture=(value:unknown)=>{if(value && typeof value==='object' && 'persisted' in value && value.persisted===true)state.confirmed=performance.now();};
    const json=Response.prototype.json;
    Response.prototype.json=async function(){const value=await json.call(this);capture(value);return value;};
    const text=Response.prototype.text;
    Response.prototype.text=async function(){const value=await text.call(this);try{capture(JSON.parse(value));}catch{}return value;};
    const observe=()=>{
      const current=document.querySelector('#train')?.getAttribute('data-queue-entry-id');
      if(!current)return;
      if(state.entry===undefined){state.entry=current;return;}
      if(current!==state.entry && state.confirmed!==undefined) {
        state.entry=current;const started=state.confirmed;state.confirmed=undefined;
        requestAnimationFrame(()=>state.samples.push(performance.now()-started));
      }
    };
    new MutationObserver(observe).observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['data-queue-entry-id']});
  });
  await page.goto('/');
  for(let index=0;index<10;index++) {
    await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id',String(400+index));
    await page.getByRole('button',{name:'Correct',exact:true}).click();
    await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id',String(401+index));
    await expect.poll(()=>page.evaluate(()=>(window as unknown as BrowserProofWindow).preparedLatency.samples.length)).toBe(index+1);
  }
  const samples:number[]=await page.evaluate(()=>(window as unknown as BrowserProofWindow).preparedLatency.samples);
  expect([...samples].sort((a,b)=>a-b)[9]).toBeLessThanOrEqual(250);expect(confirmed).toBe(10);
  await testInfo.attach('prepared-next-latency-evidence',{body:JSON.stringify({browser_after_confirmation_ms:samples,confirmations:confirmed,network:'controlled; real server/transport measured separately in PostgreSQL rehearsal'}),contentType:'application/json'});
});
