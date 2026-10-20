import { execFileSync } from 'node:child_process';
import { api, test, expect, move } from './product-fixtures';
import { prepareUI } from './ui-fixtures';

test('frozen first-study rank survives refresh and replacement preserves the issued board attempt', async ({ page, request }) => {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error('Owning disposable PostgreSQL runner required');
  const fixture = (action: string) => JSON.parse(execFileSync('docker', [
    'compose','-p',project,'-f','docker-compose.postgres.test.yml','run','--rm','--no-deps',
    '-e','TEMPO_DATABASE_READ_URL=postgresql://postgres@postgres:5432/tempo',
    '-e','TEMPO_DATABASE_WRITE_URL=postgresql://postgres@postgres:5432/tempo',
    'schema','python','/source/database/tests/frozen-order-browser-fixture.py',action,
  ], { encoding:'utf8', timeout:30_000 }).trim());
  const seeded = fixture('seed');
  const queue = async () => (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards as { id:string; moves:string[] }[];
  await expect.poll(async () => (await queue()).map(card => card.id), { timeout:30_000 }).toEqual(seeded.ranked_ids);
  await prepareUI(page);
  await expect(page.locator('.board-frame').first()).toHaveAttribute('data-input-enabled','true');
  fixture('replacement');
  await expect.poll(async () => (await queue())[0]?.id).toBe(seeded.ranked_ids[0]);
  await move(page,'d2','d4');
  await expect(page.getByRole('button',{name:'Correct',exact:true})).toBeVisible();
});
