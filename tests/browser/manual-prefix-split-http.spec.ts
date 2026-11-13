import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { Chess } from 'chess.js';
import type { APIRequestContext, APIResponse, Page } from '@playwright/test';
import { test, expect } from './observability';
import { api, move } from './product-fixtures';
import { verifyPostgresFixtureTarget, postgresFixtureComposeArguments } from './product-fixture-reset';
import { boardBounds, navigate, prepareUI } from './ui-fixtures';

async function confirmed(request: APIRequestContext, response: APIResponse, originalId?: string) {
  expect(response.ok(), `command HTTP ${response.status()}`).toBe(true);
  const first = await response.json();
  if (response.status() !== 202) return first;
  const id = originalId ?? first.operation_id;
  expect(first.operation_id).toBe(id);
  let result: unknown;
  await expect.poll(async () => {
    const read = await request.get(`${api}/operations/${encodeURIComponent(id)}`);
    expect(read.ok()).toBe(true);
    const receipt = await read.json();
    expect(receipt.operation_id).toBe(id);
    if (receipt.state === 'failed' || receipt.state === 'blocked') throw new Error(`Original command ${id}: ${receipt.state}`);
    if (receipt.state === 'complete') result = receipt.response;
    return receipt.state;
  }, { timeout: 60_000 }).toBe('complete');
  return result;
}

async function withOwnedPrefix(page: Page, request: APIRequestContext, action: (owned: {
  card: string; entry: number; repertoire: string; snapshot: () => any; track: (id: string) => void;
}) => Promise<void>) {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const health = await request.get(`${api}/health`);
  await verifyPostgresFixtureTarget({ health: await health.json(), apiUrl: api, project });
  const compose = postgresFixtureComposeArguments(project);
  const filename = `p12-split-${randomUUID().replaceAll('-', '')}.pgn`;
  const importId = `p12-import-${randomUUID()}`;
  const imported = await confirmed(request, await request.post(`${api}/imports/pgn`, { headers: {'Idempotency-Key':importId}, multipart: {
    file: { name: filename, mimeType: 'application/x-chess-pgn', buffer: Buffer.from('[Event "P12 owned split"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *') },
    trained_color: 'white', initial_depth: '2',
  }}), importId);
  const repertoire = (imported as {repertoire_id: string}).repertoire_id;
  expect(repertoire).toMatch(/^[a-zA-Z0-9_-]+$/);
  let card = 'auto'; let day = ''; let displaced: number[][] = []; const operations: string[] = [importId];
  const fixture = (actionName: string, ...extra: string[]) => JSON.parse(execFileSync('docker', [...compose, 'run', '--rm', '--no-deps', '-e', 'TEMPO_TEST_INSTANCE=disposable', 'schema', 'python', '-', actionName, filename, repertoire, card, day, ...extra], {
    input: requireFixture(), encoding: 'utf8', timeout: 30_000,
  }));
  let originalFailure: unknown;
  try {
    const settingsResponse = await request.get(`${api}/settings`);
    const settings = await settingsResponse.json();
    day = new Intl.DateTimeFormat('en-CA', {timeZone: settings.timezone === 'local' ? process.env.TZ : settings.timezone, year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
    const settingsId = `p12-settings-${randomUUID()}`;
    await confirmed(request, await request.put(`${api}/repertoires/${repertoire}/settings`, {headers:{'Idempotency-Key':settingsId},data: {new_cards_per_day: 2}}), settingsId);
    operations.push(settingsId);
    let ready: {ready: boolean; card?: string; entry?: number; displaced?: number[][]} = {ready:false};
    await expect.poll(() => { ready = fixture('prepare'); return ready.ready; }, {timeout:60_000}).toBe(true);
    card = ready.card!; displaced = ready.displaced!;
    const entry = ready.entry!;
    const snapshot = () => fixture('snapshot');
    await prepareUI(page); await navigate(page, 'Train');
    await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id', String(entry));
    await expect(page.getByRole('button', {name:'Accept', exact:true})).toBeVisible();
    await action({card, entry, repertoire, snapshot, track: id => operations.push(id)});
  } catch (error) { originalFailure = error; throw error; } finally {
    try {
      if (!day) throw new Error('Original fixture Study day unavailable; preserve for inspection');
      const cleanup = fixture('cleanup', JSON.stringify(displaced), JSON.stringify(operations));
      expect(cleanup.cleaned).toBe(true);
      expect(cleanup.restored + cleanup.reordered + cleanup.removed).toBe(displaced.length);
      expect(Object.values(cleanup.remaining).every(value => value === 0)).toBe(true);
    } catch (error) {
      if (originalFailure === undefined) throw error;
      throw new Error(`Original split failure: ${originalFailure instanceof Error ? originalFailure.stack : String(originalFailure)}\n\nFixture cleanup failure: ${error instanceof Error ? error.stack : String(error)}`);
    }
  }
}

function requireFixture() {
  return readFileSync('tests/fixtures/postgres-manual-prefix-browser.py', 'utf8');
}

test('real manual split accepts original issued review and replays its exact receipt', async ({page, request}, testInfo) => {
  test.setTimeout(180_000);
  await withOwnedPrefix(page, request, async ({card, entry, snapshot, track}) => {
    const preview = await request.get(`${api}/cards/${card}/prefix-split`);
    expect(preview.ok()).toBe(true);
    const proposed = await preview.json();
    expect(proposed).toMatchObject({source_card_id:card, applied:false});
    const pendingResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/cards/${card}/prefix-split`));
    await page.getByRole('button', {name:'Accept', exact:true}).click();
    const sent = await pendingResponse; const original = sent.request();
    const operationId = original.headers()['idempotency-key'];
    const body = original.postDataJSON();
    expect(operationId).toBeTruthy(); track(operationId); expect(body).toMatchObject({expected_revision:proposed.source_revision, queue_entry_id:entry});
    const acceptance = await confirmed(request, sent as unknown as APIResponse, operationId) as {status:string;intent_id:string};
    expect(acceptance).toMatchObject({status:'waiting_review'});
    await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id', String(entry));
    await expect(page.locator('.board-frame').first()).toHaveAttribute('data-input-enabled','true');
    const board = await boardBounds(page);
    await page.mouse.click(board.x+board.width*4.5/8, board.y+board.height*6.5/8);
    await page.mouse.click(board.x+board.width*4.5/8, board.y+board.height*4.5/8);
    await expect(page.getByRole('button',{name:'Correct',exact:true})).toBeEnabled();
    const reviewRequest = page.waitForRequest(event => event.method()==='POST' && event.url().endsWith(`/cards/${card}/review`));
    await page.getByRole('button',{name:'Correct',exact:true}).click();
    const review = await reviewRequest; const reviewBody = review.postDataJSON();
    const reviewOperationId = review.headers()['idempotency-key']; expect(reviewOperationId).toBeTruthy(); track(reviewOperationId);
    expect(reviewBody.queue_entry_id).toBe(entry); expect(reviewBody.expected_revision).toBe(proposed.source_revision);
    try {
      await expect.poll(() => snapshot().intent[0]?.[4], {timeout:90_000}).toBe('applied');
    } catch (original) {
      // Only this fixture's exact identities are inspected, inside the already
      // verified disposable project; diagnostic failure never replaces the poll.
      try {
        const data = execFileSync('docker', [...postgresFixtureComposeArguments(process.env.TEMPO_TEST_COMPOSE_PROJECT),
          'run','--rm','--no-deps','-e','TEMPO_TEST_INSTANCE=disposable','schema','python','-',
          card, acceptance.intent_id, operationId, reviewOperationId], {
          input: readFileSync('tests/fixtures/postgres-manual-prefix-diagnostics.py','utf8'), encoding:'utf8', timeout:10_000,
        });
        await testInfo.attach('split-operation-diagnostics', {body:data,contentType:'application/json'});
        const logs = execFileSync('docker', [...postgresFixtureComposeArguments(process.env.TEMPO_TEST_COMPOSE_PROJECT),
          'logs','--no-color','--tail','300','foreground-worker','background-scheduler'], {encoding:'utf8',timeout:10_000});
        const lines = logs.split('\n').filter(line => line.includes('recover_study_operations') ||
          line.includes('execute_foreground_command') || line.includes(`prefix-split-intent:${acceptance.intent_id}`)).slice(-100).map(line => line.slice(0, 2048));
        await testInfo.attach('split-recovery-routing', {body:lines.join('\n'),contentType:'text/plain'});
      } catch (diagnosticError) {
        await testInfo.attach('split-diagnostic-failure', {body:String(diagnosticError),contentType:'text/plain'}).catch(() => undefined);
      }
      throw original;
    }
    const after = snapshot();
    expect(after.split).toHaveLength(1); expect(after.intent).toHaveLength(1);
    expect(after.intent[0].slice(0, 6)).toEqual([acceptance.intent_id, card, proposed.source_revision, entry, 'applied', reviewBody.attempt_id]);
    expect(after.intent[0][6]).toBe(after.receipts[0][2]);
    expect(after.intent[0][7]).toBe(`prefix-split-intent:${acceptance.intent_id}`);
    expect(after.intent[0][8]).toMatchObject({source_card_id:card, source_revision:proposed.source_revision, applied:true,
      parent:{card_id:after.split[0][2]}, continuation:{card_id:after.split[0][3]}});
    expect(new Set(after.queue.map((row: [number]) => row[0])).size).toBe(after.queue.length);
    expect(after.receipts).toHaveLength(1); expect(after.receipts[0][0]).toBe(reviewBody.attempt_id); expect(after.receipts[0][1]).toBe(entry);
    expect(after.origins).toContainEqual([entry,card,proposed.source_revision,1]);
    const originalReceipt = await (await request.get(`${api}/operations/${encodeURIComponent(operationId)}`)).json();
    expect(originalReceipt).toMatchObject({operation_id:operationId,state:'complete',response:acceptance});
    await page.reload();
    const replay = await request.post(`${api}/cards/${card}/prefix-split`, {headers:{'Idempotency-Key':operationId},data:body});
    expect(await confirmed(request,replay,operationId)).toEqual(acceptance);
    expect(snapshot()).toEqual(after);
    let successor: {queue_entry_id:number; start_fen:string; moves:string[]; trained_color:string} | undefined;
    await expect.poll(async () => {
      const queue = await request.get(`${api}/queue/window?limit=20`);
      if (!queue.ok() || queue.status() === 202) return false;
      successor = (await queue.json()).cards?.[0];
      return Boolean(successor && successor.queue_entry_id !== entry);
    }, {timeout:60_000}).toBe(true);
    await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id', String(successor!.queue_entry_id));
    const boardFrame = page.locator('.board-frame').first();
    await expect(boardFrame).toHaveAttribute('data-input-enabled','true');
    // The UI may already have played an opponent move from the original line.
    // Match its exact rendered position before selecting the next player move.
    let initialFen = ''; let expectedMove = '';
    await expect.poll(async () => {
      const rendered = await boardFrame.getAttribute('data-fen');
      if (!rendered) return false;
      const chess = new Chess(successor!.start_fen);
      for (const uci of successor!.moves) {
        if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(uci)) throw new Error('Successor has an invalid original move');
        if (chess.fen() === rendered && chess.turn() === (successor!.trained_color === 'white' ? 'w' : 'b')) {
          if (!chess.move({from:uci.slice(0,2), to:uci.slice(2,4), ...(uci[4] ? {promotion:uci[4]} : {})})) throw new Error('Successor move is not legal');
          initialFen = rendered; expectedMove = uci; return true;
        }
        chess.move({from:uci.slice(0,2), to:uci.slice(2,4), ...(uci[4] ? {promotion:uci[4]} : {})});
      }
      return false;
    }, {timeout:10_000, message:'Rendered successor must match its original line and offer a legal player move'}).toBe(true);
    await move(page, expectedMove.slice(0,2), expectedMove.slice(2,4));
    await expect(boardFrame).not.toHaveAttribute('data-fen', initialFen);
  });
});

test('real manual split rejection is retained on the original offered card', async ({page, request}) => {
  test.setTimeout(180_000);
  await withOwnedPrefix(page, request, async ({card, entry, snapshot, track}) => {
    const before = snapshot();
    const rejection = page.waitForResponse(response => response.request().method()==='POST' && response.url().endsWith(`/cards/${card}/prefix-split/reject`));
    await page.getByRole('button',{name:'Reject',exact:true}).click();
    const response = await rejection; const operationId = response.request().headers()['idempotency-key'];
    expect(operationId).toBeTruthy(); track(operationId);
    const result = await confirmed(request,response as unknown as APIResponse,operationId) as {rejected_after_review_id:number};
    expect(result.rejected_after_review_id).toBeGreaterThan(0);
    await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id',String(entry));
    await page.reload(); await expect(page.getByRole('button',{name:'Accept',exact:true})).toHaveCount(0);
    const after = snapshot(); expect(after.split).toEqual(before.split); expect(after.receipts).toEqual(before.receipts);
    expect(after.reviews).toEqual(before.reviews); expect(after.origins).toEqual(before.origins);
  });
});
