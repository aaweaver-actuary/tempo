import { createServer } from 'node:http';
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname, relative } from 'node:path';
import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';
import {runCreationScenarios} from './run-creation.mjs';
const root=resolve(new URL('../',import.meta.url).pathname);
const fixturePath=process.argv[2];const evidence=resolve(process.argv[3]??root+'/test-results/browser');
if(!fixturePath)throw new Error('Explicit actual Intelligence producer fixture required; no live fallback');
const fixtures=JSON.parse(await readFile(fixturePath,'utf8'));
assert.equal(fixtures.producer,'installed Intelligence with real disposable PostgreSQL');
const dist=resolve(root,'dist');
async function files(dir){return(await Promise.all((await readdir(dir,{withFileTypes:true})).map(entry=>entry.isDirectory()?files(resolve(dir,entry.name)):[relative(dist,resolve(dir,entry.name))]))).flat();}
const inventory=new Set(await files(dist));const unexpected=[],traffic=[];let browser,page,releaseDismissal;let wrongGameScope=true;let active=fixtures;let unscopedActive=fixtures.unscoped;let receiptVisible=false;let legacyView=false;
assert.ok(fixtures.dismissal?.receipt,'Actual producer dismissal receipt required');
const mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml'};
const host=createServer(async(request,response)=>{
  const url=new URL(request.url,'http://localhost');const path=url.pathname==='/'?'index.html':url.pathname.slice(1);
  if(!inventory.has(path)||!['GET','HEAD'].includes(request.method)||(url.search&&(path!=='index.html'||[...url.searchParams.keys()].some(key=>!['run','category','dismissal'].includes(key))))){unexpected.push('host escape '+request.method+' '+url.pathname);response.writeHead(404);response.end();return;}
  response.writeHead(200,{'Content-Type':mime[extname(path)]??'application/octet-stream'});response.end(await readFile(resolve(dist,path)));
});
await mkdir(evidence,{recursive:true});
await new Promise(resolve=>host.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+host.address().port;
let passed=false;let creationCases=[];
try{
  browser=await chromium.launch({headless:true});const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1280,height:900}});
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());const path=url.pathname;
    if(url.origin!==origin){unexpected.push('external '+url.origin+path);return route.abort();}
    if(!path.startsWith('/v1/'))return route.continue();
    const headers=request.headers();traffic.push({method:request.method(),path,query:url.search,body:request.postDataJSON()});
    try{
      assert.equal(headers.authorization,'Bearer synthetic-caller');assert.equal(headers['x-tempo-workspace'],'fixture');let body;
      if(request.method()==='POST' && url.searchParams.get('run_id')==='unscoped-browser'){assert.equal(decodeURIComponent(path)+url.search,fixtures.unscoped.dismissal.path);assert.deepEqual({budget:250,...request.postDataJSON()},fixtures.unscoped.dismissal.command);unscopedActive=fixtures.unscoped.dismissal;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(unscopedActive.receipt)});return;}
      if(request.method()==='POST' && request.postDataJSON().action==='reopen'){assert.equal(decodeURIComponent(path)+url.search,fixtures.reopening.path);assert.deepEqual({budget:250,...request.postDataJSON()},fixtures.reopening.command);active=fixtures.reopening;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(active.receipt)});return;}
      if(request.method()==='POST'){assert.equal(decodeURIComponent(path)+url.search,fixtures.dismissal.path);assert.deepEqual({budget:250,...request.postDataJSON()},fixtures.dismissal.command);active=fixtures.dismissal;await new Promise(resolve=>{releaseDismissal=resolve;});receiptVisible=true;await route.abort();return;}
      assert.equal(request.method(),'GET');
      if(path==='/v1/analysis/bootstrap'){assert.equal(url.search,'');body=fixtures.bootstrap;}
      else if(path==='/v1/analysis/inputs'){assert.equal(url.search,'');body=fixtures.inputs;}
      else if(path==='/v1/analysis/runs'){assert.equal(url.search,'');body=legacyView?{...active.runs,items:active.runs.items.map(run=>({...run,derivation_version:1,recovery_required:'legacy_analysis_revision_required'}))}:active.runs;}
      else if(path==='/v1/analysis/commands/'+fixtures.dismissal.command.command_id){if(!receiptVisible){await route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:'analysis_command_not_found'})});return;}assert.equal(url.search,'');body=fixtures.dismissal.receipt;}
      else if(path==='/v1/analysis/runs/run/games'){assert.equal(url.search,'');assert.ok(fixtures.games,'Actual native game-read fixture required');body=wrongGameScope?{...fixtures.games,input_id:'wrong-input'}:fixtures.games;}
      else if(path==='/v1/analysis/runs/unscoped-browser/report'){body=unscopedActive.report;}
      else if(path==='/v1/analysis/runs/run/report'){
        assert.ok([...url.searchParams.keys()].every(key=>['category','dismissal'].includes(key)));
        const category=url.searchParams.get('category'),dismissal=url.searchParams.get('dismissal');
        body={...active.report,items:active.report.items.filter(item=>(!category||item.category===category)&&(!dismissal||item.dismissal_state===dismissal)),next_cursor:null};
      }else if(path.startsWith('/v1/analysis/findings/')){
        const selected=url.searchParams.get('run_id')==='unscoped-browser'?unscopedActive:active;
        assert.ok(['run','unscoped-browser'].includes(url.searchParams.get('run_id')));assert.equal(url.searchParams.get('generation'),String(selected.report.generation));assert.equal([...url.searchParams.keys()].length,2);
        const id=decodeURIComponent(path.slice('/v1/analysis/findings/'.length));body=selected.details[id];assert.ok(body);
      }else throw new Error('unexpected API');
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    }catch(error){unexpected.push(path+': '+error.message);await route.abort();}
  });
  await context.addInitScript(ids=>{crypto.randomUUID=()=>{const count=Number(sessionStorage.getItem('test-command-sequence')??0);sessionStorage.setItem('test-command-sequence',String(count+1));return ids[count]??ids.at(-1);};},[fixtures.dismissal.command.command_id,'browser-blocked-command',fixtures.reopening.command.command_id,fixtures.unscoped.dismissal.command.command_id]);
  page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>unexpected.push('pageerror '+error.message));page.on('websocket',socket=>unexpected.push('WebSocket '+socket.url()));
  await page.goto(origin+'/?run=run');await page.getByLabel('Intelligence origin').fill(origin);await page.getByLabel('Workspace',{exact:true}).fill('fixture');await page.getByLabel('Caller credential').fill('synthetic-caller');await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByRole('heading',{name:'Analysis runs'}).waitFor();await page.getByRole('button',{name:'Inspect finding'}).first().waitFor();
  // C3_retrospective_repertoire_scope_visible: source mismatch is not historical knowledge.
  const chronologyNotice='Repertoire comparisons use the exported snapshot. Repertoire availability and prior study at game time are unverified.';
  await expect(page.getByText(chronologyNotice,{exact:true})).toBeVisible();
  // C3_report_completion_separate_from_input_scope: full input is not a finished scan.
  assert.equal(fixtures.report.complete,false);assert.equal(fixtures.report.source_completeness,'complete');
  await expect(page.locator('.report-state')).toContainText('Analysis incomplete');
  await expect(page.locator('.report-state')).toContainText('complete inputs');
  await expect(page.getByRole('button',{name:'Continue · next 250 decisions',exact:true})).toHaveCount(1);
  await expect(page.getByRole('button',{name:'Retry',exact:true})).toHaveCount(1);
  await expect(page.getByRole('button',{name:'Resume',exact:true})).toHaveCount(1);
  const writesBeforeGames=traffic.filter(item=>item.method==='POST').length;
  await page.getByRole('button',{name:'Browse selected games',exact:true}).click();
  await page.getByRole('heading',{name:'Selected games',exact:true}).waitFor();
  await expect(page.getByRole('status')).toHaveText('Game scope identity mismatch');
  assert.equal(await page.getByLabel('Game chessboard').count(),0);wrongGameScope=false;
  await page.getByRole('button',{name:'Back to findings',exact:true}).click();
  await page.getByRole('button',{name:'Browse selected games',exact:true}).click();
  await page.getByRole('heading',{name:'Selected games',exact:true}).waitFor();
  const unknownGame=fixtures.games.items.find(game=>game.contexts.some(context=>context.status==='unknown'&&context.after_divergence===true));
  assert.ok(unknownGame,'Native selected game with an unknown comparison required');
  await page.getByRole('button',{name:'Inspect game '+(unknownGame.provider_game_id??unknownGame.game_id),exact:true}).click();
  await page.getByLabel('Game chessboard').waitFor();await expect(page.getByText(chronologyNotice,{exact:true})).toBeVisible();
  const unknownContext=unknownGame.contexts.find(context=>context.status==='unknown'&&context.after_divergence===true);
  await page.getByLabel('Game position',{exact:true}).selectOption(String(unknownContext.ply));
  await expect(page.getByLabel('Accepted comparison')).toContainText('unknown');
  await expect(page.getByLabel('Accepted comparison')).toContainText(unknownContext.reason.replaceAll('_',' '));
  await page.getByRole('button',{name:'Next position',exact:true}).click();
  assert.equal(await page.getByLabel('Game position',{exact:true}).inputValue(),String(unknownContext.ply+1));
  await page.screenshot({path:resolve(evidence,'game-desktop.png'),fullPage:true});
  // C3_mobile_game_board_stays_inside_viewport: retain the immediate overflow guard.
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // Clipping the old pixel plane must not substitute for a complete resized board.
  await expect.poll(()=>page.getByLabel('Game chessboard').evaluate(element=>{
    const board=element.getBoundingClientRect(),pieces=[...element.querySelectorAll('piece')];
    return pieces.length===32&&pieces.every(piece=>{const bounds=piece.getBoundingClientRect();return bounds.left>=board.left-1&&bounds.right<=board.right+1&&bounds.top>=board.top-1&&bounds.bottom<=board.bottom+1;});
  })).toBe(true);
  await page.screenshot({path:resolve(evidence,'game-phone.png'),fullPage:true});
  assert.equal(traffic.filter(item=>item.method==='POST').length,writesBeforeGames,'Game browsing caused a mutation');
  await page.getByRole('button',{name:'Back to selected games',exact:true}).click();
  await page.getByRole('button',{name:'Back to findings',exact:true}).click();
  await page.setViewportSize({width:1280,height:960});await page.getByLabel('Category',{exact:true}).waitFor();
  await page.getByLabel('Category',{exact:true}).selectOption('practical_gap');await expect(page.getByRole('button',{name:'Inspect finding'})).toHaveCount(1);
  await page.getByRole('button',{name:'Inspect finding'}).click();await page.getByLabel('Finding chessboard').waitFor();await expect(page.getByText(chronologyNotice,{exact:true})).toBeVisible();await expect(page.locator('piece')).toHaveCount(32);
  // C3_gap_names_opponent_reply_separately_from_learner_response.
  await expect(page.getByText('Uncovered opponent reply: a6. No accepted continuation under this policy.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Flip board'}).focus();await page.keyboard.press('Enter');await page.getByRole('button',{name:'Show played move'}).click();await expect.poll(()=>page.locator('piece.white.knight').evaluateAll(nodes=>nodes.map(node=>node.cgKey).sort())).toEqual(['b1','f3']);await page.getByRole('button',{name:'Decision position'}).click();await expect.poll(()=>page.locator('piece.white.knight').evaluateAll(nodes=>nodes.map(node=>node.cgKey).sort())).toEqual(['b1','g1']);
  await page.getByText('Response proposal unavailable:',{exact:false}).waitFor();await page.screenshot({path:resolve(evidence,'detail-desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'Dismiss',exact:true}).click();await expect(page.getByRole('status')).toHaveText('Saving dismissal state…');
  assert.ok((await page.evaluate(()=>sessionStorage.getItem('tempo-analysis-command-v2:'+JSON.stringify([location.origin,'fixture'])))).includes(fixtures.dismissal.command.command_id));
  const originalPending=await page.evaluate(()=>sessionStorage.getItem('tempo-analysis-command-v2:'+JSON.stringify([location.origin,'fixture'])));
  await page.getByRole('button',{name:'Back to findings'}).click();
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(page.getByRole('status')).toHaveText('Recover the pending command before another change.');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('tempo-analysis-command-v2:'+JSON.stringify([location.origin,'fixture']))),originalPending);
  assert.equal(traffic.filter(item=>item.method==='POST').length,1);
  await expect.poll(()=>typeof releaseDismissal).toBe('function');releaseDismissal();await expect(page.getByRole('status')).not.toHaveText('Saving dismissal state…');
  await page.reload();await page.getByRole('button',{name:'Inspect finding'}).first().waitFor();assert.equal(await page.getByLabel('Category',{exact:true}).inputValue(),'practical_gap');
  assert.ok((await page.evaluate(()=>sessionStorage.getItem('tempo-analysis-connection-v1'))).includes('synthetic-caller'));
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('tempo-analysis-command-v2:'+JSON.stringify([location.origin,'fixture']))),null);
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Inspect finding'}).first().click();await page.getByLabel('Finding chessboard').waitFor();await page.getByRole('button',{name:'Reopen',exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:resolve(evidence,'detail-phone.png'),fullPage:true});
  await page.getByRole('button',{name:'Reopen',exact:true}).click();await page.getByRole('button',{name:'Inspect finding'}).first().waitFor();
  await page.reload();await page.getByRole('button',{name:'Inspect finding'}).first().click();await page.getByRole('button',{name:'Dismiss',exact:true}).waitFor();
  await page.getByRole('button',{name:'Back to findings'}).click();await page.getByLabel('Category',{exact:true}).selectOption('');await page.getByLabel('Run',{exact:true}).selectOption('unscoped-browser');
  await page.getByRole('button',{name:'Inspect finding'}).first().click();await page.getByText('Repertoire, policy and source lineage are unknown for this engine finding.',{exact:true}).waitFor();
  await page.getByLabel('Finding chessboard').waitFor();await page.screenshot({path:resolve(evidence,'unscoped-engine.png'),fullPage:true});
  await page.getByRole('button',{name:'Dismiss',exact:true}).click();await page.getByRole('button',{name:'Inspect finding'}).first().waitFor();await page.reload();await page.getByRole('button',{name:'Inspect finding'}).first().click();await page.getByRole('button',{name:'Reopen',exact:true}).waitFor();
  legacyView=true;
  await page.getByRole('button',{name:'Back to findings'}).click();
  await expect(page.getByRole('button',{name:'Continue · next 250 decisions',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Retry',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Resume',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Cancel',exact:true})).toHaveCount(1);
  await expect(page.getByText('This older analysis requires a new revision before continuing. Cancel remains available.',{exact:true})).toBeVisible();
  await context.close();assert.deepEqual(unexpected,[]);
  creationCases=await runCreationScenarios(browser,origin,fixtures,evidence);
  assert.ok(creationCases.every(result=>result.passed),JSON.stringify(creationCases.map(({case:identity,passed,error,run_ids})=>({identity,passed,error,run_ids}))));
  passed=true;
}finally{
  if(!passed&&page&&!page.isClosed()){await writeFile(resolve(evidence,"failure.html"),await page.content());await page.screenshot({path:resolve(evidence,"failure.png"),fullPage:true});}
  await browser?.close();await new Promise(resolve=>host.close(resolve));
  console.log(JSON.stringify({executedIdentities:passed?['C_native_workbench',...creationCases.map(item=>item.case)]:[],passed:passed?1+creationCases.length:creationCases.filter(item=>item.passed).length,failed:passed?0:Math.max(1,creationCases.filter(item=>!item.passed).length),skipped:0,success:passed}));
  await writeFile(resolve(evidence,'browser.json'),JSON.stringify({case:'C_native_workbench',passed,fixture:fixturePath,traffic,unexpected,port_closed:!host.listening},null,2)+'\n');
}
