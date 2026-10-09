import { expect, test } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { api, test as productTest, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

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
    "schema", "python", "/source/scripts/check_postgres_daily_study_dispatch.py", action, fixtureId,
    ...(action === 'seed' ? [workspaceDate] : []));
  try {
    docker("stop", "background-worker", "background-scheduler");
    fixture("seed");
    docker("start", "background-worker");
    const initialActivity = await (await request.get(`${api}/system/activity?limit=1`)).json();
    expect(initialActivity.counts.queued).toBeGreaterThan(1000);
    // A cold focused run can reach the browser before the restarted worker
    // publishes the fixture's due card. Establish the real queue boundary
    // without waiting for the independent analysis backlog to drain.
    await expect.poll(async () => {
      const publishedQueueResponse = await request.get(`${api}/queue/window?limit=20`);
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

test("discovery preview backlog leaves a prompt foreground training queue refresh", async ({ page }) => {
  await prepareVisualUI(page);
  const discoveries = Array.from({ length: 6 }, (_, index) => ({
    id: `preview-${index}`, repertoire_id: "visual-repertoire", kind: "missing_response",
    status: "active", fen_key: startFen.split(" ").slice(0, 4).join(" "),
    fen: startFen, decision_fen: startFen, decision_start_fen: startFen,
    decision_route_uci: [], accepted_moves_uci: [], card_id: null,
    opponent_move_uci: null, trained_color: "white", score: 1,
    evidence: { supporting_games: 4 }, evidence_fingerprint: `revision-${index}`,
    seen_at: "2026-09-18T00:00:00Z", snoozed_until: null, admission_state: null,
    admitted_card_id: null, unread: false, source_games: [], routes: [],
    created_at: "2026-09-18T00:00:00Z", updated_at: "2026-09-18T00:00:00Z",
  }));
  await page.route("**/api/discoveries?**", (route) => route.fulfill({ json: {
    discoveries, total: discoveries.length, next_offset: null, unread_count: 0,
  } }));

  let activePreviews = 0;
  let maximumActivePreviews = 0;
  let startedPreviews = 0;
  const previewWorkClasses: string[] = [];
  let releasePreviews: (() => void) | undefined;
  const previewHold = new Promise<void>((resolve) => { releasePreviews = resolve; });
  await page.route("**/api/discoveries/*/recommendations", async (route) => {
    startedPreviews += 1;
    activePreviews += 1;
    maximumActivePreviews = Math.max(maximumActivePreviews, activePreviews);
    previewWorkClasses.push(route.request().headers()["x-tempo-work-class"] ?? "");
    try {
      await previewHold;
      const discovery = discoveries.find(item => route.request().url().includes(`/${item.id}/`))!;
      await route.fulfill({ json: { state: "ready", opportunity_id: discovery.id,
        evidence_fingerprint: discovery.evidence_fingerprint, starting_fen: startFen,
        candidates: [{ move_uci: "e2e4", score: { cp: 20, mate: null }, loss_cp: 0,
          similarity: "fixture", repertoire_line_count: 0, exact_transposition: false,
          example_line_id: null, example_line_name: null, preview_moves_uci: ["e2e4"],
          engine_version: "fixture", network_version: "fixture", depth: 14,
          report_id: "fixture", source_game_id: "fixture", source_ply: 0 }] } });
    } finally {
      activePreviews -= 1;
    }
  });
  let queueReads = 0;
  await page.route("**/api/queue/window?**", (route) => {
    queueReads += 1;
    return route.fulfill({ json: { local_date: "2026-09-18", count: 1, cards: [{
      id: "visual-card", queue_entry_id: 1, start_fen: startFen, moves: ["e2e4"],
      content_type: "opening", repertoire_name: "Spanish opening",
      repertoire_source: "PGN", trained_color: "white",
    }] } });
  });
  await page.route("**/api/cards/visual-card/review", (route) =>
    route.fulfill({ json: { persisted: true } }));

  try {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Correct" })).toBeVisible();
    await expect.poll(() => startedPreviews).toBeGreaterThanOrEqual(2);
    await page.waitForTimeout(300);
    expect(maximumActivePreviews).toBeLessThanOrEqual(2);
    expect(previewWorkClasses.every((workClass) => workClass === "background")).toBe(true);

    const refreshStartedAt = Date.now();
    await page.getByRole("button", { name: "Correct" }).click();
    await expect.poll(() => queueReads, { timeout: 2_000 }).toBeGreaterThanOrEqual(2);
    expect(Date.now() - refreshStartedAt).toBeLessThan(2_000);

    releasePreviews?.();
    await expect.poll(() => activePreviews).toBe(0);
    // Closed idle preparation deliberately selects only two entries per feed
    // refresh; foreground queue reads must not depend on draining all six.
    await page.waitForTimeout(3_500);
    expect(startedPreviews).toBe(2);
    await page.getByRole("button", { name: "Discoveries", exact: true }).click();
    await expect.poll(() => startedPreviews).toBeGreaterThan(2);
    expect(maximumActivePreviews).toBeLessThanOrEqual(2);
  } finally {
    releasePreviews?.();
  }
});
