import { chromium, expect } from '@playwright/test';
import { createServer } from 'node:http';
import assert from 'node:assert/strict';
import { Chess } from 'chess.js';
let input = ''; for await (const chunk of process.stdin) input += chunk;
const { frontend, learning, credential, replacement_credential, scenario, operation, mate, fixture_key } = JSON.parse(input);
const traffic = [], unexpected = [], fixtureEvents = [], applicationRequests = new Set(), confirmedReceipts = [];
let evidenceDelivered=false, priorityHead=false;
let holdNextQueue=false, heldQueue=false, delayedResponseCancelled=false, releaseDelayedQueue;
const delayedQueue=new Promise(resolve=>{releaseDelayedQueue=resolve;});
async function fixture(action) {
  const result=await fetch(`${frontend}/__fixture/${action}`, {method:'POST',headers:{'Content-Type':'application/json','X-Tempo-Fixture-Key':fixture_key},body:'{}'});
  assert.equal(result.status,200); fixtureEvents.push(action); return result.json();
}
let loss = ['lost-response','before-commit','typed-explanation-loss','tactics-discovery-loss','service-restart'].includes(scenario), blockedQueue = false, dropped = false;
const forward = async (request, response) => {
  const chunks = []; for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks);
  const path = new URL(request.url, learning).pathname;
  const review = request.method === 'POST' && /\/cards\/[^/]+\/review$/.test(path);
  const lossyCommand = review || request.method === 'POST' && (scenario === 'typed-explanation-loss' && path.endsWith('/attempts') || scenario === 'tactics-discovery-loss' && path==='/api/tactics/attempt');
  traffic.push({ method: request.method, path, key: request.headers['idempotency-key'], body: body.length ? JSON.parse(body) : null });
  if (blockedQueue && path === '/api/queue/window') { request.socket.destroy(); return; }
  if (review && loss && scenario === 'before-commit') { dropped=true; request.socket.destroy(); return; }
  try {
    const headers = { ...request.headers }; delete headers.host; delete headers.connection; delete headers['content-length'];
    const upstream = await fetch(new URL(request.url, learning), { method: request.method, headers, body: body.length ? body : undefined, redirect: 'error' });
    const bytes = Buffer.from(await upstream.arrayBuffer());
    if (path==='/api/queue/window' && request.method==='GET' && upstream.ok) {
      priorityHead ||= Boolean(JSON.parse(bytes).cards?.[0]?.gameplay_priority_reason);
      if (scenario==='remediation-late' && !evidenceDelivered) { await fixture('deliver-evidence'); evidenceDelivered=true; }
      if(holdNextQueue) {
        holdNextQueue=false;heldQueue=true;
        await delayedQueue;
        delayedResponseCancelled=response.destroyed;
      }
    }
    if (review && upstream.ok && scenario==='service-restart') await fixture('restart-learning');
    if (review && upstream.ok && scenario === 'next-load') blockedQueue = true;
    if (lossyCommand && loss && ['lost-response','typed-explanation-loss','tactics-discovery-loss','service-restart'].includes(scenario)) {
      loss = false;
      response.writeHead(upstream.status, { ...Object.fromEntries([...upstream.headers].filter(([key])=>!['content-encoding','transfer-encoding','content-length'].includes(key))), 'Content-Length': bytes.length });
      response.flushHeaders();
      response.write(bytes.subarray(0,1), () => { dropped=true; response.destroy(); });
      return;
    }
    response.writeHead(upstream.status, Object.fromEntries([...upstream.headers].filter(([key]) => !['content-encoding','transfer-encoding','content-length'].includes(key))));
    response.end(bytes);
  } catch { request.socket.destroy(); }
};
const proxy = createServer(forward);
await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
const endpoint = `http://127.0.0.1:${proxy.address().port}`;
let replacementProxy, replacementEndpoint;
const browser = await chromium.launch({headless:true});
const context = await browser.newContext({ serviceWorkers: scenario === 'offline-shell' ? 'allow' : 'block' });
context.on('request', request => {
  const url = new URL(request.url());
  applicationRequests.add(`${request.method()} ${url.origin === endpoint ? 'Learning' : url.origin === new URL(frontend).origin ? 'Study' : url.origin}${url.pathname}`);
  if (![new URL(frontend).origin, endpoint, replacementEndpoint].includes(url.origin)) unexpected.push({ method: request.method(), path: url.pathname, origin: url.origin });
});
async function connect(page) {
  await page.goto(frontend);
  await page.getByLabel('Learning origin').fill(endpoint);
  await page.getByLabel('Workspace', {exact:true}).fill(scenario === 'wrong-workspace' ? 'other-workspace' : 'workspace-a');
  await page.getByLabel('Study credential').fill(scenario === 'wrong-credential' ? 'wrong-study-credential-' + 'x'.repeat(32) : credential);
  await page.getByRole('button', {name:'Connect to Learning', exact:true}).click();
}
async function move(page, from='e2', to='e4') {
  const board = page.locator('cg-board:visible'); await expect(board).toBeVisible();
  const box = await board.boundingBox(); assert.ok(box);
  const flipped = await board.evaluate(element => Boolean(element.closest('.orientation-black')));
  for (const square of [from,to]) await page.mouse.click(box.x + ((flipped ? 7-(square.charCodeAt(0)-97) : square.charCodeAt(0)-97)+0.5)*box.width/8, box.y+((flipped ? Number(square[1])-1 : 8-Number(square[1]))+0.5)*box.height/8);
}
async function records(page) {
  return page.evaluate(() => new Promise((resolve,reject) => {
    const opened=indexedDB.open('tempo-study-intents'); opened.onerror=()=>reject(opened.error);
    opened.onsuccess=()=>{const db=opened.result;const read=db.transaction('intents').objectStore('intents').getAll();read.onsuccess=()=>{resolve(read.result);db.close();};read.onerror=()=>reject(read.error);};
  }));
}
let page;
try {
  page = await context.newPage();
  if (scenario === 'storage-denied') await page.addInitScript(() => { Object.defineProperty(window,'indexedDB',{get(){throw new DOMException('Storage denied','SecurityError');}}); });
  if (scenario === 'origin-transition') await page.addInitScript(() => {
    const opening=indexedDB.open('tempo-study-intents',1);
    opening.onupgradeneeded=()=>{const store=opening.result.createObjectStore('intents',{keyPath:['workspace','key']});store.add({workspace:'http://old-learning.invalid:study',key:'review:old',state:'pending',endpoint:'/api/cards/original/review',payload:{outcome:'correct'}});};
    opening.onsuccess=()=>opening.result.close();
  });
  if (operation) await page.addInitScript(operation => { crypto.randomUUID = () => operation; }, operation);
  if (['quota','aborted-storage'].includes(scenario)) await page.addInitScript(mode => {
    const add = IDBObjectStore.prototype.add;
    IDBObjectStore.prototype.add = function(value,...keys) {
      if (this.name==='intents' && value.kind==='review') {
        if (mode==='quota') throw new DOMException('Study storage quota exhausted','QuotaExceededError');
        const request = add.call(this,value,...keys);
        request.addEventListener('success',()=>this.transaction.abort()); return request;
      }
      return add.call(this,value,...keys);
    };
  },scenario);
  if (scenario==='incompatible-storage') await page.addInitScript(() => {
    const opening=indexedDB.open('tempo-study-intents',2);
    opening.onupgradeneeded=()=>opening.result.createObjectStore('intents',{keyPath:['workspace','key']});
    opening.onsuccess=()=>opening.result.close();
  });
  await connect(page);
  if (['storage-denied','origin-transition','wrong-credential','wrong-workspace','disallowed-origin','incompatible-storage'].includes(scenario)) {
    await expect(page.getByRole('alert')).toBeVisible();
    assert.equal(traffic.filter(item=>item.path==='/api/queue/window').length,0);
    if (scenario==='origin-transition') assert.equal((await records(page))[0].endpoint,'/api/cards/original/review');
  } else {
    if (scenario !== 'endgame-unavailable') await expect(page.locator('cg-board:visible')).toBeVisible();
    assert.equal(await page.getByRole('button',{name:'Builder',exact:true}).count(),0);
    if (scenario==='teaching') {
      await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='teaching'&&item.state==='committed').length).toBe(1);
      await page.reload(); await expect(page.locator('cg-board:visible')).toBeVisible();
    }
    let second;
    if (scenario==='two-tabs') { second=await context.newPage();await connect(second);await expect(second.locator('cg-board:visible')).toBeVisible(); }
    if (scenario==='offline-shell') {
      await expect.poll(()=>page.evaluate(async()=>{
        const registry=JSON.parse(localStorage.getItem('tempo-learning-authorities-v1'));
        const databaseName=Object.values(registry)[0].offlineDatabase;
        return new Promise(resolve=>{const opening=indexedDB.open(databaseName);opening.onsuccess=()=>{const db=opening.result;const read=db.transaction('training').objectStore('training').get('prepared-daily-queue');read.onsuccess=()=>{resolve(read.result?.cards?.length??0);db.close();};};});
      })).toBe(1);
      await page.evaluate(async()=>{await navigator.serviceWorker.ready;});
      await expect.poll(()=>page.evaluate(()=>Boolean(navigator.serviceWorker.controller))).toBe(true);
      await page.reload(); await expect(page.locator('cg-board:visible')).toBeVisible();
      await context.setOffline(true);
      await page.reload(); await expect(page.getByRole('button',{name:'Disconnect Learning'})).toBeVisible();
      await expect(page.locator('cg-board:visible')).toBeVisible();
      await move(page);
      await expect.poll(()=>page.evaluate(async()=>{
        const registry=JSON.parse(localStorage.getItem('tempo-learning-authorities-v1'));
        return new Promise(resolve=>{const opening=indexedDB.open(Object.values(registry)[0].offlineDatabase);opening.onsuccess=()=>{const db=opening.result;const read=db.transaction('training').objectStore('training').get('prepared-daily-queue');read.onsuccess=()=>{resolve(read.result?.attempts?.length??0);db.close();};};});
      })).toBe(1);
      assert.equal((await records(page)).filter(item=>item.kind==='review'&&item.state==='committed').length,0);
      assert.equal(traffic.filter(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path)).length,0);
      await page.reload();
      await expect(page.getByRole('button',{name:'Disconnect Learning'})).toBeVisible();
      await context.setOffline(false);
      await page.reload();
    }
    if (scenario.startsWith('tactics-discovery')) {
      await page.getByRole('button',{name:'Tactics',exact:true}).click();
      const deck=await (await fetch(`${frontend}/data/tactics-packs/hangingPiece-easy-01.json`)).json();
      const puzzle=[...deck].sort((left,right)=>left.DeckPosition-right.DeckPosition)[0];
      const board=new Chess(puzzle.FEN); const moves=puzzle.Moves.trim().split(/\s+/); board.move(moves[0]);
      for (let index=1;index<moves.length;index+=2) {
        await expect(page.locator('.board-frame')).toHaveAttribute('data-fen',board.fen());
        const answer=moves[index];await move(page,answer.slice(0,2),answer.slice(2,4));board.move(answer);
        if(moves[index+1]) board.move(moves[index+1]);
      }
      if(scenario.endsWith('-loss')) {await expect.poll(()=>dropped).toBe(true);await page.reload();}
      await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='tactic-command'&&item.state==='committed').length,{timeout:20000}).toBe(1);
    } else if (scenario==='delayed-replacement') {
      holdNextQueue=true;
      await page.getByRole('button',{name:'Tactics',exact:true}).click();
      await page.getByRole('button',{name:'Train',exact:true}).click();
      await expect.poll(()=>heldQueue).toBe(true);
      const newer=await context.newPage();await connect(newer);
      await expect(newer.locator('cg-board:visible')).toBeVisible();await move(newer);
      await expect.poll(async()=> (await records(newer)).filter(item=>item.kind==='review'&&item.state==='committed').length).toBe(1);
      await page.getByRole('button',{name:'Tactics',exact:true}).click();
      await page.getByRole('button',{name:'Train',exact:true}).click();
      await expect(page.getByText("You're done for today",{exact:true})).toBeVisible();
      releaseDelayedQueue();
      await expect.poll(()=>delayedResponseCancelled).toBe(true);
      await expect(page.locator('cg-board:visible')).toHaveCount(0);
      await expect(page.getByText("You're done for today",{exact:true})).toBeVisible();
      assert.equal(traffic.filter(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path)).length,1);
    } else if (scenario==='authority-reconnect') {
      await move(page);
      await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='review'&&item.state==='committed').length).toBe(1);
      const original=(await records(page)).find(item=>item.kind==='review');
      await fixture('rotate-study-credential');
      await page.getByRole('button',{name:'Study settings',exact:true}).click();
      await expect(page.getByLabel('Learning origin')).toBeVisible();
      replacementProxy=createServer(forward);
      await new Promise(resolve=>replacementProxy.listen(0,'127.0.0.1',resolve));
      replacementEndpoint=`http://127.0.0.1:${replacementProxy.address().port}`;
      await page.getByLabel('Learning origin').fill(replacementEndpoint);
      await page.getByLabel('Study credential').fill(replacement_credential);
      await page.getByRole('button',{name:'Connect to Learning',exact:true}).click();
      await page.getByRole('button',{name:'Study settings',exact:true}).click();
      await page.getByLabel('New tactics per day',{exact:true}).fill('3');
      await page.getByRole('button',{name:'Save Study settings',exact:true}).click();
      await expect(page.getByText('Study settings saved by Learning.',{exact:true})).toBeVisible();
      const retained=await records(page);
      assert.deepEqual(retained.find(item=>item.key===original.key&&item.workspace===original.workspace),original);
      assert.equal(retained.find(item=>item.kind==='settings-command'&&item.state==='committed').workspace,`${replacementEndpoint}:study:workspace-a`);
    } else if (scenario==='settings') {
      await page.getByRole('button',{name:'Study settings',exact:true}).click();
      await page.getByLabel('New tactics per day',{exact:true}).fill('3');
      await page.getByRole('button',{name:'Save Study settings',exact:true}).click();
      await expect(page.getByText('Study settings saved by Learning.',{exact:true})).toBeVisible();
      await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='settings-command'&&item.state==='committed').length).toBe(1);
    } else if (['quota','aborted-storage'].includes(scenario)) {
      await move(page);
      await expect(page.getByRole('alert').first()).toBeVisible();
      assert.equal(traffic.filter(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path)).length,0);
      assert.equal((await records(page)).filter(item=>item.kind==='review').length,0);
    } else if (scenario.startsWith('typed-')) {
      const family=scenario.slice(6).replace(/-loss$/, '');
      await expect(page.getByRole('heading',{name:'Study exercise',exact:true})).toBeVisible();
      if (family==='move_line') await move(page);
      if (family==='square_set'||family==='knight_path') {
        for (const square of family==='square_set'?['e2']:['b1','c3']) {
          await page.getByLabel('Coordinates or UCI move').fill(square);
          await page.getByRole('button',{name:'Add',exact:true}).click();
        }
      }
      if (family==='choice') await page.getByLabel('A',{exact:true}).check();
      if (family==='explanation') await page.getByLabel('Optional written answer').fill('A reasoned explanation');
      await page.getByRole('button',{name:family==='explanation'?'I have my answer':'Submit',exact:true}).click();
      if (scenario.endsWith('-loss')) { await expect.poll(()=>dropped).toBe(true); await page.reload(); }
      if (family==='explanation') await page.getByRole('button',{name:'Correct (self-assessed)',exact:true}).click();
      await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeVisible();
      await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='study-command'&&item.state==='committed').length).toBe(family==='explanation'?2:1);
    } else if (scenario.startsWith('defense')) {
      for (const square of ['b4','c2','e1','a1']) {
        await page.locator('.defense-square-entry input').fill(square);
        await page.getByRole('button',{name:'Select',exact:true}).click();
      }
      await page.getByRole('combobox').selectOption('checking_fork');
      await page.getByRole('button',{name:'Submit assessment',exact:true}).click();
      await page.getByRole('button',{name:'Continue to defense',exact:true}).click();
      await move(page,scenario==='defense'?'e1':'a2',scenario==='defense'?'f2':'a4');
      if (scenario==='defense') await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeVisible();
      else await expect(page.getByText('Analyzing this legal defense. Your study result has not been recorded yet.',{exact:true})).toBeVisible();
    } else if (scenario.startsWith('endgame')) {
      if (scenario==='endgame-cached') {
        await expect(page.getByRole('button',{name:'Win',exact:true})).toBeEnabled();
        await page.getByRole('button',{name:'Win',exact:true}).click();
        await move(page,mate.slice(0,2),mate.slice(2,4));
        await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='review'&&item.state==='committed').length,{timeout:20000}).toBe(1);
      } else {
        await expect(page.getByText(/No local tablebase answer/).first()).toBeVisible();
        assert.equal(traffic.filter(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path)).length,0);
      }
    } else {
      if (scenario==='guided-failure') {
        await move(page,'d2','d4');
        await expect.poll(()=>traffic.some(item=>item.method==='POST'&&item.path.endsWith('/fail'))).toBe(true);
        await page.reload(); await expect(page.locator('cg-board:visible')).toBeVisible();
      }
    if (scenario==='database-interruption') await fixture('stop-database');
    if (scenario!=='offline-shell') await move(page);
    if (scenario==='database-interruption') {
      await expect.poll(()=>traffic.some(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path))).toBe(true);
      assert.equal((await records(page)).filter(item=>item.kind==='review'&&item.state==='committed').length,0);
      await fixture('start-database');
      await page.reload();
    }
    if (second) await move(second);
    if (['lost-response','before-commit','service-restart'].includes(scenario)) {
      await expect.poll(()=>dropped).toBe(true);
      loss=false;
      await page.reload();
    }
    if (scenario==='offline-shell') {
      // This answer belongs to the existing prepared-queue store, not the online
      // coordinator. Verify its exact original completed HTTP receipt as well as
      // the owning test's independent one-review PostgreSQL assertion.
      await expect.poll(async()=>{
        const posted=traffic.find(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path));
        if(!posted) return false;
        const response=await fetch(`${endpoint}/api/operations/${encodeURIComponent(posted.key)}`,{headers:{Authorization:`Bearer ${credential}`,'X-Tempo-Workspace':'workspace-a'}});
        if(!response.ok) return false;
        const receipt=await response.json();
        if(receipt.operation_id!==posted.key||receipt.state!=='complete'||receipt.response?.persisted!==true||!Number.isInteger(receipt.response.review_id)) return false;
        confirmedReceipts.push(receipt);return true;
      },{timeout:20000}).toBe(true);
      const posts=traffic.filter(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path));
      assert.ok(posts.every(item=>item.key===posts[0].key&&JSON.stringify(item.body)===JSON.stringify(posts[0].body)));
    } else await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='review'&&item.state==='committed').length,{timeout:20000}).toBeGreaterThan(0);
    if (scenario==='remediation') assert.ok(priorityHead,'Real accepted evidence must prioritize the issued head');
    if (scenario==='remediation-late') {
      assert.equal((await fixture('assert-late-pending')).settled,0);
      await page.reload(); await expect(page.locator('cg-board:visible')).toBeVisible();
      await move(page);
      await expect.poll(async()=> (await records(page)).filter(item=>item.kind==='review'&&item.state==='committed').length,{timeout:20000}).toBe(2);
    }
    if (scenario==='next-load') {
      await expect(page.getByRole('alert').filter({hasText:'Result saved; the next card could not be loaded.'}).first()).toBeVisible({timeout:20000});
      blockedQueue=false;
      await page.getByRole('button',{name:'Retry loading the queue',exact:true}).click();
      await expect(page.locator('cg-board:visible')).toBeVisible();
    }
    const posts=traffic.filter(item=>item.method==='POST'&&/\/cards\/[^/]+\/review$/.test(item.path));
    assert.ok(posts.length>0);
    if (scenario==='lost-response'||scenario==='next-load') assert.equal(posts.length,1);
    if (scenario==='before-commit') assert.ok(posts.every(item=>item.key===posts[0].key&&JSON.stringify(item.body)===JSON.stringify(posts[0].body)));
    }
  }
  assert.deepEqual(unexpected,[]);
  console.log(JSON.stringify({complete:true,scenario,unexpected_requests:unexpected,application_requests:[...applicationRequests].sort(),traffic,confirmed_receipts:confirmedReceipts,fixture_events:fixtureEvents,fault_dropped:dropped,delayed_response_cancelled:delayedResponseCancelled,browser:browser.version()}));
} catch (error) {
  // Do not record form input, credentials, authenticated headers or raw traces.
  console.error(String(error.stack ?? error).replaceAll(credential, "[redacted]").replaceAll(replacement_credential??'unused-replacement-secret',"[redacted]"));
  console.error((await page?.locator("body").innerText().catch(()=>"") ?? "").slice(0,3000).replaceAll(credential,"[redacted]"));
  console.error(JSON.stringify(traffic)); process.exitCode=1;
} finally {
  releaseDelayedQueue();
  if(replacementProxy) {replacementProxy.closeAllConnections();await new Promise(resolve=>replacementProxy.close(resolve));}
  await context.close();await browser.close();proxy.closeAllConnections();await new Promise(resolve=>proxy.close(resolve));
}
