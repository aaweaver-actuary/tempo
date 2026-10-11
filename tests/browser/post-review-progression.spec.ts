import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { prepareUI, boardBounds } from "./ui-fixtures";

for (const failure of ["prepared abort", "fallback abort", "fallback timeout"] as const) {
  test(`issue186_completed_tactic_has_bounded_progression: ${failure}`, async ({ page, request }, testInfo) => {
    test.setTimeout(180_000);
    const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
    if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
    const identifier = `training-transition-${randomUUID()}`;
    const docker = (...arguments_: string[]) => promisify(execFile)("docker", ["compose", "-p", project,
      "-f", "docker-compose.postgres.test.yml", ...arguments_], { encoding: "utf8", timeout: 60_000 });
    const fixture = async (action: string, ...arguments_: string[]) => JSON.parse((await docker("run", "--rm", "--no-deps", "schema",
      "python", "/source/scripts/check_postgres_training_transition.py", action, identifier, ...arguments_)).stdout);
    let displaced: number[][] = [];
    let blockQueue = false;
    let queueFailures = 0;
    let reviewPosts = 0;
    let delivery: { url: string; key: string; body: string } | undefined;
    const heldRequests: (() => Promise<void>)[] = [];
    const consumers = ["background-worker", "background-scheduler", "defense-engine", "maia-worker"];
    try {
      const todayResponse = await request.get(`${api}/queue/window?limit=20`);
      expect(todayResponse.ok()).toBe(true);
      const today = (await todayResponse.json()).local_date as string;
      await docker("stop", ...consumers);
      displaced = (await fixture("seed-progression", today)).displaced_queue_positions;
      await expect.poll(async () => {
        const response = await request.get(`${api}/queue/window?limit=20`);
        return response.ok() ? (await response.json()).cards?.[0]?.id : `HTTP ${response.status()}`;
      }, { timeout: 60_000 }).toBe(`${identifier}-prepared-000`);
      await prepareUI(page);
      await page.route("**/api/queue/window?**", async route => {
        if (blockQueue) {
          queueFailures++;
          if (failure === "fallback timeout") {
            heldRequests.push(() => route.abort("aborted").catch(() => undefined));
            return;
          }
          await route.abort("aborted");
          return;
        }
        const response = await route.fetch();
        if (failure !== "prepared abort" && reviewPosts === 0) {
          const body = await response.json();
          // Only A is cached. Authoritative issuance and total count remain real;
          // no prepared candidate can be sent in the durable review envelope.
          await route.fulfill({ response, json: { ...body, cards: body.cards.slice(0, 1) } });
        } else await route.fulfill({ response });
      });
      page.on("request", submitted => {
        if (submitted.method() === "POST" && /\/api\/cards\/[^/]+\/review$/.test(new URL(submitted.url()).pathname)) reviewPosts++;
        if (submitted.url().endsWith(`/cards/${identifier}-prepared-000/review`)) {
          delivery = { url: submitted.url(), key: submitted.headers()["idempotency-key"], body: submitted.postData()! };
        }
      });
      await page.goto("/");
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", /\d+/);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      const completedEntry = await page.locator("#train").getAttribute("data-queue-entry-id");
      if (failure === "fallback timeout") await page.clock.install();
      blockQueue = true;
      const board = await boardBounds(page);
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 6.5 / 8);
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 4.5 / 8);
      // Completion normally schedules its review after the feedback pause.
      if (failure === "fallback timeout") await page.clock.runFor(1_000);
      await expect.poll(() => queueFailures).toBeGreaterThan(0);
      if (failure === "prepared abort") {
        await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id", completedEntry!);
        await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
        await expect(page.getByRole("button", { name: "Retry loading the queue", exact: true })).toBeVisible();
      } else {
        if (failure === "fallback timeout") {
          await page.clock.fastForward(15_001);
          await expect.poll(() => queueFailures).toBe(2);
          await page.clock.fastForward(15_001);
        }
        await expect(page.getByRole("button", { name: "Retry loading the queue", exact: true })).toBeVisible();
        expect(queueFailures).toBe(2);
        await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", completedEntry!);
      }
      const lifecycle = await page.evaluate(entry => JSON.parse(localStorage.getItem("tempo-notifications-v1") ?? "[]")
        .find((record: { key: string }) => record.key === `review-queue:${entry}`), completedEntry);
      expect(lifecycle.active).toBe(false);
      expect(lifecycle.message).not.toBe("Result saved. Loading the next card…");
      const durableResult = await fixture("verify-progression");
      expect(durableResult.reviews).toHaveLength(1);
      expect(durableResult.receipts).toHaveLength(1);
      expect(durableResult.review_count).toBe(1);
      expect(durableResult.queue).toContainEqual([Number(completedEntry), "complete"]);
      blockQueue = false;
      for (const abortRequest of heldRequests) await abortRequest();
      await page.getByRole("button", { name: "Retry loading the queue", exact: true }).click();
      if (failure === "prepared abort") await expect(page.getByRole("button", { name: "Retry loading the queue", exact: true })).toHaveCount(0);
      await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id", completedEntry!);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      const nextBoard = await boardBounds(page);
      await page.mouse.click(nextBoard.x + nextBoard.width * 4.5 / 8, nextBoard.y + nextBoard.height * 6.5 / 8);
      await page.mouse.click(nextBoard.x + nextBoard.width * 4.5 / 8, nextBoard.y + nextBoard.height * 4.5 / 8);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4P3/);
      expect(reviewPosts).toBe(1);
      expect(await fixture("verify-progression")).toEqual(durableResult);
      await page.close();
      await docker("start", ...consumers);
      await docker("restart", "api", "foreground-worker");
      await expect.poll(async () => {
        const response = await request.get(`${api}/health`);
        return response.ok() ? "ready" : await response.text();
      }, { timeout: 60_000 }).toBe("ready");
      expect(delivery).toBeDefined();
      const replay = await request.post(delivery!.url, { headers: { "Idempotency-Key": delivery!.key,
        "Content-Type": "application/json" }, data: delivery!.body });
      expect(replay.ok()).toBe(true);
      const receipt = await replay.json();
      expect(receipt.persisted).toBe(true);
      const afterReplay = await fixture("verify-progression");
      expect(afterReplay).toEqual(durableResult);
      await testInfo.attach("issue186-progression-proof", { body: JSON.stringify({ failure, completedEntry,
        reviewPosts, queueFailures, lifecycle, receipt, persisted: afterReplay }), contentType: "application/json" });
    } catch (error) {
      await testInfo.attach("issue186-failure-screen", { body: await page.screenshot().catch(() => Buffer.alloc(0)),
        contentType: "image/png" }).catch(() => undefined);
      throw error;
    } finally {
      await page.close();
      await docker("stop", ...consumers);
      try { await fixture("cleanup", JSON.stringify(displaced)); }
      finally { await docker("start", ...consumers); }
    }
  });
}
