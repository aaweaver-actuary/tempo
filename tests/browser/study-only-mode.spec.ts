import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { Chess } from "chess.js";
import type { Page } from "@playwright/test";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { assertDisposableTarget } from "./disposable-target";
import { boardBounds, navigate, prepareUI, noPageOverflow } from "./ui-fixtures";

const tacticFen = "q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18";

async function visibleTacticPieces(page: Page) {
  return page.locator(".cg-wrap cg-board").first().evaluate(board => {
    const bounds = board.getBoundingClientRect();
    const roles: Record<string, string> = { pawn: "p", knight: "n", bishop: "b", rook: "r", queen: "q", king: "k" };
    return [...board.querySelectorAll("piece:not(.ghost)")].map(piece => {
      const pieceBounds = piece.getBoundingClientRect();
      const column = Math.floor((pieceBounds.x + pieceBounds.width / 2 - bounds.x) / (bounds.width / 8));
      const row = Math.floor((pieceBounds.y + pieceBounds.height / 2 - bounds.y) / (bounds.height / 8));
      const role = Object.keys(roles).find(name => piece.classList.contains(name));
      return `${String.fromCharCode(97 + column)}${8 - row}:${piece.classList.contains("white") ? "w" : "b"}:${role ? roles[role] : "unknown"}`;
    }).sort();
  });
}

for (const width of [390, 1280]) {
  test(`study_only_tactic_mistake_survives_guided_completion_and_reload (${width})`, async ({ page, request }, testInfo) => {
    test.setTimeout(120_000);
    assertDisposableTarget(await (await request.get(`${api}/health`)).json());
    const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
    if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
    const identifier = `training-transition-${randomUUID()}`;
    const docker = (...args: string[]) => execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml", ...args],
      { encoding: "utf8", timeout: 60_000 });
    const fixture = (action: string, ...args: string[]) => JSON.parse(docker("run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_training_transition.py", action, identifier, ...args));
    const originalSettings = await (await request.get(`${api}/settings`)).json();
    const deliveries: { url: string; key: string; body: { outcome: string; guided: boolean } }[] = [];
    const membershipRequests: string[] = [];
    page.on("request", outgoing => {
      if (outgoing.url().endsWith(`/cards/${identifier}-prepared-000/review`))
        deliveries.push({ url: outgoing.url(), key: outgoing.headers()["idempotency-key"], body: outgoing.postDataJSON() });
      if (outgoing.url().includes("/move-membership?")) membershipRequests.push(outgoing.url());
    });
    let displacedPositions: number[][] = [];
    try {
      const saveSettings = await request.put(`${api}/settings`, { data: { ...originalSettings, study_only_mode: true } });
      expect(saveSettings.ok()).toBe(true);
      docker("stop", "background-worker", "background-scheduler");
      const today = (await (await request.get(`${api}/queue/window?limit=20`)).json()).local_date;
      displacedPositions = fixture("seed-tactics", today).displaced_queue_positions;
      expect(fixture("verify-tactics").source_count).toBe(0);
      docker("start", "background-worker", "background-scheduler");
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await prepareUI(page);
      await navigate(page, "Train");
      const head = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
      expect(head.id).toBe(`${identifier}-prepared-000`);
      expect(Boolean(head.attempt_failed)).toBe(false);
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
      await expect(page.getByText(/Study-only mode is on/)).toBeVisible();
      const expectedPieces = new Chess(tacticFen).board().flatMap(rank => rank.flatMap(piece => piece
        ? [`${piece.square}:${piece.color}:${piece.type}`] : [])).sort();
      const play = async (from: string, to: string) => {
        await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
        const bounds = await boardBounds(page);
        for (const square of [from, to]) await page.mouse.click(bounds.x + (square.charCodeAt(0) - 97 + 0.5) * bounds.width / 8,
          bounds.y + (8 - Number(square[1]) + 0.5) * bounds.height / 8);
      };
      await play("a3", "b4");
      await expect(page.locator(".feedback.wrong")).toContainText("That move is not the puzzle solution");
      await expect(page.locator(".feedback-icon")).toHaveText("×");
      await expect.poll(() => visibleTacticPieces(page)).toEqual(expectedPieces);
      await expect.poll(async () => (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0].attempt_failed).toBeTruthy();
      expect(fixture("verify-tactics").reviews).toEqual([]);
      await page.reload();
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
      await expect(page.getByText("Guided attempt resumed", { exact: true })).toBeVisible();
      await expect.poll(() => visibleTacticPieces(page)).toEqual(expectedPieces);
      await page.clock.install();
      await page.clock.pauseAt(new Date(Date.now() + 1_000));
      await play("a2", "e6");
      await page.clock.runFor(450);
      await play("f7", "f8");
      await page.clock.runFor(20);
      await expect(page.locator(".feedback.wrong")).toContainText("Guided line complete");
      await expect(page.locator(".feedback.wrong")).toContainText("This attempt counts as Again.");
      await expect(page.locator(".feedback-icon")).toHaveText("×");
      await expect(page.locator(".feedback.complete")).toHaveCount(0);
      await noPageOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`guided-tactic-${width}.png`), fullPage: true });
      const reviewResponse = page.waitForResponse(response => response.url().endsWith(`/cards/${identifier}-prepared-000/review`)
        && response.request().method() === "POST");
      await page.clock.runFor(750);
      await page.clock.resume();
      expect((await reviewResponse).ok()).toBe(true);
      await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
      expect(deliveries).toHaveLength(1);
      expect(deliveries[0].body).toMatchObject({ outcome: "again", guided: true });
      const beforeReplay = fixture("verify-tactics");
      expect(beforeReplay.reviews).toHaveLength(1);
      expect(beforeReplay.reviews[0].slice(1)).toEqual(["again", 1]);
      expect(beforeReplay.receipts).toHaveLength(1);
      expect(beforeReplay.receipts[0].slice(3)).toEqual(["again", 1]);
      expect(membershipRequests).toEqual([]);
      await page.reload();
      const delivery = deliveries[0];
      const replay = await request.post(delivery.url, { headers: { "Idempotency-Key": delivery.key }, data: delivery.body });
      expect(replay.ok()).toBe(true);
      expect(fixture("verify-tactics")).toEqual(beforeReplay);
      await testInfo.attach("tactics-review-proof", { body: JSON.stringify(beforeReplay), contentType: "application/json" });
    } finally {
      await page.close();
      docker("stop", "background-worker", "background-scheduler");
      try { fixture("cleanup", JSON.stringify(displacedPositions)); }
      finally {
        await request.put(`${api}/settings`, { data: originalSettings });
        docker("start", "background-worker", "background-scheduler");
        await expect.poll(async () => {
          const health = await request.get(`${api}/health`);
          return health.ok() ? (await health.json()).test_instance : `HTTP ${health.status()}`;
        }, { timeout: 60_000, message: "Tactics fixture cleanup must restore disposable service readiness before the next case" }).toBe(true);
      }
    }
  });
}

test("study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  assertDisposableTarget(await (await request.get(`${api}/health`)).json());
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
  const identifier = `training-transition-${randomUUID()}`;
  const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
  const docker = (...args: string[]) => execFileSync("docker", [...compose, ...args], { encoding: "utf8", timeout: 60_000 });
  const fixture = (action: string, ...args: string[]) => JSON.parse(docker("run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/check_postgres_training_transition.py", action, identifier, ...args));
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  let displacedPositions: number[][] = [];
  const reviewRequests: { url: string; body: unknown; key: string }[] = [];
  page.on("request", outgoing => {
    if (outgoing.url().endsWith("/review")) reviewRequests.push({ url: outgoing.url(), body: outgoing.postDataJSON(), key: outgoing.headers()["idempotency-key"] });
  });
  try {
    await prepareUI(page);
    await navigate(page, "Settings");
    await expect(page.getByRole("button", { name: "Save settings" })).toBeEnabled();
    await page.getByRole("checkbox", { name: /Study-only mode/ }).check();
    expect((await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(false);
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(true);
    await noPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("study-only-settings.png") });
    docker("stop", "background-worker", "background-scheduler");
    const today = (await (await request.get(`${api}/queue/window?limit=20`)).json()).local_date;
    displacedPositions = fixture("seed-with-existing-queue", today).displaced_queue_positions;
    docker("start", "background-worker", "background-scheduler");
    await page.reload();
    await navigate(page, "Train");
    await expect(page.getByText(/Study-only mode is on. Optional/)).toBeVisible();
    await page.getByRole("button", { name: "Analysis activity" }).click();
    await expect(page.getByText(/pending work is preserved/)).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    for (let index = 0; index < 3; index++) {
      const head = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
      expect(head.id).toBe(`${identifier}-prepared-${String(index).padStart(3, "0")}`);
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      const board = await boardBounds(page);
      if (index === 0) {
        await page.mouse.click(board.x + board.width * 3.5 / 8, board.y + board.height * 6.5 / 8);
        await page.mouse.click(board.x + board.width * 3.5 / 8, board.y + board.height * 4.5 / 8);
        await expect(page.getByText(/Valid repertoire move/)).toBeVisible();
        await expect(page.getByText(/Cannot verify another repertoire move/)).toHaveCount(0);
      }
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 6.5 / 8);
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 4.5 / 8);
      await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeEnabled();
      await page.getByRole("button", { name: "Correct", exact: true }).click();
      await expect.poll(() => fixture("verify").reviews).toBe(index + 1);
      await page.reload();
    }
    const nextHead = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
    expect(nextHead.id).toBe(`${identifier}-prepared-003`);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(nextHead.queue_entry_id));
    const beforeRestart = fixture("verify");
    expect(beforeRestart).toMatchObject({ reviews: 3, complete_evidence: 3, pending_maintenance: 3000 });
    docker("restart", "foreground-worker", "background-worker", "background-scheduler");
    await expect.poll(async () => (await request.get(`${api}/health`)).ok(), { timeout: 60_000 }).toBe(true);
    const first = reviewRequests[0];
    expect(first).toBeTruthy();
    await request.post(first.url, { headers: { "Idempotency-Key": first.key }, data: first.body });
    await expect.poll(() => fixture("verify").reviews).toBe(3);
    await navigate(page, "Settings");
    await expect(page.getByRole("checkbox", { name: /Study-only mode/ })).toBeChecked();
    await page.getByRole("checkbox", { name: /Study-only mode/ }).uncheck();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).study_only_mode).toBe(false);
    await expect.poll(() => fixture("verify").pending_maintenance, { timeout: 60_000 }).toBeLessThan(3000);
  } finally {
    await request.put(`${api}/settings`, { data: originalSettings });
    docker("start", "foreground-worker", "background-worker", "background-scheduler");
    fixture("cleanup", JSON.stringify(displacedPositions));
  }
});
