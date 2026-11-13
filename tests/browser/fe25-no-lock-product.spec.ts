import type { APIRequestContext, APIResponse } from '@playwright/test';
import { api, test, expect, move } from './product-fixtures';
import { prepareUI } from './ui-fixtures';

async function confirmed(request: APIRequestContext, response: APIResponse) {
  const body = await response.json();
  expect(response.ok(), JSON.stringify(body)).toBe(true);
  if (response.status() !== 202) return body;
  let receipt: { operation_id: string; state: string; response?: unknown };
  await expect.poll(async () => {
    receipt = await (await request.get(`${api}/operations/${encodeURIComponent(body.operation_id)}`)).json();
    expect(receipt.operation_id).toBe(body.operation_id);
    return receipt.state;
  }, { timeout: 30_000 }).toBe('complete');
  return receipt!.response as typeof body;
}

test('FE25 without Web Locks saves an ordinary review and issues a playable next card', async ({ page, request }) => {
  const imported = await confirmed(request, await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: 'fe25-no-lock.pgn', mimeType: 'application/x-chess-pgn', buffer: Buffer.from(
      '[Event "FE25 no lock"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 *') },
    trained_color: 'white', initial_depth: '2',
  } }));
  await confirmed(request, await request.put(`${api}/repertoires/${imported.repertoire_id}/settings`, { data: { new_cards_per_day: 2 } }));
  const queue = async () => {
    let response = await request.get(`${api}/queue/window?limit=20`);
    if (response.status() === 202) {
      await confirmed(request, response);
      response = await request.get(`${api}/queue/window?limit=20`);
    }
    expect(response.status(), await response.text()).toBe(200);
    return (await response.json()).cards as { id: string; queue_entry_id: number; moves: string[] }[];
  };
  let initial: { id: string; queue_entry_id: number; moves: string[] } | undefined;
  await expect.poll(async () => { initial = (await queue())[0]; return initial?.moves.join(','); }, { timeout: 30_000 })
    .toBe('e2e4,e7e5,g1f3');
  await page.addInitScript(() => Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined }));
  await prepareUI(page);
  expect(await page.evaluate(() => navigator.locks === undefined)).toBe(true);
  await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id', String(initial!.queue_entry_id));
  const reviewRequest = page.waitForRequest(sent => sent.url().includes(`/cards/${initial!.id}/review`) && sent.method() === 'POST');
  await move(page, 'e2', 'e4');
  await move(page, 'g1', 'f3');
  const sent = await reviewRequest;
  const operationId = sent.headers()['idempotency-key'];
  expect(operationId).toMatch(/^review-attempt:/);
  expect(sent.postDataJSON().queue_entry_id).toBe(initial!.queue_entry_id);
  let receipt: { operation_id: string; state: string; response: { persisted: boolean; queue_entry_id: number } };
  await expect.poll(async () => {
    receipt = await (await request.get(`${api}/operations/${encodeURIComponent(operationId)}`)).json();
    return receipt;
  }, { timeout: 30_000 }).toMatchObject({ operation_id: operationId, state: 'complete',
    response: { persisted: true, queue_entry_id: initial!.queue_entry_id } });
  let next: { id: string; queue_entry_id: number; moves: string[] } | undefined;
  await expect.poll(async () => { next = (await queue())[0]; return Boolean(next && next.queue_entry_id !== initial!.queue_entry_id); },
    { timeout: 30_000 }).toBe(true);
  expect(next).toBeDefined();
  await expect(page.locator('#train')).toHaveAttribute('data-queue-entry-id', String(next!.queue_entry_id));
  await expect(page.locator('.board-frame').first()).toHaveAttribute('data-input-enabled', 'true');
  expect(next!.moves[0]).toBe('e2e4');
  await move(page, 'e2', 'e4');
  await expect(page.locator('.board-frame').first()).toHaveAttribute('data-fen', /4p3\/4P3/);
  expect(await (await request.get(`${api}/operations/${encodeURIComponent(operationId)}`)).json()).toEqual(receipt!);
});
