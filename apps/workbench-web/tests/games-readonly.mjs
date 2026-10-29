import { sharedBoardAppearance } from './board-appearance.mjs';
/** The same shipped browser, authenticated reads, no second renderer or live origin. */
import assert from 'node:assert/strict';
import {expect} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

export async function runGamesOnlyScenario(browser,origin,fixtures,evidence){
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1280,height:900}});
  const traffic=[];const unexpected=[];let page;
  try{
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==origin){unexpected.push('external '+url.origin);return route.abort();}
      if(!url.pathname.startsWith('/v1/'))return route.continue();
      traffic.push({method:request.method(),path:url.pathname,query:url.search});
      try{
        assert.equal(request.method(),'GET');assert.equal(request.headers().authorization,'Bearer synthetic-caller');assert.equal(request.headers()['x-tempo-workspace'],'fixture');
        let body;
        if(url.pathname==='/v1/analysis/bootstrap')body={...fixtures.bootstrap,capabilities:[...fixtures.bootstrap.capabilities,'games-readonly-v1']};
        else if(url.pathname==='/v1/analysis/game-scopes')body={schema_version:1,workspace_id:'fixture',items:fixtures.runs.items.map(run=>({run_id:run.run_id,revision:run.revision,input_id:run.scope.input_id,input_revision:run.scope.input_revision}))};
        else if(url.pathname==='/v1/analysis/game-scopes/run')body={schema_version:1,workspace_id:'fixture',run_id:'run',revision:1,input_id:fixtures.games.input_id,input_revision:fixtures.games.input_revision};
        else if(url.pathname==='/v1/analysis/runs/run/games')body=fixtures.games;
        else throw new Error('Unexpected viewer request');
        assert.ok(body);await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
      }catch(error){unexpected.push(error.message);await route.abort();}
    });
    page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>unexpected.push(error.message));
    await page.goto(origin+'/?run=run&boardTheme=blue&pieceSet=merida');
    await page.getByLabel('Intelligence origin',{exact:true}).fill(origin);await page.getByLabel('Workspace',{exact:true}).fill('fixture');await page.getByLabel('Caller credential',{exact:true}).fill('synthetic-caller');
    await page.getByRole('button',{name:'Connect',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Selected games',exact:true})).toBeVisible();
    await expect(page.getByText("These comparisons belong to the accepted input snapshot, not today's live repertoire.",{exact:false})).toBeVisible();
    for(const name of ['Start analysis · 250 decisions','Continue · next 250 decisions','Retry','Cancel','Resume','Dismiss'])await expect(page.getByRole('button',{name,exact:true})).toHaveCount(0);
    const game=fixtures.games.items.find(game=>game.contexts.some(context=>context.status==='unknown'))??fixtures.games.items[0];assert.ok(game);
    await page.getByRole('button',{name:'Inspect game '+(game.provider_game_id??game.game_id),exact:true}).click();
    await expect(page.getByLabel('Game chessboard',{exact:true})).toBeVisible();await sharedBoardAppearance(page,'Game chessboard');
    await page.getByRole('button',{name:'Next position',exact:true}).click();
    assert.equal(await page.getByLabel('Game position',{exact:true}).inputValue(),'1');
    await page.screenshot({path:resolve(evidence,'games-readonly-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:resolve(evidence,'games-readonly-phone.png'),fullPage:true});
    await page.reload();await expect(page.getByRole('heading',{name:'Selected games',exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'Start analysis · 250 decisions',exact:true})).toHaveCount(0);
    assert.deepEqual(unexpected,[]);assert.ok(traffic.length>0&&traffic.every(request=>request.method==='GET'));
    await writeFile(resolve(evidence,'games-readonly.json'),JSON.stringify({passed:true,traffic,unexpected},null,2)+'\n');
    return 'Games_readonly_existing_scope_replay_reload_no_mutations';
  }finally{await context.close();}
}
