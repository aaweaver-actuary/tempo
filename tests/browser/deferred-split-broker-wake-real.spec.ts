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
  card: string; entry: number; repertoire: string; snapshot: () => any; wakeSnapshot: (acceptance: string, review: string, intent: string) => any; track: (id: string) => void;
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
    await action({card, entry, repertoire, snapshot, wakeSnapshot: (acceptance, review, intent) => fixture('wake-snapshot', acceptance, review, intent), track: id => operations.push(id)});
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

test('real broker wakes committed original split child before periodic grace and fences duplicate', async ({page, request}, testInfo) => {
  test.setTimeout(180_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const compose = postgresFixtureComposeArguments(project);
  await withOwnedPrefix(page, request, async ({card, entry, wakeSnapshot, track}) => {
    const preview = await request.get(`${api}/cards/${card}/prefix-split`);
    expect(preview.ok()).toBe(true);
    const proposed = await preview.json();
    const pending = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/cards/${card}/prefix-split`));
    await page.getByRole('button', {name:'Accept', exact:true}).click();
    const acceptanceResponse = await pending;
    const acceptanceId = acceptanceResponse.request().headers()['idempotency-key'];
    expect(acceptanceId).toBeTruthy(); track(acceptanceId);
    const accepted = await confirmed(request, acceptanceResponse as unknown as APIResponse, acceptanceId) as {status:string;intent_id:string};
    expect(accepted).toMatchObject({status:'waiting_review'});
    const board = await boardBounds(page);
    await page.mouse.click(board.x+board.width*4.5/8, board.y+board.height*6.5/8);
    await page.mouse.click(board.x+board.width*4.5/8, board.y+board.height*4.5/8);
    await expect(page.getByRole('button',{name:'Correct',exact:true})).toBeEnabled();
    const reviewResponse = page.waitForResponse(response => response.request().method()==='POST' && response.url().endsWith(`/cards/${card}/review`));
    await page.getByRole('button',{name:'Correct',exact:true}).click();
    const saved = await reviewResponse;
    const reviewId = saved.request().headers()['idempotency-key'];
    const reviewBody = saved.request().postDataJSON();
    expect(reviewId).toBeTruthy(); track(reviewId);
    const savedReview = await confirmed(request, saved as unknown as APIResponse, reviewId) as {persisted:boolean;review_id:number};
    expect(savedReview).toMatchObject({persisted:true,review_id:expect.any(Number)});
    expect(reviewBody).toMatchObject({queue_entry_id:entry,expected_revision:proposed.source_revision});
    const child = `prefix-split-intent:${accepted.intent_id}`;
    let proof: any;
    await expect.poll(() => {
      proof = wakeSnapshot(acceptanceId, reviewId, accepted.intent_id);
      return proof.intent?.[4];
    }, {timeout:90_000}).toBe('applied');
    expect(proof.intent).toEqual([accepted.intent_id,card,proposed.source_revision,entry,'applied',reviewBody.attempt_id,savedReview.review_id,child]);
    expect(proof.split).toHaveLength(1);
    expect(proof.receipts).toHaveLength(1);
    expect(proof.receipts[0].slice(0,4)).toEqual([reviewBody.attempt_id,card,entry,savedReview.review_id]);
    const acceptanceReceipt = proof.operations.find((row: any[]) => row[0] === acceptanceId);
    const reviewReceipt = proof.operations.find((row: any[]) => row[0] === reviewId);
    expect(acceptanceReceipt?.slice(0,3)).toEqual([acceptanceId,'cards.prefix_split.accept','complete']);
    expect(reviewReceipt?.slice(0,3)).toEqual([reviewId,'cards.review','complete']);
    const childReceipt = proof.operations.find((row: any[]) => row[0] === child);
    expect(childReceipt).toBeTruthy();
    expect(childReceipt.slice(0,3)).toEqual([child,'cards.prefix_split.apply_deferred','complete']);
    expect(childReceipt[6]).toBe(false);
    const elapsed = Date.parse(childReceipt[4]) - Date.parse(childReceipt[3]);
    expect(elapsed).toBeGreaterThanOrEqual(0);
    expect(elapsed).toBeLessThan(30_000);
    const workerLogs = () => execFileSync('docker', [...compose,'logs','--no-color','--tail','1000','foreground-worker'], {encoding:'utf8',timeout:10_000});
    await expect.poll(() => workerLogs().includes(`app.tasks.execute_foreground_command[${child}]`), {timeout:10_000}).toBe(true);
    const before = JSON.stringify(proof);
    // Deliberately inject a duplicate only after the original child completed.
    // The one-off producer validates the owned DB marker and exact completed row.
    const duplicate = `import os,sys,psycopg\nfrom app.celery_app import celery_app\ncard,intent,child=sys.argv[1:]\nif os.environ.get('TEMPO_TEST_INSTANCE')!='disposable' or os.environ.get('TEMPO_REDIS_URL')!='redis://redis:6379/0': raise RuntimeError('foreign target')\nwith psycopg.connect('postgresql://postgres@postgres:5432/tempo') as db:\n marker=db.execute("SELECT current_database(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()\n if marker != ('tempo','tempo-disposable-postgres-test'): raise RuntimeError('foreign database')\n row=db.execute("SELECT state,child_operation_id FROM study_deferred_prefix_splits WHERE intent_id=%s AND card_id=%s",(intent,card)).fetchone()\n if row != ('applied',child) or child != 'prefix-split-intent:'+intent: raise RuntimeError('foreign or incomplete child')\ncelery_app.send_task('app.tasks.execute_foreground_command',args=[child,'cards.prefix_split.apply_deferred',{'intent_id':intent}],task_id=child,queue='foreground',retry=False)\n`;
    const completedCount = () => workerLogs().split('\n').filter(line => line.includes(`app.tasks.execute_foreground_command[${child}]`) && line.includes('succeeded')).length;
    await expect.poll(completedCount, {timeout:10_000}).toBeGreaterThan(0);
    const existingCount = completedCount();
    execFileSync('docker', [...compose,'run','--rm','--no-deps','-e','TEMPO_TEST_INSTANCE=disposable',
      '-e','TEMPO_REDIS_URL=redis://redis:6379/0','schema','python','-',card,accepted.intent_id,child],
      {input:duplicate,encoding:'utf8',timeout:15_000});
    await expect.poll(completedCount, {timeout:30_000}).toBeGreaterThan(existingCount);
    expect(JSON.stringify(wakeSnapshot(acceptanceId, reviewId, accepted.intent_id))).toBe(before);
    await testInfo.attach('split-broker-wake-receipt', {body:JSON.stringify({child, elapsed_ms:elapsed, proof}),contentType:'application/json'});
  });
});
