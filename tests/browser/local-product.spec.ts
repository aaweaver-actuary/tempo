import { test, expect, type Page } from "@playwright/test";
import { Chess } from "chess.js";

const api = process.env.TEMPO_DOCKER_URL ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api";
const pgn = '[Event "My repertoire"]\n\n1. e4 e5 2. Nf3 Nc6 (2... Nf6) *\n\n[Event "Other line"]\n\n1. d4 d5 2. c4 e6 *';
const sourceFen = "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17";
const puzzles = [1,2].map(n => ({ PuzzleId:`browser-mate-${n}`,DeckId:"hangingPiece-easy",DeckPosition:n,FEN:sourceFen,Moves:"e8d7 a2e6 d7d8 f7f8",Rating:900 }));

async function nav(page: Page, name: string) { await page.getByRole("navigation").getByRole("button", {name,exact:true}).click(); }
async function boardVisible(page: Page) {
  const board=page.locator(".board-frame").first();
  await expect(board).toBeVisible();
  const box=await board.boundingBox(); const viewport=page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.y).toBeGreaterThanOrEqual(65);
  expect(box!.x+box!.width).toBeLessThanOrEqual(viewport.width+1);
  expect(box!.y+box!.height).toBeLessThanOrEqual(viewport.height+1);
  expect(Math.abs(box!.width-box!.height)).toBeLessThan(2);
  const controls=page.locator(".board-tools").first();
  if (await controls.count()) {
    const controlsBox=(await controls.boundingBox())!;
    expect(controlsBox.y+controlsBox.height).toBeLessThanOrEqual(viewport.height+1);
  }
}
async function move(page: Page, from: string, to: string) {
  const board=page.locator(".board-frame").first();
  await expect(board).toBeVisible();
  const box=(await board.boundingBox())!;
  const black=(await board.getAttribute("data-orientation"))==="black";
  for(const square of [from,to]) {
    const file=square.charCodeAt(0)-97,rank=Number(square[1])-1;
    await page.mouse.click(box.x+((black?7-file:file)+.5)*box.width/8,box.y+((black?rank:7-rank)+.5)*box.height/8);
  }
}

test.beforeEach(async ({request}) => {
  const repertoires=(await (await request.get(`${api}/repertoires`)).json()).repertoires;
  for(const item of repertoires) await request.delete(`${api}/repertoires/${item.id}`);
  const queue=(await (await request.get(`${api}/queue/today`)).json()).cards;
  for(const card of queue) if(card.content_type !== "opening") await request.delete(`${api}/cards/${card.id}`);
  const settings=await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`,{data:{...settings,new_cards_per_day:2,initial_depth:2,lichess_username:"",chesscom_username:""}});
});

test("local import respects the daily limit; Black prompts and Builder flip survive Settings and refresh",async ({page}) => {
  await page.goto("/"); await nav(page,"Repertoire");
  await page.getByRole("button",{name:"＋ Import PGN"}).click();
  await page.locator('input[type="file"]').setInputFiles({name:"mine.pgn",mimeType:"application/x-chess-pgn",buffer:Buffer.from(pgn)});
  await page.getByRole("dialog").getByRole("button",{name:"Black",exact:true}).click();
  await page.getByRole("button",{name:"Import repertoire",exact:true}).click();
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible();
  await page.getByRole("button",{name:"View imported repertoire"}).click();
  await nav(page,"Train"); await expect(page.locator(".session-count strong")).toHaveText("2");
  await expect.poll(async()=>new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).turn()).toBe("b");
  await boardVisible(page); await nav(page,"Builder");
  const selector=page.getByRole("combobox",{name:"Active repertoire"}); await expect(selector).not.toHaveValue("");
  const selected=await selector.inputValue(); await move(page,"e2","e4");
  await expect.poll(async()=>new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).get("e4")?.type).toBe("p");
  const fen=await page.locator(".board-frame").getAttribute("data-fen");
  await page.keyboard.press("f"); await expect(selector).toHaveValue(selected);
  await nav(page,"Settings"); await nav(page,"Builder");
  await expect(selector).toHaveValue(selected); await expect(page.locator(".board-frame")).toHaveAttribute("data-fen",fen!);
  await page.reload(); await nav(page,"Builder"); await expect(selector).toHaveValue(selected);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen",fen!);
  await page.setViewportSize({width:390,height:844}); await boardVisible(page);
});

test("sample deletion uses repertoire identity and does not delete its same-filename sibling",async ({page,request}) => {
  for(const color of ["white","black"]) await request.post(`${api}/imports/pgn`,{multipart:{file:{name:"Tempo examples.pgn",mimeType:"application/x-chess-pgn",buffer:Buffer.from(pgn)},trained_color:color,initial_depth:"2"}});
  await page.goto("/"); await nav(page,"Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(2);
  page.on("dialog",dialog=>dialog.accept()); await page.locator(".repertoire-card").first().getByRole("button",{name:"Delete",exact:true}).click();
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
  await nav(page,"Builder"); await nav(page,"Repertoire"); await page.reload(); await nav(page,"Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
});

test("wrong tactic immediately shows X, requires guided continuation, and leaves final mate during the pause",async ({page}) => {
  await page.route("**/data/tactics-decks.json",route=>route.fulfill({json:puzzles}));
  await page.goto("/"); await nav(page,"Tactics"); await expect(page.getByText("Puzzle 1 of 100")).toBeVisible();
  await move(page,"a3","b4"); await expect(page.locator(".outcome-flash")).toBeVisible();
  await page.waitForTimeout(850); await expect(page.getByText("Puzzle 1 of 100")).toBeVisible();
  await move(page,"a2","e6");
  await expect.poll(async()=>new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).get("e6")?.type).toBe("b");
  await page.waitForTimeout(200); await move(page,"f7","f8");
  await expect.poll(async()=>new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).isCheckmate()).toBe(true);
  await page.waitForTimeout(200); expect(new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).isCheckmate()).toBe(true);
  await expect(page.getByText("Puzzle 2 of 100")).toBeVisible();
  await page.setViewportSize({width:390,height:844}); await boardVisible(page);
});

test("Docker Games shows actual empty records and actionable sync errors, never sample success",async ({page}) => {
  await page.goto("/"); await nav(page,"Games");
  await expect(page.getByText("No games imported",{exact:true})).toBeVisible();
  await expect(page.getByText(/private Site|comparison preview|Sample comparisons/)).toHaveCount(0);
  await page.getByRole("button",{name:"↻ Sync games"}).click();
  await expect(page.getByRole("alert")).toContainText(/username|account/i);
});

test("automatic game sync has a visible spinner and reports provider failure",async ({page,request})=>{
  const settings=await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`,{data:{...settings,lichess_username:"missing-user"}});
  await page.route("**/api/games/sync",async route=>{await new Promise(resolve=>setTimeout(resolve,1500)); await route.fulfill({status:404,json:{detail:"Lichess username not found"}});});
  await page.goto("/"); await nav(page,"Games");
  await expect(page.getByRole("button",{name:"Syncing games"})).toBeDisabled();
  await expect(page.locator(".sync-button i")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Lichess username not found");
});

test("production Stockfish returns playable engine moves without clipping the board",async ({page})=>{
  await page.addInitScript(()=>{localStorage.setItem("tempo-stockfish-on","true");localStorage.setItem("tempo-maia-on","false");});
  await page.goto("/"); await nav(page,"Builder");
  await expect(page.locator(".engine-panel .candidate-list button").first()).toBeVisible({timeout:45_000});
  await boardVisible(page);
});

test("Maia initializes matching runtime assets and returns legal playable probabilities",async ({page})=>{
  const runtime=await page.request.get("/ort/ort-wasm-simd-threaded.mjs");
  expect(runtime.ok()).toBeTruthy();
  expect(runtime.headers()["content-type"]).toMatch(/javascript/);
  await page.addInitScript(()=>{localStorage.setItem("tempo-stockfish-on","false");localStorage.setItem("tempo-maia-on","true");});
  await page.goto("/"); await nav(page,"Builder");
  const panel=page.locator(".analysis-panel").filter({hasText:"Maia 3"});
  const candidate=panel.locator(".candidate-list button").first();
  await expect(candidate).toBeVisible({timeout:45_000});
  await expect(candidate.locator("small")).toContainText(/\d+%/);
  const startingFen=await page.locator(".board-frame").getAttribute("data-fen");
  await candidate.click();
  await expect(page.locator(".board-frame")).not.toHaveAttribute("data-fen",startingFen!);
  await boardVisible(page);
});

test("real packaged tactics and standard chess sounds are readable and preloaded before opening Tactics", async ({page,request}) => {
  const catalog = await request.get("/data/tactics-decks.json");
  expect(catalog.ok()).toBeTruthy();
  expect((await catalog.json()).filter((record: { DeckId: string }) => record.DeckId === "hangingPiece-easy")).toHaveLength(100);
  for (const path of ["Move", "Capture"]) expect((await request.get(`/sounds/standard/${path}.mp3`)).ok()).toBeTruthy();
  let requests = 0;
  const progress = await (await request.get(`${api}/tactics/progress`)).json();
  const expectedPuzzle = (progress["hangingPiece:easy"]?.index ?? 0) + 1;
  page.on("request", request => { if (request.url().includes("/data/tactics-decks.json")) requests++; });
  await page.goto("/");
  await expect.poll(() => requests).toBe(1);
  await nav(page,"Tactics");
  await expect(page.locator(".board-frame")).toBeVisible();
  await expect(page.getByText(`Puzzle ${expectedPuzzle} of 100`)).toBeVisible();
  expect(requests).toBe(1);
  await boardVisible(page);
});

test("Builder source comparison is immediately reachable beside the board", async ({page}) => {
  await page.addInitScript(() => localStorage.setItem("tempo-stockfish-on", "true"));
  await page.goto("/"); await nav(page,"Builder");
  const comparison=page.getByRole("table", {name:"Move source comparison"});
  await expect(comparison).toBeVisible();
  for (const name of ["Stockfish","Maia","Lichess","Masters"]) await expect(comparison.getByRole("columnheader", {name,exact:true})).toBeVisible();
  await expect(comparison.getByRole("button").first()).toBeVisible({timeout:45_000});
  await boardVisible(page);
});

test("help remains Again after browser reload and the returned attempt is unassisted",async ({page,request})=>{
  await request.post(`${api}/imports/pgn`,{multipart:{file:{name:"help.pgn",mimeType:"application/x-chess-pgn",buffer:Buffer.from('1. e4 e5 2. Nf3 Nc6 *')},initial_depth:"2"}});
  await page.goto("/"); await expect(page.locator(".board-frame")).toBeVisible();
  await page.getByRole("button",{name:/Show move/}).click();
  await expect.poll(async()=> (await (await request.get(`${api}/queue/today`)).json()).cards[0].attempt_failed).toBe(1);
  await page.reload(); await expect(page.locator(".outcome-flash.wrong")).toBeVisible();
  await expect(page.getByRole("button",{name:"Finish on the board",exact:true})).toBeDisabled();
  await move(page,"e2","e4");
  await expect.poll(async()=>new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).get("e5")?.type).toBe("p");
  await page.waitForTimeout(200); await move(page,"g1","f3");
  await expect.poll(async()=>new Chess((await page.locator(".board-frame").getAttribute("data-fen"))!).get("f3")?.type).toBe("n");
  await expect.poll(async()=> (await (await request.get(`${api}/queue/today`)).json()).cards[0].attempt_failed).toBe(0);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-hint","false");
});
