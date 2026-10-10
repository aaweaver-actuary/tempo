import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { prepareUI, boardBounds } from "./ui-fixtures";

type CommandSpan = { label: string; trace: string; sql_count: number; sql_ms: number; duration_ms: number;
  broker_wait_ms: number; stages_ms: Record<string, number>; connections: { read_only: boolean; committed: boolean;
    commit_epoch_ms?: number; commit_observed_epoch_ms: number; acquisition_ms: number; execution_ms: number; commit_ms?: number }[] };
const conditions = (process.env.TEMPO_TRAINING_CONDITIONS ?? "prepared").split(",");
if (conditions.some(condition => !["prepared", "refresh", "recovery-held", "workers-held", "integrity-held", "lines-held"].includes(condition)))
  throw new Error("Unknown training measurement condition");
test.describe.configure({ mode: "serial" });
for (const condition of conditions) {
test(`opening_evidence_remains_durable_during_prepared_progression: ${condition}`, async ({ page, request }, testInfo) => {
  test.setTimeout(240_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
  const identifier = `training-transition-${randomUUID()}`;
  const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
  const docker = (...args: string[]) => execFileSync("docker", [...compose, ...args], { encoding: "utf8", timeout: 60_000 });
  const fixture = (action: string, ...args: string[]) => docker("run", "--rm", "--no-deps", "schema", "python", "/source/scripts/check_postgres_training_transition.py", action, identifier, ...args);
  const today = (await (await request.get(`${api}/queue/window?limit=20`)).json()).local_date as string;
  type Sample = { clickEpochMs: number; submissionEpochMs: number; responseEpochMs: number; interactiveEpochMs: number;
    operationId: string; receiptObservationEpochMs: number; queueNetworkMs: number[]; queueProcessingMs: number[];
    nextInputAcceptedEpochMs?: number;
    network: { label: string; serverTiming: string | undefined; sqlCount: number; transactions: number }[] };
  const samples: Sample[] = [];
  let reviewResponseEpochMs = 0;
  let operationId = "";
  let queueNetworkMs: number[] = [];
  let submissionEpochMs = 0;
  let recoveryRequests = 0;
  let network: Sample["network"] = [];
  let firstReview: { url: string; body: string; key: string } | undefined;
  page.on("request", request => {
    if (request.url().endsWith("/review")) {
      operationId = request.headers()["idempotency-key"] ?? ""; submissionEpochMs = Date.now();
      firstReview ??= { url: request.url(), body: request.postData()!, key: operationId };
    }
    if (request.url().includes("/api/opening-evidence/checkpoints")) recoveryRequests++;
  });
  page.on("response", response => {
    const path = new URL(response.url()).pathname;
    const label = path.endsWith("/review") ? "review" : path === "/api/queue/window" ? "queue" :
      path === "/api/repertoire/lines" ? "repertoire-lines" : path === "/api/repertoires" ? "integrity" :
      path === "/api/opening-evidence/checkpoints" ? "recovery" : null;
    if (label) network.push({ label, serverTiming: response.headers()["server-timing"],
      sqlCount: Number(response.headers()["x-tempo-sql-count"] ?? 0), transactions: Number(response.headers()["x-tempo-transaction-count"] ?? 0) });
    if (response.url().endsWith("/review")) reviewResponseEpochMs = Date.now();
    if (response.url().includes("/api/queue/window")) {
      const timing = response.request().timing();
      queueNetworkMs.push(timing.responseStart - timing.requestStart);
    }
  });
  try {
    docker("stop", "background-worker", "background-scheduler");
    fixture("seed", today);
    if (condition !== "workers-held") docker("start", "background-worker", "background-scheduler");
    if (condition === "lines-held") await page.route("**/api/repertoire/lines", () => new Promise(() => {}));
    await prepareUI(page);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", /\d+/);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    // Real immutable manifests and current-session ownership; only synthetic evidence.
    await expect.poll(() => page.evaluate(async () => {
      const opening = indexedDB.open("tempo-offline-training", 2);
      const database = await new Promise<IDBDatabase>((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error); });
      const read = database.transaction("opening_attempts").objectStore("opening_attempts").getAll();
      return new Promise<number>(resolve => { read.onsuccess = () => { database.close(); resolve(read.result.length); }; });
    })).toBeGreaterThan(0);
    if (condition === "integrity-held") await page.route("**/api/repertoires", () => new Promise(() => {}));
    if (condition === "refresh") await page.route("**/api/cards/*/review", async route => {
      const body = route.request().postDataJSON(); delete body.prepared_next;
      await route.continue({ postData: JSON.stringify(body) });
    });
    await page.evaluate(async holdRecovery => {
      const opening = indexedDB.open("tempo-offline-training", 2);
      const database = await new Promise<IDBDatabase>(resolve => { opening.onsuccess = () => resolve(opening.result); });
      const read = database.transaction("opening_attempts").objectStore("opening_attempts").getAll();
      const attempts = await new Promise<{ manifest: { decisions: { decision_index: number; decision_id: string; expected_uci: string }[] } }[]>(resolve => { read.onsuccess = () => resolve(read.result); });
      const header = attempts[0];
      const decision = header.manifest.decisions[0];
      const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
      const attemptIds: string[] = [];
      for (let index = 0; index < 100; index++) {
        const attemptId = crypto.randomUUID();
        attemptIds.push(attemptId);
        transaction.objectStore("opening_attempts").put({ ...header, attempt_id: attemptId, final_sequence: 1, delivery_state: "pending",
          terminal: { state: "partial", final_sequence: 1, ended_at: new Date().toISOString() } });
        transaction.objectStore("opening_events").put({ attempt_id: attemptId, sequence: 1, decision_index: decision.decision_index,
          decision_id: decision.decision_id, expected_uci: decision.expected_uci, kind: "assistance", observed_at: new Date().toISOString(),
          response_uci: null, assistance: "hint", disposition: null });
      }
      await new Promise<void>((resolve, reject) => { transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error); });
      database.close();
      if (holdRecovery) {
        const releases: (() => void)[] = [];
        await Promise.all(attemptIds.map(attemptId => new Promise<void>(resolve => {
          void navigator.locks.request(`tempo-opening-attempt:${attemptId}`, async () => {
            await new Promise<void>(release => { releases.push(release); resolve(); });
          });
        })));
        Reflect.set(window, "releaseMeasuredRecovery", () => releases.forEach(release => release()));
      }
      window.dispatchEvent(new Event("online"));
    }, condition === "recovery-held");
    const clockReads = Array.from({ length: 5 }, () => {
      const sent = Date.now();
      const worker = Number(docker("exec", "-T", "foreground-worker", "python", "-c", "import time; print(time.time()*1000)").trim());
      const received = Date.now();
      return { offsetMs: worker - (sent + received) / 2, uncertaintyMs: (received - sent) / 2 + 1 };
    }).sort((left, right) => left.uncertaintyMs - right.uncertaintyMs);
    for (let index = 0; index < 53; index++) {
      const currentBoard = await boardBounds(page);
      await page.mouse.click(currentBoard.x + currentBoard.width * 4.5 / 8, currentBoard.y + currentBoard.height * 6.5 / 8);
      await page.mouse.click(currentBoard.x + currentBoard.width * 4.5 / 8, currentBoard.y + currentBoard.height * 4.5 / 8);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4P3/);
      if (samples.length) samples.at(-1)!.nextInputAcceptedEpochMs = Date.now();
      const previousEntry = await page.locator("#train").getAttribute("data-queue-entry-id");
      queueNetworkMs = []; network = []; reviewResponseEpochMs = 0;
      await page.evaluate(() => {
        performance.clearMeasures("tempo.queue.processing");
        performance.clearMarks("tempo.review.confirmed");
        const startedEntry = document.querySelector("#train")?.getAttribute("data-queue-entry-id");
        Reflect.set(window, "transitionInteractiveEpochMs", 0);
        const observer = new MutationObserver(() => {
          if (document.querySelector("#train")?.getAttribute("data-queue-entry-id") !== startedEntry &&
              document.querySelector('.board-frame[data-input-enabled="true"]')) {
            requestAnimationFrame(() => { Reflect.set(window, "transitionInteractiveEpochMs", performance.timeOrigin + performance.now()); observer.disconnect(); });
          }
        });
        observer.observe(document.body, { subtree: true, attributes: true, childList: true });
      });
      const clickEpochMs = Date.now();
      await page.getByRole("button", { name: "Correct", exact: true }).click();
      await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id", previousEntry!);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      await expect.poll(() => page.evaluate(() => Reflect.get(window, "transitionInteractiveEpochMs"))).toBeGreaterThan(0);
      const interactiveEpochMs = await page.evaluate(() => Reflect.get(window, "transitionInteractiveEpochMs") as number);
      const queueProcessingMs = await page.evaluate(() => performance.getEntriesByName("tempo.queue.processing").map(entry => entry.duration));
      const receiptObservationEpochMs = await page.evaluate(() => {
        const marks = performance.getEntriesByName("tempo.review.confirmed");
        return marks.length ? performance.timeOrigin + marks.at(-1)!.startTime : 0;
      });
      if (index >= 3) samples.push({ clickEpochMs, submissionEpochMs, responseEpochMs: reviewResponseEpochMs, interactiveEpochMs, operationId,
        receiptObservationEpochMs, queueNetworkMs: [...queueNetworkMs], queueProcessingMs, network: [...network] });
    }
    // A ready board must accept a real browser move, not merely mount a new card.
    const bounds = await boardBounds(page);
    await page.mouse.click(bounds.x + bounds.width * 4.5 / 8, bounds.y + bounds.height * 6.5 / 8);
    await page.mouse.click(bounds.x + bounds.width * 4.5 / 8, bounds.y + bounds.height * 4.5 / 8);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4P3/);
    samples.at(-1)!.nextInputAcceptedEpochMs = Date.now();
    // Stop current UI timers/requests before service restart and fixture teardown.
    await page.close();
    const workerLog = docker("logs", "--no-color", "foreground-worker");
    const spans: CommandSpan[] = workerLog.split("\n").filter(line => line.includes("training_timing ")).map(line => JSON.parse(line.slice(line.indexOf("training_timing ") + 16)));
    const measured = samples.map(sample => {
      const trace = createHash("sha256").update(sample.operationId).digest("hex").slice(0, 16);
      const span = spans.find(span => span.label === "cards.review" && span.trace === trace);
      const commit = span?.connections.filter(connection => !connection.read_only && connection.committed).at(-1)?.commit_epoch_ms;
      return { ...sample, command: span, commitEpochMs: commit ?? null,
        commitToInteractiveMs: commit ? sample.interactiveEpochMs - (commit - clockReads[0].offsetMs) : null,
        commitToInputAcceptedMs: commit ? sample.nextInputAcceptedEpochMs! - (commit - clockReads[0].offsetMs) : null,
        clickToInteractiveMs: sample.interactiveEpochMs - sample.clickEpochMs };
    });
    const report = { condition, revision: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
      dirtyPatchSha256: createHash("sha256").update(execFileSync("git", ["diff", "HEAD"], { encoding: "utf8" })).digest("hex"),
      fixture: { prepared: 64, competingJobs: 3000, lockedCards: 15000, journals: 100, warmup: 3, samples: 50 },
      clock: { description: "Successful psycopg commit completion, separately from response receipt and pool cleanup. Worker/host offset bracketed by five reads; smallest uncertainty retained.", ...clockReads[0] }, measured, recoveryRequests,
      persisted: JSON.parse(fixture("verify")) };
    const output = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
    mkdirSync(output, { recursive: true }); writeFileSync(join(output, `training-transition-${condition}.json`), JSON.stringify(report, null, 2));
    await testInfo.attach("training-transition", { body: JSON.stringify(report), contentType: "application/json" });
    expect(report.persisted.reviews).toBe(53);
    expect(report.persisted.complete_evidence).toBe(53);
    expect(report.persisted.completion_events).toBe(53);
    if (process.env.TEMPO_TRAINING_TIMINGS === "1" && condition !== "refresh") {
      expect(measured.every(sample => sample.commitToInteractiveMs !== null && sample.commitToInteractiveMs >= 0)).toBe(true);
      const durations = measured.map(sample => sample.commitToInputAcceptedMs!).sort((left, right) => left - right);
      expect(durations[Math.ceil(durations.length * .95) - 1] + clockReads[0].uncertaintyMs).toBeLessThan(500);
    }
    if (condition === "prepared") {
      // Restart the owned service boundary and replay a response-lost frozen delivery.
      docker("restart", "api", "foreground-worker");
      await expect.poll(async () => {
        const health = await request.get(`${api}/health`);
        return health.ok() ? "ready" : await health.text();
      }, { timeout: 60_000, message: "Restarted services must regain authoritative readiness" }).toBe("ready");
      const replay = await request.post(firstReview!.url, { headers: { "Idempotency-Key": firstReview!.key,
        "Content-Type": "application/json" }, data: firstReview!.body });
      expect(replay.ok()).toBe(true);
      expect((await replay.json()).prepared_continuation?.card_id).toContain("-prepared-001");
      expect(JSON.parse(fixture("verify"))).toMatchObject({ reviews: 53, complete_evidence: 53, completion_events: 53 });
    }
  } finally {
    await page.evaluate(() => (Reflect.get(window, "releaseMeasuredRecovery") as (() => void) | undefined)?.()).catch(() => undefined);
    await page.close();
    const ownedConsumers = ["api", "foreground-worker", "background-worker", "background-scheduler", "defense-engine", "maia-worker"];
    docker("stop", ...ownedConsumers);
    try { fixture("cleanup"); }
    finally { docker("start", ...ownedConsumers); }
  }
});
}
