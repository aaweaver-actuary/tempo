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

for (const mode of ["delayed", "rejected", "lost-response", "timeout", "reload", "reordered", "competing"] as const) {
  const width = 1280;
  test(`tactic_failure_ordering_audit (${mode})`, async ({ page, request }, testInfo) => {
    test.setTimeout(120_000);
    assertDisposableTarget(await (await request.get(`${api}/health`)).json());
    const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
    if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
    const identifier = `training-transition-${randomUUID()}`;
    const docker = (...args: string[]) => execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml", ...args],
      { encoding: "utf8", timeout: 60_000 });
    const fixture = (action: string, ...args: string[]) => JSON.parse(docker("run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/check_postgres_training_transition.py", action, identifier, ...args));
    const snapshot = () => JSON.parse(docker("run", "--rm", "--no-deps", "schema", "python",
      "/source/scripts/audit_tactic_failure_snapshot.py", identifier));
    const originalSettings = await (await request.get(`${api}/settings`)).json();
    const deliveries: { url: string; key: string; body: { outcome: string; guided: boolean } }[] = [];
    const membershipRequests: string[] = [];
    let failureCount = 0;
    let allowFailures = mode !== "reload";
    let releaseFailure!: () => void;
    const failureBarrier = new Promise<void>(resolve => { releaseFailure = resolve; });
    const failureDeliveries: { body: unknown; key: string }[] = [];
    await page.route("**/api/queue/entries/*/fail", async route => {
      failureCount += 1;
      failureDeliveries.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
      if (mode === "reload" && !allowFailures) {
        await route.fulfill({ status: 503, json: { detail: "Audit holds failure persistence" } });
        return;
      }
      if (failureCount === 1) {
        if (mode === "rejected") {
          await route.fulfill({ status: 503, json: { detail: "Audit rejects first failure" } });
          return;
        }
        if (mode === "lost-response") {
          const committed = await route.fetch();
          expect(committed.ok()).toBe(true);
          await route.abort("failed");
          return;
        }
        await failureBarrier;
        if (mode === "timeout") {
          await route.abort("timedout").catch(() => undefined);
          return;
        }
      }
      await route.continue().catch(() => undefined);
    });
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
      await page.setViewportSize({ width, height: 900 });
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
      await page.clock.install();
      await page.clock.pauseAt(new Date(Date.now() + 1000));
      await play("a3", "b4");
      await expect(page.locator(".feedback.wrong")).toContainText("That move is not the puzzle solution");
      await expect(page.locator(".feedback-icon")).toHaveText("×");
      await expect.poll(() => visibleTacticPieces(page)).toEqual(expectedPieces);
      await expect.poll(() => failureCount).toBeGreaterThan(0);
      const retained = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1") ?? "[]"));
      expect(retained[0]).toMatchObject({ queueEntryId: head.queue_entry_id, backendId: head.id, expectedRevision: head.revision });
      expect(fixture("verify-tactics").reviews).toEqual([]);
      await expect(page.getByRole("button", { name: "Finish on the board", exact: true })).toBeDisabled();
      if (mode === "reload") {
        await page.reload();
        await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
        await expect(page.getByText("Guided attempt resumed", { exact: true })).toBeVisible();
        await expect.poll(() => visibleTacticPieces(page)).toEqual(expectedPieces);
        allowFailures = true;
      }
      if (mode === "lost-response") {
        await expect.poll(async () => (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0].attempt_failed).toBeTruthy();
      }
      if (mode === "competing") {
        const competitor = await request.post(`${api}/cards/${head.id}/review`, {
          headers: { "Idempotency-Key": `audit-competing-${identifier}` },
          data: { queue_entry_id: head.queue_entry_id, expected_revision: head.revision, outcome: "again", guided: true, attempt_id: randomUUID() },
        });
        expect(competitor.ok()).toBe(true);
        await expect.poll(() => fixture("verify-tactics").reviews.length).toBe(1);
      }
      if (mode === "reload") {
        await page.clock.install();
        await page.clock.pauseAt(new Date(Date.now() + 1000));
      }
      if (mode === "timeout") {
        await page.clock.runFor(15001);
        releaseFailure();
        await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1") ?? "[]").length)).toBe(1);
      }
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
      if (mode === "delayed") releaseFailure();
      const reviewResponse = page.waitForResponse(response => response.url().endsWith(`/cards/${identifier}-prepared-000/review`)
        && response.request().method() === "POST");
      await page.clock.runFor(750);
      await page.clock.resume();
      expect((await reviewResponse).ok()).toBe(true);
      await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id", String(head.queue_entry_id));
      expect(deliveries).toHaveLength(1);
      expect(deliveries[0].body).toMatchObject({ outcome: "again", guided: true });
      releaseFailure();
      const beforeReplay = fixture("verify-tactics");
      const durableBeforeReplay = snapshot();
      expect(durableBeforeReplay.reviews).toHaveLength(mode === "competing" ? 2 : 1);
      expect(durableBeforeReplay.reviews[0].slice(2, 4)).toEqual(["again", 1]);
      expect(durableBeforeReplay.queue.filter((row: unknown[]) => row[0] === `${identifier}-prepared-001`)
        .every((row: unknown[]) => row[3] === 0)).toBe(true);
      expect(beforeReplay.reviews).toHaveLength(mode === "competing" ? 2 : 1);
      expect(beforeReplay.reviews[0].slice(1)).toEqual(["again", 1]);
      expect(beforeReplay.receipts).toHaveLength(mode === "competing" ? 2 : 1);
      for (const receipt of beforeReplay.receipts) expect(receipt.slice(3)).toEqual(["again", 1]);
      for (const review of beforeReplay.reviews) expect(review.slice(1)).toEqual(["again", 1]);
      expect(membershipRequests).toEqual([]);
      await page.reload();
      const delivery = deliveries[0];
      const replay = await request.post(delivery.url, { headers: { "Idempotency-Key": delivery.key }, data: delivery.body });
      expect(replay.ok()).toBe(true);
      expect(fixture("verify-tactics")).toEqual(beforeReplay);
      expect(snapshot()).toEqual(durableBeforeReplay);
      const staleFailure = await request.post(`${api}/queue/entries/${head.queue_entry_id}/fail`, {
        headers: { "Idempotency-Key": `audit-stale-${identifier}` },
        data: { card_id: `${identifier}-prepared-001`, expected_revision: head.revision },
      });
      // A stale command may be accepted for asynchronous execution; its receipt must reject it.
      if (staleFailure.status() === 202) {
        const operation = (await staleFailure.json()).operation_id;
        await expect.poll(async () => (await (await request.get(`${api}/operations/${operation}`)).json()).state).toBe("failed");
      } else expect(staleFailure.status()).toBe(409);
      expect(snapshot()).toEqual(durableBeforeReplay);
      await testInfo.attach("tactics-review-proof", { body: JSON.stringify({ mode, beforeReplay, durableBeforeReplay, failureDeliveries, deliveries }), contentType: "application/json" });
    } finally {
      releaseFailure();
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
