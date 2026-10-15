import { chromium } from '@playwright/test';
import { build } from 'esbuild';
import { resolve,dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
let input='';for await (const chunk of process.stdin) input+=chunk;
const {url,token,loss}=JSON.parse(input);
const here=dirname(fileURLToPath(import.meta.url));
const bundled=await build({entryPoints:[resolve(here,'client.ts')],bundle:true,write:false,format:'iife',globalName:'learningClient',platform:'browser',nodePaths:[resolve(here,'node_modules')],define:{__TEMPO_API_URL__:'window.location.origin'}});
const browser=await chromium.launch({headless:true});
try {
  const context=await browser.newContext();
  await context.addInitScript(({token})=>{
    const actualFetch=window.fetch;
    window.fetch=(input,options={})=>{
      const headers=new Headers(options.headers);headers.set('Authorization','Bearer '+token);
      return actualFetch(input,{...options,headers});
    };
  },{token});
  const page=await context.newPage();
  await page.goto(url+'/health/live');
  await page.addScriptTag({content:bundled.outputFiles[0].text});
  const card=await page.evaluate(async()=>{
    const raw=await (await fetch('/api/queue/window')).json();
    const parsed=learningClient.queueEnvelopeSchema.parse(raw);
    return learningClient.queueCardSchema.parse(parsed.cards[0]);
  });
  const attempt=await page.evaluate(card=>{
    const attemptId=crypto.randomUUID();
    learningClient.enqueuePendingReview({backendId:card.id,queueEntryId:card.queue_entry_id,outcome:'correct',guided:false,attemptId,expectedRevision:card.revision});
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
    await page.addScriptTag({content:bundled.outputFiles[0].text});
  }
  const result=await page.evaluate(async()=>learningClient.flushPendingReviews(Infinity,true));
  assert.deepEqual(result.persistedAttemptIds,[attempt]);
  assert.equal(await page.evaluate(()=>learningClient.pendingReviews().length),0);
  const next=await page.evaluate(async()=>{
    const raw=await (await fetch('/api/queue/window')).json();
    return learningClient.queueEnvelopeSchema.parse(raw);
  });
  console.log(JSON.stringify({complete:true,case:loss?'lost-response-reload-recovery':'queue-review-progression',attempt,remaining:next.count,browser:browser.version()}));
  await context.close();
} finally {await browser.close();}
