import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {expect} from '@playwright/test';

/** Real browser storage and UI; every service request is explicitly intercepted. */
export async function runCreationScenarios(browser,origin,fixtures,evidence){
  const outcomes=[];
  for(const scenario of ['response-loss','repeated-click','authority-switch']){
    const context=await browser.newContext({serviceWorkers:'block'});
    const page=await context.newPage();page.setDefaultTimeout(10000);
    const traffic=[],unexpected=[],runs=new Map();let release;
    let statusVisible=false;let originalAuthority=origin;const otherOrigin=origin.replace('127.0.0.1','localhost');
    const input=fixtures.inputs.items.find(item=>item.state==='accepted');
    assert.ok(input,'An accepted synthetic producer input is required');
    const deferred=new Promise(resolve=>{release=resolve;});
    const pendingKey='tempo-analysis-command-v2:'+JSON.stringify([origin,'fixture']);
    const result={case:'R7 browser '+scenario,passed:false,traffic,unexpected};
    page.on('pageerror',error=>unexpected.push(error.message));
    page.on('websocket',socket=>unexpected.push('WebSocket '+socket.url()));
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin===origin&&!url.pathname.startsWith('/v1/'))return route.continue();
      const entry={origin:url.origin,workspace:request.headers()['x-tempo-workspace'],method:request.method(),path:url.pathname,body:request.postData()};traffic.push(entry);
      try{
        assert.ok([origin,otherOrigin].includes(url.origin));
        assert.equal(url.search,'');
        const headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'Authorization, X-Tempo-Workspace, Content-Type'};
        if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
        const workspace=request.headers()['x-tempo-workspace'];
        assert.ok(['fixture','foreign-workspace'].includes(workspace));
        assert.equal(request.headers().authorization,'Bearer synthetic-caller');
        const reply=(body,status=200)=>route.fulfill({status,headers,contentType:'application/json',body:JSON.stringify(body)});
        if(request.method()==='POST'){
          assert.equal(url.origin,originalAuthority);assert.equal(workspace,'fixture');
          assert.equal(url.pathname,'/v1/analysis/runs');
          const body=request.postDataJSON();assert.equal(body.input_id,input.input_id);assert.equal(body.input_revision,input.revision);assert.equal(body.position_budget,250);
          const scope={expected_revision:null,policy_version:'c-analysis-v1',ranking_source:'actual',source_evidence_ids:[],ranking_evidence_id:null,...body};
          const run={schema_version:1,workspace_id:'fixture',run_id:body.run_id,revision:1,state:'pending',scope,
            derivation_version:2,recovery_required:null,progress:{pending:1},requested_generation:1,published_generation:null,last_complete_generation:null,engine_scan_complete:false};
          const previous=runs.get(body.run_id);if(previous)assert.deepEqual(previous.scope,scope);
          runs.set(body.run_id,run);
          if(scenario==='repeated-click'&&traffic.filter(item=>item.method==='POST').length===1){await deferred;statusVisible=true;return reply(run);}
          if(!statusVisible)return route.abort();
          return reply(run);
        }
        assert.equal(request.method(),'GET');
        if(url.pathname==='/v1/analysis/bootstrap')return reply({...fixtures.bootstrap,workspace_id:workspace});
        if(url.pathname==='/v1/analysis/inputs')return reply({...fixtures.inputs,workspace_id:workspace});
        if(url.pathname==='/v1/analysis/runs')return reply({schema_version:1,workspace_id:workspace,items:workspace==='fixture'&&url.origin===origin?[...runs.values()]:[]});
        const match=/^\/v1\/analysis\/runs\/([^/]+)(\/report)?$/.exec(url.pathname);
        assert.ok(match,'Unexpected service request '+url.pathname);
        assert.equal(workspace,'fixture');assert.equal(url.origin,origin);
        const run=runs.get(decodeURIComponent(match[1]));assert.ok(run);
        if(match[2])return reply({schema_version:1,workspace_id:'fixture',run_id:run.run_id,generation:null,items:[],next_cursor:null,status:'pending'});
        return statusVisible?reply(run):reply({error:'analysis_run_not_found'},404);
      }catch(error){unexpected.push(error.message);return route.abort();}
    });
    async function connect(apiOrigin=origin,workspace='fixture'){
      await page.getByLabel('Intelligence origin').fill(apiOrigin);
      await page.getByLabel('Workspace',{exact:true}).fill(workspace);
      await page.getByLabel('Caller credential').fill('synthetic-caller');
      await page.getByRole('button',{name:'Connect',exact:true}).click();
      await page.getByLabel('Imported input').waitFor();
    }
    async function disconnect(){await page.getByRole('button',{name:'Disconnect',exact:true}).click();await page.getByLabel('Intelligence origin').waitFor();}
    const start=()=>page.getByRole('button',{name:'Start analysis · 250 decisions',exact:true}).click();
    try{
      await page.goto(origin);await connect();
      await page.getByLabel('Imported input').selectOption(input.input_id+':'+input.revision);
      await start();await expect.poll(()=>runs.size).toBe(1);
      const id=[...runs.keys()][0];result.original_run_id=id;
      if(scenario==='repeated-click'){
        await start();await page.waitForTimeout(100);release();
        await expect(page.getByRole('status')).not.toHaveText('Saving analysis request…');
        assert.equal(runs.size,1,'Repeated Start generated another run identity');
        assert.equal(traffic.filter(item=>item.method==='POST').length,1);
      }else{
        await expect(page.getByRole('status')).not.toHaveText('Saving analysis request…');
        if(scenario==='response-loss'){
          await start();await page.waitForTimeout(100);
          assert.equal(runs.size,1,'Ambiguous completion generated another run identity');
          await expect(page.getByRole('status')).toContainText(/creation is unconfirmed|pending/);
        }
        const saved=await page.evaluate(key=>sessionStorage.getItem(key),pendingKey);
        assert.ok(saved?.includes(id),'Creation intent did not survive response loss');
        assert.ok(!saved.includes('synthetic-caller'));
        if(scenario==='authority-switch'){
          for(const [apiOrigin,workspace] of [[otherOrigin,'fixture'],[origin,'foreign-workspace']]){
            await disconnect();await connect(apiOrigin,workspace);await page.reload();await page.getByLabel('Imported input').waitFor();
            assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),pendingKey),saved);
            assert.equal(traffic.filter(item=>item.origin===apiOrigin&&item.workspace===workspace&&item.path.includes(id)).length,0);
          }
          await disconnect();statusVisible=true;await connect();
        }else{
          await page.reload();await page.getByLabel('Imported input').waitFor();
          assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),pendingKey),saved,'404 discarded the original creation');
          statusVisible=true;await page.reload();
        }
      }
      await expect.poll(()=>new URL(page.url()).searchParams.get('run')).toBe(id);
      assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),pendingKey),null);
      assert.equal(runs.size,1);assert.equal(traffic.filter(item=>item.method==='POST').length,1);
      assert.equal(runs.get(id).scope.position_budget,250);assert.equal(runs.get(id).scope.input_id,input.input_id);
      assert.deepEqual(unexpected,[]);result.passed=true;
    }catch(error){result.error=error.message;await writeFile(resolve(evidence,'R7-'+scenario+'-failure.html'),await page.content());await page.screenshot({path:resolve(evidence,'R7-'+scenario+'-failure.png'),fullPage:true});}
    finally{release();result.run_ids=[...runs.keys()];await context.close();outcomes.push(result);}
  }
  await writeFile(resolve(evidence,'R7-run-creation.json'),JSON.stringify(outcomes,null,2)+'\n');
  return outcomes;
}
