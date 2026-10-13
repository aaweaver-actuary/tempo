import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "./observability";
import { boardBounds } from "./ui-fixtures";
import { RedisAdmissionCleanup } from "./support/redis-admission-cleanup";

const execute = promisify(execFile);

test("test_issue218_redis_outage_retains_review_intent_and_recovers_without_duplicate", async ({ page, request }, testInfo) => {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("An invocation-owned disposable project is required");
  expect(testInfo.config.workers, "Redis faults require the serial disposable runner").toBe(1);
  const api = `${process.env.TEMPO_DOCKER_URL}/api`;
  const health = await request.get(`${api}/health`);
  expect(health.ok()).toBe(true);
  expect((await health.json()).test_instance).toBe(true);
  const identity = `redis-admission-${randomUUID()}`;
  const compose = ["compose", "-f", "docker-compose.postgres.test.yml", "-p", project];
  const docker = (...args: string[]) => execute("docker", [...compose, ...args], { timeout: 30_000 });
  const fixture = async (action: string) => {
    const result = await docker("run", "--rm", "--no-deps", "-e", `TEMPO_TEST_COMPOSE_PROJECT=${project}`,
      "schema", "python", "/source/scripts/check_postgres_redis_admission.py", action, identity);
    return result.stdout.trim() ? JSON.parse(result.stdout) : null;
  };
  // There is no dispatched operation during either fault. Recovery can proceed
  // immediately; the five-second configured recovery interval plus the existing
  // 15-second foreground watchdog bound confirmation without a five-minute lease.
  const recoveryDeadline = 5_000 + 15_000;
  const evidence: unknown[] = [];
  const cleanup = new RedisAdmissionCleanup({
    stopRedis: () => docker("stop", "redis"),
    pauseRedis: () => docker("pause", "redis"),
    startRedis: () => docker("start", "redis"),
    unpauseRedis: () => docker("unpause", "redis"),
    closePage: () => page.close(),
    // Recover by identity even when committed seed stdout was lost.
    cleanupFixture: () => fixture("cleanup"),
  });
  await cleanup.run(async () => {
    await fixture("seed");
    for (const fault of ["refusal", "nonreply"] as const) {
      await page.goto("/");
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", /\d+/);
      const queueEntry = await page.locator("#train").getAttribute("data-queue-entry-id");
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      const baseline = await fixture("inspect");
      const posts: { key: string; body: string; status: number; result: unknown }[] = [];
      const listener = async (response: import("@playwright/test").Response) => {
        const submitted = response.request();
        if (submitted.method() === "POST" && /\/api\/cards\/[^/]+\/review$/.test(new URL(submitted.url()).pathname))
          posts.push({ key: submitted.headers()["idempotency-key"], body: submitted.postData()!, status: response.status(), result: await response.json() });
      };
      await cleanup.withResponseListener(() => page.off("response", listener), async () => {
        page.on("response", listener);
        if (fault === "refusal") await cleanup.stopRedis();
        else await cleanup.pauseRedis();
        const started = Date.now();
        const board = await boardBounds(page);
        await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 6.5 / 8);
        await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 4.5 / 8);
        await expect.poll(() => posts.length).toBeGreaterThan(0);
        const delivery = posts[0];
        expect(delivery.status).toBe(503);
        expect(delivery.result).toEqual({ detail: "Foreground admission unavailable; check Redis service status and retry with the same Idempotency-Key" });
        expect(Date.now() - started).toBeLessThan(15_000);
        const retained = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"));
        expect(retained).toHaveLength(1);
        expect(retained[0].backendId).toBe(`${identity}-${fault === "refusal" ? 0 : 1}`);
        expect(retained[0].attemptId).toMatch(/^[a-f0-9-]{36}$/);
        expect(delivery.key).toBe(`review-attempt:${retained[0].attemptId}`);
        await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", queueEntry!);
        expect(await fixture("inspect")).toEqual(baseline);
        await page.reload();
        await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"))).toEqual(retained);
        // After a true reload no successor is usable while admission is unavailable.
        await expect(page.locator(".board-frame[data-input-enabled='true']")).toHaveCount(0);
        expect(await fixture("inspect")).toEqual(baseline);
        await cleanup.restoreRedis();
        await expect.poll(async () => {
          try { return (await request.get(`${api}/health`)).ok(); } catch { return false; }
        }, { timeout: recoveryDeadline }).toBe(true);
        await page.reload();
        await expect.poll(async () => (await fixture("inspect")).reviews.length, { timeout: recoveryDeadline }).toBe(baseline.reviews.length + 1);
        await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]")), { timeout: recoveryDeadline }).toEqual([]);
        const accepted = await request.post(`${api}/cards/${retained[0].backendId}/review`, { headers: { "Idempotency-Key": delivery.key, "Content-Type": "application/json" }, data: delivery.body });
        expect(accepted.status()).toBe(200);
        const acceptedResult = await accepted.json();
        expect(acceptedResult.persisted).toBe(true);
        if (!JSON.parse(delivery.body).prepared_next) expect(acceptedResult).not.toHaveProperty("prepared_continuation");
        const subsequentOperation = randomUUID();
        const replay = await request.post(`${api}/cards/${retained[0].backendId}/review`, { headers: { "Idempotency-Key": subsequentOperation, "Content-Type": "application/json" }, data: delivery.body });
        expect(replay.status()).toBe(200);
        expect(await replay.json()).toEqual(acceptedResult);
        const recovered = await fixture("inspect");
        expect(recovered.reviews).toHaveLength(baseline.reviews.length + 1);
        expect(recovered.receipts).toHaveLength(baseline.receipts.length + 1);
        expect(recovered.operations.filter((row: string[]) => row[0] === delivery.key || row[0] === subsequentOperation).map((row: string[]) => row[1])).toEqual(["complete", "complete"]);
        expect(JSON.parse(recovered.receipts.find((row: unknown[]) => row[0] === retained[0].attemptId)[3])).toEqual(acceptedResult);
        evidence.push({ fault, queueEntry, delivery, retained, acceptedResult, subsequentOperation, recovered });
      });
    }
    const directory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/issue-225";
    mkdirSync(directory, { recursive: true });
    const proof = JSON.stringify({ project, recoveryDeadline, evidence }, null, 2);
    writeFileSync(join(directory, "native-recovery.json"), proof);
    await testInfo.attach("redis-admission-native-proof", { body: proof, contentType: "application/json" });
  });
});
