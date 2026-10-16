import { chromium } from '@playwright/test';
import { clientBundle } from './bundle.mjs';
import { resolve,dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
let input='';for await (const chunk of process.stdin) input+=chunk;
const {url,token,loss,remediation=false}=JSON.parse(input);
const here=dirname(fileURLToPath(import.meta.url));
const bundled=await clientBundle(here);
const browser=await chromium.launch({headless:true});
try {
  const context=await browser.newContext();
  const requests=[];
  await context.route('**/*',async route=>{
    assert.equal(new URL(route.request().url()).origin,new URL(url).origin,'Unexpected optional-service traffic');
    requests.push(new URL(route.request().url()).pathname);
    await route.continue();
  });
  await context.addInitScript(({token})=>{
    const actualFetch=window.fetch;
    window.fetch=(input,options={})=>{
      const headers=new Headers(options.headers);headers.set('Authorization','Bearer '+token);
      return actualFetch(input,{...options,headers});
    };
  },{token});
  const page=await context.newPage();
  await page.goto(url+'/health/live');
  await page.addScriptTag({content:bundled});
  const card=await page.evaluate(async()=>{
    const raw=await (await fetch('/api/queue/window')).json();
    const parsed=learningClient.queueEnvelopeSchema.parse(raw);
    return learningClient.queueCardSchema.parse(parsed.cards[0]);
  });
  if(remediation) assert.ok(card.gameplay_priority_reason,'Expected accepted remediation');
  const attempt=await page.evaluate(async card=>{
    const attemptId=crypto.randomUUID();
    await learningClient.enqueuePendingReview({backendId:card.id,queueEntryId:card.queue_entry_id,outcome:'correct',guided:false,attemptId,expectedRevision:card.revision});
    return attemptId;
  },card);
  if(loss) {
    let intercepted=false;
    await page.route('**/api/cards/*/review',async route=>{
      const response=await route.fetch();assert.equal(response.status(),200);
      intercepted=true;
      await route.abort('failed');
    });
    await page.evaluate(async()=>{try{await learningClient.flushPendingReviews();}catch{/* Deliberate response loss after the real API committed. */}});
    assert.ok(intercepted,'Real review request was not sent');
    assert.equal(await page.evaluate(()=>learningClient.pendingReviews().length),1);
    await page.unroute('**/api/cards/*/review');
    await page.reload();
    await page.addScriptTag({content:bundled});
  }
  const result=await page.evaluate(async()=>learningClient.flushPendingReviews(Infinity,true));
  assert.deepEqual(result.persistedAttemptIds,[attempt]);
  assert.equal(await page.evaluate(()=>learningClient.pendingReviews().length),0);
  const next=await page.evaluate(async()=>{
    const raw=await (await fetch('/api/queue/window')).json();
    return learningClient.queueEnvelopeSchema.parse(raw);
  });
  console.log(JSON.stringify({complete:true,case:loss?'lost-response-reload-recovery':'queue-review-progression',attempt,remaining:next.count,browser:browser.version(),remediation,requests}));
  await context.close();
} finally {await browser.close();}
