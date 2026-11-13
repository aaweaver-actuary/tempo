import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import type { APIRequestContext, APIResponse } from '@playwright/test';
import { test, expect } from './observability';
import { api } from './product-fixtures';
import { verifyPostgresFixtureTarget, postgresFixtureComposeArguments } from './product-fixture-reset';
import { navigate, prepareUI } from './ui-fixtures';

async function confirmed(request: APIRequestContext, response: APIResponse, id: string) {
  expect(response.ok(), `original operation HTTP ${response.status()}`).toBe(true);
  const acknowledgement = await response.json();
  if (response.status() !== 202) return acknowledgement;
  expect(acknowledgement.operation_id).toBe(id);
  let result: unknown;
  await expect.poll(async () => {
    const response = await request.get(`${api}/operations/${encodeURIComponent(id)}`);
    expect(response.ok()).toBe(true);
    const receipt = await response.json();
    expect(receipt.operation_id).toBe(id);
    if (receipt.state === 'failed' || receipt.state === 'blocked') throw new Error(`Original operation ${id} ${receipt.state}`);
    if (receipt.state === 'complete') result = receipt.response;
    return receipt.state;
  }, {timeout:60_000}).toBe('complete');
  return result;
}

async function authoritativeHead(request: APIRequestContext) {
  let head: {id:string;queue_entry_id:number;cycle:number;revision:number;study_exercise_id:string} | undefined;
  await expect.poll(async () => {
    const response = await request.get(`${api}/queue/window?limit=20`);
    if (response.status() === 202) {
      const acknowledgment = await response.json();
      if (typeof acknowledgment.operation_id !== 'string' || !acknowledgment.operation_id) throw new Error('Queue issuance lacks original identity');
      await confirmed(request, response, acknowledgment.operation_id);
      return false;
    }
    if (!response.ok()) throw new Error(`Queue head HTTP ${response.status()}`);
    const queue = await response.json();
    if (!Array.isArray(queue.cards)) throw new Error('Malformed authoritative queue');
    head = queue.cards[0]; return Boolean(head);
  }, {timeout:60_000}).toBe(true);
  return head!;
}

test('real frozen Study preserves original parent and child through reload and playable successor', async ({page, request}) => {
  test.setTimeout(180_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const health = await request.get(`${api}/health`);
  await verifyPostgresFixtureTarget({health:await health.json(), apiUrl:api, project});
  const compose = postgresFixtureComposeArguments(project);
  const settings = await (await request.get(`${api}/settings`)).json();
  const day = new Intl.DateTimeFormat('en-CA', {timeZone:settings.timezone === 'local' ? process.env.TZ : settings.timezone,
    year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const prefix = `p12-frozen-${randomUUID().replaceAll('-', '')}`;
  const operations: string[] = [];
  let displaced: number[][] = [];
  const fixture = (action: string, ...args: string[]) => JSON.parse(execFileSync('docker', [...compose,'run','--rm','--no-deps','-e','TEMPO_TEST_INSTANCE=disposable','schema','python','-',action,prefix,day,...args], {
    input:readFileSync('tests/fixtures/postgres-frozen-study-browser.py','utf8'), encoding:'utf8', timeout:30_000,
  }));
  let originalFailure: unknown;
  try {
    let seeded: {ready:boolean; displaced:number[][]; entries:number[]} = {ready:false, displaced:[], entries:[]};
    await expect.poll(() => { seeded = fixture('seed'); return seeded.ready; }, {timeout:60_000}).toBe(true);
    displaced = seeded.displaced; const entries = seeded.entries;
    await prepareUI(page); await navigate(page,'Train');
    await expect(page.getByText('P12 first move')).toBeVisible();
    const present = await request.get(`${api}/studies/${prefix}/exercises/${prefix}-exercise-0/present?${new URLSearchParams({card_id:`${prefix}-card-0`,queue_entry_id:String(entries[0]),queue_cycle:'0',revision:'1'})}`);
    expect(present.ok()).toBe(true);
    const projection = await present.json();
    expect(projection).toMatchObject({id:`${prefix}-exercise-0`, revision:1, type:'move_line'});
    expect(projection).not.toHaveProperty('accepted_lines');
    await page.getByLabel('Coordinates or UCI move').fill('e2e4');
    await page.getByRole('button',{name:'Add',exact:true}).click();
    const firstResponse = page.waitForResponse(response => response.request().method()==='POST' && response.url().endsWith(`/exercises/${prefix}-exercise-0/attempts`));
    await page.getByRole('button',{name:'Submit',exact:true}).click();
    const first = await firstResponse; const firstRequest = first.request();
    const firstId = firstRequest.headers()['idempotency-key']; const firstBody = firstRequest.postDataJSON();
    operations.push(firstId); expect(firstBody).toMatchObject({revision:1,card_id:`${prefix}-card-0`,queue_entry_id:entries[0],queue_cycle:0,context:'review'});
    const firstResult = await confirmed(request,first as unknown as APIResponse,firstId) as {attempt_id:string;persisted:boolean;review:{review_id:number}};
    expect(firstResult).toMatchObject({attempt_id:firstBody.attempt_id,persisted:true,review:{review_id:expect.any(Number)}});
    await expect(page.getByText('P12 original feedback')).toBeVisible();
    await page.getByRole('button',{name:'Continue',exact:true}).click();
    await expect(page.getByText('P12 explain the move')).toBeVisible();
    await page.getByLabel('Optional written answer').fill('A pawn claims the centre.');
    const parentResponse = page.waitForResponse(response => response.request().method()==='POST' && response.url().endsWith(`/exercises/${prefix}-exercise-1/attempts`));
    await page.getByRole('button',{name:'I have my answer'}).click();
    const parent = await parentResponse; const parentRequest = parent.request();
    const parentId = parentRequest.headers()['idempotency-key']; const parentBody = parentRequest.postDataJSON();
    operations.push(parentId); expect(parentBody).toMatchObject({revision:1,card_id:`${prefix}-card-1`,queue_entry_id:entries[1],queue_cycle:0,context:'review'});
    const parentResult = await confirmed(request,parent as unknown as APIResponse,parentId);
    expect(parentResult).toMatchObject({attempt_id:parentBody.attempt_id,pending_self_assessment:true});
    await expect(page.getByRole('button',{name:'Correct (self-assessed)'})).toBeVisible();
    const pending = fixture('snapshot'); expect(pending.reviews).toHaveLength(1);
    await page.reload();
    await expect(page.getByRole('button',{name:'Correct (self-assessed)'})).toBeVisible();
    const childResponse = page.waitForResponse(response => response.request().method()==='POST' && response.url().endsWith(`/${parentBody.attempt_id}/self-assess`));
    await page.getByRole('button',{name:'Correct (self-assessed)'}).click();
    const child = await childResponse; const childRequest = child.request();
    const childId = childRequest.headers()['idempotency-key']; const childBody = childRequest.postDataJSON();
    operations.push(childId); expect(childId).toBeTruthy(); expect(childBody).toEqual({rating:'correct'});
    const childResult = await confirmed(request,child as unknown as APIResponse,childId);
    expect(childResult).toMatchObject({attempt_id:parentBody.attempt_id,persisted:true,review:{review_id:expect.any(Number)}});
    const finished = fixture('snapshot'); expect(finished.attempts).toHaveLength(2); expect(finished.reviews).toHaveLength(2);
    expect(finished.origins).toContainEqual([entries[0],`${prefix}-card-0`,1,1]);
    expect(finished.origins).toContainEqual([entries[1],`${prefix}-card-1`,1,1]);
    const parentReplay = await request.post(parentRequest.url(),{headers:{'Idempotency-Key':parentId},data:parentBody});
    expect(await confirmed(request,parentReplay,parentId)).toEqual(parentResult);
    const childReplay = await request.post(childRequest.url(),{headers:{'Idempotency-Key':childId},data:childBody});
    expect(await confirmed(request,childReplay,childId)).toEqual(childResult);
    expect(fixture('snapshot')).toEqual(finished);
    await page.getByRole('button',{name:'Continue',exact:true}).click();
    const head = await authoritativeHead(request);
    expect(head).toMatchObject({id:`${prefix}-card-2`,queue_entry_id:entries[2],cycle:0,revision:1,study_exercise_id:`${prefix}-exercise-2`});
    const presentNext = await request.get(`${api}/studies/${prefix}/exercises/${prefix}-exercise-2/present?${new URLSearchParams({card_id:head.id,queue_entry_id:String(head.queue_entry_id),queue_cycle:String(head.cycle),revision:String(head.revision)})}`);
    expect(presentNext.ok()).toBe(true);
    expect(await presentNext.json()).toMatchObject({id:`${prefix}-exercise-2`,revision:1,prompt:'P12 playable successor'});
    const issued = fixture('snapshot');
    expect(issued.origins).toContainEqual([entries[2],`${prefix}-card-2`,1,1]);
    expect(issued.reviews).toHaveLength(2);
    await expect(page.getByText('P12 playable successor')).toBeVisible();
    await expect(page.getByLabel('Coordinates or UCI move')).toBeEnabled();
    await page.getByLabel('Coordinates or UCI move').fill('d2d4');
    await page.getByRole('button',{name:'Add',exact:true}).click();
    await expect(page.getByText('Answer: d2d4')).toBeVisible();
    expect(fixture('snapshot').reviews).toHaveLength(2);
  } catch (error) { originalFailure = error; throw error; } finally {
    try {
      const cleanup = fixture('cleanup',JSON.stringify(displaced),JSON.stringify(operations));
      expect(cleanup.cleaned).toBe(true);
      expect(cleanup.restored + cleanup.reordered + cleanup.removed).toBe(displaced.length);
      expect(Object.values(cleanup.remaining).every(value => value === 0)).toBe(true);
    } catch (cleanupError) {
      if (originalFailure !== undefined) throw new AggregateError([originalFailure, cleanupError], 'Study journey and fixture cleanup failed');
      throw cleanupError;
    }
  }
});

test.describe('real frozen phone recovery', () => {
  test.use({userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'});
  test('original frozen phone attempt survives offline reload and reconciles once', async ({page, context, request}) => {
    test.setTimeout(180_000);
    const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
    const health = await request.get(`${api}/health`);
    await verifyPostgresFixtureTarget({health:await health.json(), apiUrl:api, project});
    const compose = postgresFixtureComposeArguments(project);
    const settings = await (await request.get(`${api}/settings`)).json();
    const day = new Intl.DateTimeFormat('en-CA', {timeZone:settings.timezone === 'local' ? process.env.TZ : settings.timezone,
      year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    const prefix = `p12-frozen-${randomUUID().replaceAll('-', '')}`;
    let displaced: number[][] = []; const operations: string[] = [];
    const fixture = (action: string, ...args: string[]) => JSON.parse(execFileSync('docker', [...compose,'run','--rm','--no-deps','-e','TEMPO_TEST_INSTANCE=disposable','schema','python','-',action,prefix,day,...args], {
      input:readFileSync('tests/fixtures/postgres-frozen-study-browser.py','utf8'), encoding:'utf8', timeout:30_000,
    }));
    let originalFailure: unknown;
    try {
      let seeded: {ready:boolean; displaced:number[][]; entries:number[]} = {ready:false,displaced:[],entries:[]};
      await expect.poll(() => {seeded=fixture('seed'); return seeded.ready;}, {timeout:60_000}).toBe(true);
      displaced=seeded.displaced;
      await context.addInitScript(() => Object.defineProperty(navigator,'standalone',{value:true, configurable:true}));
      await page.setViewportSize({width:390,height:844});
      await prepareUI(page); await navigate(page,'Train');
      await expect(page.getByText('P12 first move')).toBeVisible();
      const readPhone = () => page.evaluate(async () => {
        const opened=indexedDB.open('tempo-offline-training',2);
        const db=await new Promise<IDBDatabase>((resolve,reject)=>{opened.onsuccess=()=>resolve(opened.result);opened.onerror=()=>reject(opened.error);});
        const reading=db.transaction('training').objectStore('training').get('prepared-daily-queue');
        const result=await new Promise<any>((resolve,reject)=>{reading.onsuccess=()=>resolve(reading.result);reading.onerror=()=>reject(reading.error);});
        db.close(); return result;
      });
      await expect.poll(async () => {
        const saved=await readPhone();
        return Boolean(saved?.localDate===day && saved.cards?.some((card: {id:string}) => card.id===`${prefix}-card-0`) && await page.evaluate(() => Boolean(navigator.serviceWorker?.controller)));
      },{timeout:60_000}).toBe(true);
      await context.setOffline(true); await page.reload();
      await expect(page.getByText('P12 first move')).toBeVisible();
      const held = {localEntryId:99000001,cardId:`${prefix}-held`,outcome:'correct',guided:false,
        completedAt:new Date().toISOString(),expectedReviewId:0,expectedRevision:1,studyId:prefix,
        attemptId:`${prefix}-held-attempt`,answer:{type:'retired',value:'original'}};
      await page.evaluate(async original => {
        const opened=indexedDB.open('tempo-offline-training',2);
        const db=await new Promise<IDBDatabase>((resolve,reject)=>{opened.onsuccess=()=>resolve(opened.result);opened.onerror=()=>reject(opened.error);});
        const tx=db.transaction('training','readwrite'); const store=tx.objectStore('training');
        const item=await new Promise<any>((resolve,reject)=>{const get=store.get('prepared-daily-queue');get.onsuccess=()=>resolve(get.result);get.onerror=()=>reject(get.error);});
        item.attempts.unshift(original); store.put(item,'prepared-daily-queue');
        await new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error);}); db.close();
      }, held);
      await page.getByLabel('Coordinates or UCI move').fill('e2e4');
      await page.getByRole('button',{name:'Add',exact:true}).click();
      await page.getByRole('button',{name:'Submit',exact:true}).click();
      await expect.poll(async () => (await readPhone())?.attempts?.length ?? 0).toBe(2);
      const before = (await readPhone()).attempts.find((attempt: {cardId:string}) => attempt.cardId===`${prefix}-card-0`);
      expect(before).toMatchObject({cardId:`${prefix}-card-0`,localEntryId:seeded.entries[0],studyId:prefix,exerciseId:`${prefix}-exercise-0`,expectedRevision:1});
      expect(before.attemptId).toBeTruthy();
      await page.reload();
      const sentUrls: string[] = [];
      page.on('request', event => { if (event.method() === 'POST') sentUrls.push(event.url()); });
      const replayed = page.waitForRequest(event => event.method()==='POST' && event.url().endsWith(`/exercises/${prefix}-exercise-0/attempts`));
      await context.setOffline(false);
      const delivery = await replayed;
      const body=delivery.postDataJSON(); const operationId=delivery.headers()['idempotency-key'];
      operations.push(operationId);
      expect(operationId).toBe(`phone-study:${before.attemptId}`);
      expect(body).toMatchObject({attempt_id:before.attemptId,card_id:before.cardId,queue_entry_id:before.localEntryId,revision:before.expectedRevision,context:'review'});
      await expect.poll(async()=> (await readPhone())?.attempts?.find((attempt: {cardId:string}) => attempt.cardId===`${prefix}-card-0`)?.serverAcknowledged === true,{timeout:60_000}).toBe(true);
      expect((await readPhone()).attempts.find((attempt: {cardId:string}) => attempt.cardId===held.cardId)).toEqual(held);
      expect(sentUrls.some(url => url.includes(held.cardId) || url.includes(held.attemptId))).toBe(false);
      const receipt=await (await request.get(`${api}/operations/${encodeURIComponent(operationId)}`)).json();
      expect(receipt).toMatchObject({operation_id:operationId,state:'complete',response:{attempt_id:before.attemptId,persisted:true}});
      const snapshot=fixture('snapshot'); expect(snapshot.attempts).toHaveLength(1); expect(snapshot.reviews).toHaveLength(1);
      expect(snapshot.attempts[0].slice(0,7)).toEqual([before.attemptId,`${prefix}-exercise-0`,1,before.cardId,before.localEntryId,0,'review']);
      expect(snapshot.reviews[0][1]).toBe(before.cardId);
      expect(snapshot.origins).toContainEqual([before.localEntryId,before.cardId,1,1]);
      await page.reload(); expect(fixture('snapshot')).toEqual(snapshot);
    } catch (error) { originalFailure = error; throw error; } finally {
      try {
        await context.setOffline(false);
        const cleanup = fixture('cleanup',JSON.stringify(displaced),JSON.stringify(operations));
        expect(cleanup.cleaned).toBe(true);
        expect(cleanup.restored + cleanup.reordered + cleanup.removed).toBe(displaced.length);
        expect(Object.values(cleanup.remaining).every(value => value === 0)).toBe(true);
      } catch (cleanupError) {
        if (originalFailure !== undefined) throw new AggregateError([originalFailure, cleanupError], 'Study journey and fixture cleanup failed');
        throw cleanupError;
      }
    }
  });

  test('real phone Again resolves original repeat cycle before replaying child', async ({page, context, request}) => {
    test.setTimeout(180_000);
    const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
    const health = await request.get(`${api}/health`);
    await verifyPostgresFixtureTarget({health:await health.json(), apiUrl:api, project});
    const compose = postgresFixtureComposeArguments(project);
    const settings = await (await request.get(`${api}/settings`)).json();
    const day = new Intl.DateTimeFormat('en-CA', {timeZone:settings.timezone === 'local' ? process.env.TZ : settings.timezone,
      year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    const prefix = `p12-frozen-${randomUUID().replaceAll('-', '')}`;
    let displaced: number[][] = []; const operations: string[] = [];
    const fixture = (action: string, ...args: string[]) => JSON.parse(execFileSync('docker', [...compose,'run','--rm','--no-deps','-e','TEMPO_TEST_INSTANCE=disposable','schema','python','-',action,prefix,day,...args], {
      input:readFileSync('tests/fixtures/postgres-frozen-study-browser.py','utf8'), encoding:'utf8', timeout:30_000,
    }));
    const readPhone = () => page.evaluate(async () => {
      const opened=indexedDB.open('tempo-offline-training',2);
      const db=await new Promise<IDBDatabase>((resolve,reject)=>{opened.onsuccess=()=>resolve(opened.result);opened.onerror=()=>reject(opened.error);});
      const reading=db.transaction('training').objectStore('training').get('prepared-daily-queue');
      const result=await new Promise<any>((resolve,reject)=>{reading.onsuccess=()=>resolve(reading.result);reading.onerror=()=>reject(reading.error);});
      db.close(); return result;
    });
    let originalFailure: unknown;
    try {
      let seeded: {ready:boolean; displaced:number[][]; entries:number[]} = {ready:false,displaced:[],entries:[]};
      await expect.poll(() => {seeded=fixture('seed-requeue'); return seeded.ready;}, {timeout:60_000}).toBe(true);
      displaced=seeded.displaced;
      await context.addInitScript(() => Object.defineProperty(navigator,'standalone',{value:true, configurable:true}));
      await page.setViewportSize({width:390,height:844});
      await prepareUI(page); await navigate(page,'Train');
      await expect(page.getByText('P12 first move')).toBeVisible();
      const head = await authoritativeHead(request);
      expect(head).toMatchObject({id:`${prefix}-card-0`,queue_entry_id:seeded.entries[0],cycle:0,revision:1});
      await expect.poll(async () => {
        const saved=await readPhone();
        return Boolean(saved?.localDate===day && saved.cards?.some((item: {id:string}) => item.id===`${prefix}-card-0`) && await page.evaluate(() => Boolean(navigator.serviceWorker?.controller)));
      }, {timeout:60_000}).toBe(true);
      await context.setOffline(true); await page.reload();
      await page.getByLabel('Coordinates or UCI move').fill('d2d4');
      await page.getByRole('button',{name:'Add',exact:true}).click();
      await page.getByRole('button',{name:'Submit',exact:true}).click();
      await expect.poll(async () => (await readPhone())?.attempts?.length ?? 0).toBe(1);
      await page.getByRole('button',{name:'Continue',exact:true}).click();
      await expect(page.getByText('P12 first move')).toBeVisible();
      await page.getByLabel('Coordinates or UCI move').fill('e2e4');
      await page.getByRole('button',{name:'Add',exact:true}).click();
      await page.getByRole('button',{name:'Submit',exact:true}).click();
      await expect.poll(async () => (await readPhone())?.attempts?.length ?? 0).toBe(2);
      const saved=(await readPhone()).attempts;
      const parent=saved.find((item: {localEntryId:number}) => item.localEntryId===seeded.entries[0]);
      const child=saved.find((item: {parentLocalEntryId?:number}) => item.parentLocalEntryId===seeded.entries[0]);
      expect(parent).toMatchObject({cardId:head.id,queueCycle:0,outcome:'again'});
      expect(child).toMatchObject({cardId:head.id,queueCycle:1,outcome:'correct'});
      expect(parent.attemptId).toBeTruthy(); expect(child.attemptId).toBeTruthy(); expect(child.attemptId).not.toBe(parent.attemptId);
      await page.reload();
      const parentKey=`phone-study:${parent.attemptId}`, childKey=`phone-study:${child.attemptId}`;
      const parentDelivery=page.waitForRequest(event => event.method()==='POST' && event.headers()['idempotency-key']===parentKey);
      const repeatRead=page.waitForResponse(response => response.request().method()==='GET' && response.url().endsWith(`/attempts/${parent.attemptId}/requeue`));
      const childDelivery=page.waitForRequest(event => event.method()==='POST' && event.headers()['idempotency-key']===childKey);
      await context.setOffline(false);
      const first=await parentDelivery; operations.push(parentKey); expect(first.postDataJSON()).toMatchObject({attempt_id:parent.attemptId,queue_entry_id:head.queue_entry_id,queue_cycle:0});
      const repeatResponse=await repeatRead; expect(repeatResponse.ok()).toBe(true); const repeat=await repeatResponse.json();
      expect(repeat).toMatchObject({parent_attempt_id:parent.attemptId,card_id:head.id,revision:1,cycle:4,review_id:expect.any(Number),queue_entry_id:expect.any(Number)});
      const second=await childDelivery; operations.push(childKey);
      expect(second.postDataJSON()).toMatchObject({attempt_id:child.attemptId,card_id:head.id,queue_entry_id:repeat.queue_entry_id,queue_cycle:4,expected_review_id:repeat.review_id});
      await expect.poll(async () => (await readPhone())?.attempts?.filter((item: {serverAcknowledged?:boolean}) => item.serverAcknowledged).length ?? 0,{timeout:60_000}).toBe(2);
      for (const [key, attempt] of [[parentKey,parent.attemptId],[childKey,child.attemptId]]) {
        const receipt=await (await request.get(`${api}/operations/${encodeURIComponent(key)}`)).json();
        expect(receipt).toMatchObject({operation_id:key,state:'complete',response:{attempt_id:attempt,persisted:true}});
      }
      const snapshot=fixture('snapshot'); expect(snapshot.attempts).toHaveLength(2); expect(snapshot.reviews).toHaveLength(2);
      expect(snapshot.attempts).toContainEqual(expect.arrayContaining([parent.attemptId,`${prefix}-exercise-0`,1,head.id,head.queue_entry_id,0,'review']));
      expect(snapshot.attempts).toContainEqual(expect.arrayContaining([child.attemptId,`${prefix}-exercise-0`,1,head.id,repeat.queue_entry_id,4,'review']));
      await page.reload(); expect(fixture('snapshot')).toEqual(snapshot);
    } catch (error) { originalFailure = error; throw error; } finally {
      try {
        await context.setOffline(false);
        const cleanup = fixture('cleanup',JSON.stringify(displaced),JSON.stringify(operations));
        expect(cleanup.cleaned).toBe(true); expect(cleanup.restored + cleanup.reordered + cleanup.removed).toBe(displaced.length);
        expect(Object.values(cleanup.remaining).every(value => value === 0)).toBe(true);
      } catch (cleanupError) {
        if (originalFailure !== undefined) throw new AggregateError([originalFailure, cleanupError], 'Study journey and fixture cleanup failed');
        throw cleanupError;
      }
    }
  });
});
