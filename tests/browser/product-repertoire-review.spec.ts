import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import type { APIRequestContext, APIResponse } from "@playwright/test";
import { test, expect, api, nav } from "./product-fixtures";
import { verifyPostgresFixtureTarget } from "./product-fixture-reset";

async function confirmed(request: APIRequestContext,response: APIResponse) {
  const value = await response.json();
  if (response.status()!==202) {expect(response.ok(),JSON.stringify(value)).toBeTruthy();return value;}
  expect(value.operation_id).toBeTruthy();
  let receipt:{state:string;response?:unknown};
  await expect.poll(async()=>{
    receipt=await(await request.get(`${api}/operations/${value.operation_id}`)).json();
    return receipt.state;
  },{timeout:30_000}).toBe("complete");
  return receipt!.response as {repertoire_id:string};
}

async function fixture(request:APIRequestContext,action:"seed"|"readback",repertoireId:string) {
  const health=await(await request.get(`${api}/health`)).json();
  const project=process.env.TEMPO_TEST_COMPOSE_PROJECT;
  await verifyPostgresFixtureTarget({health,apiUrl:api,project});
  const service=action==="seed"?"foreground-worker":"api";
  const metadata=JSON.parse(execFileSync("docker",["inspect","--format",
    '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}',`${project}-${service}-1`],{encoding:"utf8",timeout:5000}));
  expect(metadata.labels["com.docker.compose.project"]).toBe(project);
  expect(metadata.labels["com.docker.compose.service"]).toBe(service);
  expect(metadata.id).toMatch(/^[a-f0-9]{12,64}$/);
  const result=spawnSync("docker",["exec","-i",metadata.id,"python","-",action,repertoireId],{
    input:readFileSync("backend/tests/fixtures/product_repertoire.py","utf8"),encoding:"utf8",timeout:15_000,maxBuffer:256*1024,
  });
  expect(result.status,result.stderr).toBe(0);
  return action==="readback"?JSON.parse(result.stdout):null;
}

async function importPgn(request:APIRequestContext,name:string,pgn:string) {
  const imported=await confirmed(request,await request.post(`${api}/imports/pgn`,{multipart:{
    file:{name,mimeType:"application/x-chess-pgn",buffer:Buffer.from(pgn)},trained_color:"white",initial_depth:"2",
  }}));
  await expect.poll(async()=>(await(await request.get(`${api}/repertoires`)).json()).repertoires
    .find((item:{id:string})=>item.id===imported.repertoire_id)?.graph_state,{timeout:30_000}).toBe("ready");
  return imported.repertoire_id as string;
}

test("C exact conflict is enforced and manually resolved while unrelated issued Study and history survive",async({page,request})=>{
  const unrelated=await importPgn(request,"product-repertoire-unrelated.pgn",'[Event "Task unrelated"]\n\n1. Nf3 d5 2. g3 Nf6 *');
  let head:{id:string;repertoire_id:string};
  await expect.poll(async()=>{
    head=(await(await request.get(`${api}/queue/today`)).json()).cards[0];
    return head?.repertoire_id;
  },{timeout:30_000}).toBe(unrelated);
  const repertoire=await importPgn(request,"product-repertoire-review.pgn",'[Event "Task open"]\n\n1. e4 e5 2. Nf3 Nc6 *\n\n[Event "Task opponent fork"]\n\n1. e4 c5 2. Nf3 d6 *');
  const before=await fixture(request,"readback",repertoire);
  await fixture(request,"seed",repertoire);
  await page.goto("/");await nav(page,"Repertoire");
  await page.getByRole("button",{name:"Audit own responses"}).click();
  await page.getByLabel("Audit repertoire").selectOption(repertoire);
  await expect(page.getByRole("heading",{name:"Competing own responses"})).toBeVisible();
  await page.getByRole("button",{name:"Apply current conflict audit"}).click();
  const reviewUrl=`${api}/product/repertoires/${repertoire}/review`;
  await expect.poll(async()=>{
    const value=await(await request.get(reviewUrl)).json();
    return value.conflicts[0]?.study_block.status;
  },{timeout:30_000}).toBe("blocked");
  await page.getByRole("button",{name:"Refresh review"}).click();
  await expect(page.getByText(/Study block: blocked/)).toBeVisible();
  const current=await(await request.get(reviewUrl)).json();
  const proposed=current.conflicts[0].choices.find((choice:{move_uci:string})=>choice.move_uci==="e2e4").proposal;
  const preview=await confirmed(request,await request.post(`${api}/product/repertoires/${repertoire}/changes/preview`,{data:proposed}));
  expect(preview.status).toBe("preview");
  const stale=await request.post(`${api}/product/repertoires/${repertoire}/changes/decide`,{data:{
    proposal_id:proposed.proposal_id,expected_proposal_revision:proposed.proposal_revision+1,
    expected_source_revision:proposed.source_revision,display_digest:proposed.display_digest,action:"accept",edited_proposal:null,
  }});
  // A 202 is still pending: resolve the actual command result before checking a stale decision.
  if(stale.status()===202) {
    const operation=(await stale.json()).operation_id;
    await expect.poll(async()=>(await(await request.get(`${api}/operations/${operation}`)).json()).state,{timeout:30_000}).toBe("failed");
  } else expect(stale.status()).toBe(409);
  await page.getByRole("button",{name:"Choose e2e4"}).click();
  await page.getByRole("button",{name:"Preview proposal",exact:true}).click();
  const approval=page.getByLabel("I approve these repertoire changes and all associated card changes.");
  await expect.poll(async()=>{
    if(await approval.isEnabled())return true;
    const retry=page.getByRole("button",{name:"Preview proposal",exact:true});
    if(await retry.isEnabled())await retry.click();
    return false;
  },{timeout:30_000}).toBe(true);
  await approval.check();await page.getByRole("button",{name:"Accept displayed changes"}).click();
  await expect.poll(async()=>{
    const retry=page.getByRole("button",{name:"Retry pending operation"});
    if(await retry.isVisible()&&await retry.isEnabled())await retry.click();
    return (await(await request.get(reviewUrl)).json()).conflicts.length;
  },{timeout:30_000}).toBe(0);
  const after=await fixture(request,"readback",repertoire);
  expect(after.reviews).toEqual(before.reviews);
  for(const card of before.cards)expect(after.cards).toContainEqual(card);
  for(const line of before.lines)expect(after.lines).toContainEqual(line);
  const queue=(await(await request.get(`${api}/queue/today`)).json()).cards;
  expect(queue[0].id).toBe(head!.id);
  await page.reload();await nav(page,"Repertoire");
  await page.getByRole("button",{name:"Audit own responses"}).click();
  await page.getByLabel("Audit repertoire").selectOption(repertoire);
  await expect(page.getByRole("heading",{name:"Competing own responses"})).toHaveCount(0);
  await nav(page,"Train");await expect(page.getByRole("button",{name:"Repertoire library"})).toHaveCount(0);
});

test("C rejects an illegal PGN tail before any repertoire activation",async({request})=>{
  const identities=async()=>(await(await request.get(`${api}/repertoires`)).json()).repertoires
    .map((item:{id:string})=>item.id).sort();
  const before=await identities();
  const rejected=await request.post(`${api}/imports/pgn`,{multipart:{
    file:{name:"product-repertoire-illegal.pgn",mimeType:"application/x-chess-pgn",
      buffer:Buffer.from('[Event "Task illegal tail"]\n\n1. e4 e5 2. e4 *')},
    trained_color:"white",initial_depth:"2",
  }});
  expect(rejected.status()).toBe(422);
  expect(typeof(await rejected.json()).detail).toBe("string");
  expect(await identities()).toEqual(before);
});
