import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { api, test as productTest, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";

productTest("daily study opens on the workspace date while background analysis remains queued", async ({ page, request }) => {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("Daily study backlog proof requires the owning disposable PostgreSQL runner");
  const fixtureId = `daily-study-proof-${randomUUID()}`;
  const composeArguments = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
  const docker = (...args: string[]) => execFileSync("docker", [...composeArguments, ...args], { encoding: "utf8", timeout: 30_000 });
  const queueResponse = await request.get(`${api}/queue/window?limit=20`);
  expect(queueResponse.ok()).toBeTruthy();
  const workspaceDate = (await queueResponse.json()).local_date as string;
  expect(workspaceDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  const fixture = (action: string) => docker("run", "--rm", "--no-deps", "-e", "TEMPO_REDIS_URL=redis://redis:6379/0",
    "schema", "python", "/source/database/tests/check_postgres_daily_study_dispatch.py", action, fixtureId,
    ...(action === 'seed' ? [workspaceDate] : []));
  try {
    docker("stop", "background-worker", "background-scheduler");
    fixture("seed");
    // A denied wake returns promptly; normal durable polling must redeliver it.
    docker("start", "background-worker", "background-scheduler");
    const initialActivity = await (await request.get(`${api}/system/activity?limit=1`)).json();
    expect(initialActivity.counts.queued).toBeGreaterThan(1000);
    // A cold focused run can reach the browser before the restarted worker
    // publishes the fixture's due card. Establish the real queue boundary
    // without waiting for the independent analysis backlog to drain.
    await expect.poll(async () => {
      const publishedQueueResponse = await request.get(`${api}/queue/window?limit=20`, { headers: { "X-Tempo-Work-Class": "background" } });
      expect(publishedQueueResponse.ok()).toBeTruthy();
      const publishedQueue = await publishedQueueResponse.json();
      return publishedQueue.cards.some((card: { id: string }) => card.id === `${fixtureId}-due`);
    }, { message: "Fixture due card is published before browser interaction", timeout: 30_000 })
      .toBe(true);
    const publishedActivity = await (await request.get(`${api}/system/activity?limit=1`)).json();
    expect(publishedActivity.counts.queued).toBeGreaterThan(1000);
    await prepareUI(page);
    await expect(page.locator('.persistent-board-shell[data-unavailable="true"]')).toHaveCount(0);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    await expect(page.getByText(/The local queue could not be loaded/)).toHaveCount(0);
    const queue = await (await request.get(`${api}/queue/window?limit=20`)).json();
    expect(queue.cards.some((card: { id: string }) => card.id === `${fixtureId}-due`)).toBe(true);
    await move(page, "e2", "e4");
    await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeVisible();
    const remainingActivity = await (await request.get(`${api}/system/activity?limit=1`)).json();
    expect(remainingActivity.counts.queued).toBeGreaterThan(0);
  } finally {
    docker("stop", "background-worker");
    try { fixture("cleanup"); }
    finally { docker("start", "background-worker", "background-scheduler"); }
  }
});
