import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { nativeStudyRecords } from '../browser/native-study-storage';
import { move } from '../browser/native-board';
import { openingCard } from '../controlled-browser/fixtures';
import startup from '../fixtures/controlled-browser/startup.json' with { type: 'json' };

test('real worker upgrade retains and replays the original pending review', async ({ page, context }, testInfo) => {
  const diagnostics: string[] = [];
  page.on('pageerror', error => diagnostics.push(`pageerror: ${error.message}`));
  page.on('requestfailed', request => diagnostics.push(`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`));
  page.on('response', response => { if (response.status() >= 400) diagnostics.push(`response ${response.status()}: ${response.url()}`); });
  const root = process.env.TEMPO_SW_CANDIDATE_ARTIFACT;
  if (!root) throw new Error('TEMPO_SW_CANDIDATE_ARTIFACT must name the task-owned candidate build');
  const directory = resolve(root);
  const worker = await readFile(resolve(directory, 'sw.js'), 'utf8');
  if (!worker.includes('const CACHE = "tempo-static-v7";')) throw new Error('Unexpected product worker version');
  let version = 7;
  const types: Record<string, string> = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.wasm': 'application/wasm', '.json': 'application/json', '.webmanifest': 'application/manifest+json' };
  const server = createServer(async (request, response) => {
    response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
    response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
    try {
      const path = new URL(request.url!, 'http://localhost').pathname;
      if (path === '/sw.js') {
        response.writeHead(200, { 'Content-Type': 'text/javascript', 'Cache-Control': 'no-store' });
        response.end(version === 7 ? worker : worker.replace('const CACHE = "tempo-static-v7";', 'const CACHE = "tempo-static-v8";'));
        return;
      }
      const file = resolve(directory, '.' + (path === '/' ? '/index.html' : path));
      if (!file.startsWith(directory + sep)) throw new Error('Invalid artifact path');
      const data = await readFile(file);
      response.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream' });
      response.end(data);
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise<void>(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  try {
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Missing owned origin');
    const origin = `http://127.0.0.1:${address.port}`;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.clock.setFixedTime(new Date('2026-09-18T16:00:00Z'));
    await context.addInitScript(() => {
      localStorage.setItem('tempo-study-intents-cutover-v1', 'acknowledged');
      for (const key of ['tempo-sound', 'tempo-stockfish-on', 'tempo-maia-on', 'tempo-games-engine-on', 'tempo-explorer-on']) localStorage.setItem(key, 'false');
    });
    let accept = false;
    const delivered: { id: string | undefined; body: Record<string, unknown> }[] = [];
    await page.route('**/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === 'POST' && /\/api\/cards\/[^/]+\/review$/.test(path)) {
        if (!accept) return route.fulfill({ status: 503, json: { detail: 'temporarily unavailable' } });
        delivered.push({ id: request.headers()['idempotency-key'], body: request.postDataJSON() });
        return route.fulfill({ json: { persisted: true, review_id: 601 } });
      }
      if (path.startsWith('/api/operations/')) return route.fulfill({ status: 404, json: { detail: 'Not found' } });
      if (path === '/api/queue/window') return route.fulfill({ json: { cards: [openingCard('pending-london', 'London System', 'd2d4', 41)], count: 1 } });
      const fixture = (startup as Record<string, unknown>)[path];
      if (fixture !== undefined) return route.fulfill({ json: fixture });
      if (path.endsWith('/teaching')) return route.fulfill({ json: { states: [] } });
      return route.fulfill({ status: 404, json: { detail: 'not part of this fixture' } });
    });
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
    await page.evaluate(async () => { await (await caches.open('unrelated-cache')).put('/sentinel', new Response('keep')); });
    await expect(page.locator('.phone-study-heading h2')).toHaveText('London System');
    await move(page, 'd2', 'd4');
    await expect.poll(async () => (await nativeStudyRecords(page)).filter(item => item.kind === 'review' && item.state !== 'committed').length).toBe(1);
    const original = (await nativeStudyRecords(page)).find(item => item.kind === 'review' && item.state !== 'committed')!;
    expect(original.key).toMatch(/^review:/);
    expect(original.requestId).toBe(`review-attempt:${original.key.slice('review:'.length)}`);
    version = 8;
    await page.evaluate(async () => { await (await navigator.serviceWorker.ready).update(); });
    await expect.poll(() => page.evaluate(async () => (await caches.keys()).sort())).toContain('tempo-static-v8');
    await expect.poll(() => page.evaluate(async () => (await caches.keys()).sort())).not.toContain('tempo-static-v7');
    await expect.poll(() => page.evaluate(() => new Promise<string>(resolveVersion => {
      const channel = new MessageChannel();
      channel.port1.onmessage = event => resolveVersion(event.data.version);
      navigator.serviceWorker.controller!.postMessage({ type: 'tempo:offline-shell-status' }, [channel.port2]);
    }))).toBe('tempo-static-v8');
    expect(await page.evaluate(async () => Boolean(await (await caches.open('unrelated-cache')).match('/sentinel')))).toBe(true);
    await context.setOffline(true);
    await page.reload();
    await expect(page.getByRole('button', { name: 'Tempo home' })).toBeVisible();
    await expect(page.getByText('A previous result needs confirmation or attention.', { exact: false })).toBeVisible();
    await expect.poll(async () => (await nativeStudyRecords(page)).find(item => item.key === original.key)).toMatchObject({ key: original.key, requestId: original.requestId, payload: original.payload });
    accept = true;
    await context.setOffline(false);
    await page.reload();
    await page.getByRole('button', { name: 'Check saved reviews', exact: true }).click();
    await expect.poll(() => delivered.length).toBe(1);
    expect(delivered[0].id).toBe(original.requestId);
    const saved = original.payload as { backendId: string; queueEntryId: number; attemptId: string; completedAt: string; outcome: string; guided: boolean; expectedRevision?: number };
    expect(delivered[0].body).toMatchObject({ queue_entry_id: saved.queueEntryId, attempt_id: saved.attemptId,
      recorded_at: saved.completedAt, outcome: saved.outcome, guided: saved.guided });
    if (saved.expectedRevision !== undefined) expect(delivered[0].body.expected_revision).toBe(saved.expectedRevision);
    await expect.poll(async () => (await nativeStudyRecords(page)).find(item => item.key === original.key)?.state).toBe('committed');
    await page.reload();
    expect(delivered).toHaveLength(1);
  } finally {
    await testInfo.attach('browser-diagnostics', { body: JSON.stringify(diagnostics, null, 2), contentType: 'application/json' });
    await new Promise<void>((resolveClose, reject) => server.close(error => error ? reject(error) : resolveClose()));
  }
});
