import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {readFileSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const root=resolve(process.argv[2]);
const prepared=JSON.parse(readFileSync(join(root,'prepared-session.json'),'utf8'));
const {chromium}=await import(pathToFileURL(join(prepared.target.root,'node_modules/playwright/index.mjs')));
const applicationUrl=new URL(prepared.target.webUrl);
assert.equal(JSON.parse(readFileSync(root+'/release-operation.json','utf8')).phase,'ready');
const browser=await chromium.launch({headless:true});
let context;
try{
context=await browser.newContext({viewport:{width:320,height:568},serviceWorkers:'block'});
const blocked=[],errors=[];
// Own empty context only. Block all writes and queue issuance; do not touch
// Andy's browser, saved intents, reviews, captures or live recovery commands.
await context.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url());
 if(request.method()!=='GET'||url.pathname.startsWith('/api/queue')){
  blocked.push({method:request.method(),path:url.pathname});return route.abort('blockedbyclient');
 }
 if(url.origin!==applicationUrl.origin&&['http:','https:'].includes(url.protocol))return route.abort('blockedbyclient');
 return route.continue();
});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
const storageKey='tempo-pending-discovery-admissions-v1';
const records=['a','b','c','d'].map((digit,index)=>({opportunityId:digit.repeat(64),selectedMoveUci:'g1f3',evidenceFingerprint:`installed-ui-only-${index}`,operationId:`00000000-0000-4000-8000-${String(index+1).padStart(12,'0')}`,state:'failed',error:'Installed presentation check only'}));
try{
 await page.goto(applicationUrl.href,{waitUntil:'domcontentloaded',timeout:20000});
 await page.evaluate(({key,saved})=>localStorage.setItem(key,JSON.stringify(saved)),{key:storageKey,saved:records});
 await page.reload({waitUntil:'domcontentloaded'});
 const details=page.locator('details.pending-discovery-saves');
 const trigger=details.locator('summary');await trigger.waitFor({timeout:15000});
 assert.equal((await trigger.innerText()).trim(),'Saves (4)');
 assert.equal(await details.getAttribute('open'),null);
 const panel=page.getByRole('region',{name:'Saved discovery requests'});assert.equal(await panel.isVisible(),false);
 await trigger.click();assert(await panel.isVisible());assert.equal(await panel.getByRole('listitem').count(),4);
 const geometry=await panel.evaluate(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,viewportWidth:innerWidth,viewportHeight:innerHeight,overflowY:getComputedStyle(e).overflowY}});
 assert(geometry.x>=7.5&&geometry.right<=312.5);assert(geometry.width<=304.5);assert(geometry.bottom<=568-80);assert.equal(geometry.overflowY,'auto');
 await page.screenshot({path:root+'/installed-ui-mobile-expanded.png',fullPage:true});
 await trigger.click();assert.equal(await details.getAttribute('open'),null);
 await page.reload({waitUntil:'domcontentloaded'});await trigger.waitFor();assert.equal(await details.getAttribute('open'),null);
 assert.equal(await page.evaluate(key=>localStorage.getItem(key),storageKey),JSON.stringify(records));
 await page.screenshot({path:root+'/installed-ui-mobile-collapsed.png',fullPage:true});
 await page.setViewportSize({width:1280,height:900});await trigger.click();
 const desktop=await panel.boundingBox();assert(desktop&&desktop.width<=420.5);
 assert.equal(errors.length,0);
 const result={checked_at:new Date().toISOString(),candidate:prepared.revision,serviceWorkers:'block',isolated_synthetic_browser_storage:true,live_data_mutations:false,collapsed_by_default:true,full_four_failed_records_retained:true,retained_on_reload:true,mobile:geometry,desktop,all_writes_and_queue_issuance_blocked:blocked,page_errors:errors,user_browser_untouched:true};
 writeFileSync(root+'/installed-ui-fix.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({installed_ui_fix_verified:true,mobile:geometry,desktop_width:desktop.width,page_errors:errors.length}));
}finally{await context.close();}
}finally{await browser.close();}
