import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { test, expect } from './observability';
import { api } from './product-fixtures';
import { verifyPostgresFixtureTarget, postgresFixtureComposeArguments } from './product-fixture-reset';
import { navigate, prepareUI } from './ui-fixtures';

test('real Study worker publishes deliberately malformed optional manifest diagnostics', async ({ page, request }) => {
  test.setTimeout(180_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const health = await request.get(`${api}/health`);
  await verifyPostgresFixtureTarget({ health: await health.json(), apiUrl: api, project });
  const compose = postgresFixtureComposeArguments(project);
  const settings = await (await request.get(`${api}/settings`)).json();
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: settings.timezone === 'local' ? process.env.TZ : settings.timezone,
    year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const prefix = `p12-frozen-${randomUUID().replaceAll('-', '')}`;
  let displaced: number[][] = [];
  const fixture = (action: string, ...args: string[]) => JSON.parse(execFileSync('docker', [...compose, 'run', '--rm', '--no-deps',
    '-e', 'TEMPO_TEST_INSTANCE=disposable', 'schema', 'python', '-', action, prefix, day, ...args], {
    input: readFileSync('tests/fixtures/postgres-frozen-study-browser.py', 'utf8'), encoding: 'utf8', timeout: 30_000,
  }));
  let originalFailure: unknown;
  try {
    let seeded: { ready: boolean; displaced: number[][]; entries: number[] } = { ready: false, displaced: [], entries: [] };
    await expect.poll(() => { seeded = fixture('seed'); return seeded.ready; }, { timeout: 60_000 }).toBe(true);
    displaced = seeded.displaced;
    // Observe the actual Worker instance and messages; the constructor and computation remain real.
    await page.addInitScript(() => {
      const NativeWorker = window.Worker;
      (window as any).__fe10WorkerReplies = [];
      window.Worker = class extends NativeWorker {
        constructor(...args: ConstructorParameters<typeof Worker>) {
          super(...args);
          this.addEventListener('message', event => (window as any).__fe10WorkerReplies.push(event.data));
        }
      };
    });
    let injected = false;
    // Only corrupt the optional diagnostic field of the fixture-owned card in one
    // real GET response. No issuance, review, or save request is mocked.
    await page.route('**/api/queue/window**', async route => {
      if (route.request().method() !== 'GET' || injected) return route.continue();
      const response = await route.fetch();
      if (response.status() !== 200) return route.fulfill({ response });
      const body = await response.json();
      const card = Array.isArray(body.cards) ? body.cards.find((item: any) => item.id === `${prefix}-card-0`) : undefined;
      if (!card) return route.fulfill({ response });
      expect(card).toMatchObject({ id: `${prefix}-card-0`, queue_entry_id: seeded.entries[0], revision: 1 });
      card.opening_decision_manifest = { deliberately_invalid_fe10_manifest: true };
      injected = true;
      await route.fulfill({ response, json: body });
    });
    await prepareUI(page);
    await navigate(page, 'Train');
    await expect.poll(() => injected, { timeout: 60_000 }).toBe(true);
    await expect(page.getByText('P12 first move')).toBeVisible();
    await expect(page.getByLabel('Coordinates or UCI move')).toBeEnabled();
    await expect.poll(() => page.evaluate(() => (window as any).__fe10WorkerReplies.some((reply: any) =>
      reply.diagnostics?.some((issue: any) => issue.source === 'opening evidence' && String(issue.message).includes('invalid manifest')))),
      { timeout: 30_000 }).toBe(true);
    const notice = page.locator('details.data-diagnostics-notice');
    await expect(notice).toBeVisible();
    await notice.locator('summary').click();
    await expect(notice).toContainText('opening evidence');
    await expect(notice).toContainText('invalid manifest');
    const snapshot = fixture('snapshot');
    expect(snapshot.reviews).toHaveLength(0);
    expect(snapshot.origins).toContainEqual([seeded.entries[0], `${prefix}-card-0`, 1, 1]);
  } catch (error) {
    originalFailure = error;
    throw error;
  } finally {
    try {
      const cleanup = fixture('cleanup', JSON.stringify(displaced), JSON.stringify([]));
      expect(cleanup.cleaned).toBe(true);
      expect(cleanup.restored + cleanup.reordered + cleanup.removed).toBe(displaced.length);
      expect(Object.values(cleanup.remaining).every(value => value === 0)).toBe(true);
    } catch (error) {
      if (originalFailure === undefined) throw error;
      throw new Error(`Original diagnostic failure: ${originalFailure instanceof Error ? originalFailure.stack : String(originalFailure)}\n\nFixture cleanup failure: ${error instanceof Error ? error.stack : String(error)}`);
    }
  }
});
