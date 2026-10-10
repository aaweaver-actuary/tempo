import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { prepareUI, boardBounds } from "./ui-fixtures";

type CommandSpan = { label: string; trace: string; started_epoch_ms: number; sql_count: number; sql_ms: number; duration_ms: number;
  broker_wait_ms: number; stages_ms: Record<string, number>; connections: { read_only: boolean; committed: boolean;
    commit_epoch_ms?: number; commit_observed_epoch_ms: number; acquisition_ms: number; execution_ms: number; commit_ms?: number }[] };
const conditions = (process.env.TEMPO_TRAINING_CONDITIONS ?? "prepared").split(",");
if (conditions.some(condition => !["prepared", "refresh", "recovery-held", "workers-held", "integrity-held", "lines-held"].includes(condition)))
  throw new Error("Unknown training measurement condition");
// The configured single worker runs independent conditions serially even if one fails.
for (const condition of conditions) {
test(`opening_evidence_remains_durable_during_prepared_progression: ${condition}`, async ({ page, request }, testInfo) => {
  test.setTimeout(240_000);
  const warmup = process.env.TEMPO_TRAINING_TIMINGS === "1" ? 3 : 0;
  const measuredCount = process.env.TEMPO_TRAINING_TIMINGS === "1" ? 50 : 3;
  const transitionCount = warmup + measuredCount;
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable runner is required");
  const identifier = process.env.TEMPO_TRAINING_FIXTURE_ID ?? `training-transition-${randomUUID()}`;
  if (!/^training-transition-[a-f0-9-]+$/.test(identifier) || (process.env.TEMPO_TRAINING_FIXTURE_ID && conditions.length !== 1))
    throw new Error("A fixed fixture requires one condition in a fresh disposable invocation");
  const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
  const docker = (...args: string[]) => execFileSync("docker", [...compose, ...args], { encoding: "utf8", timeout: 60_000, maxBuffer: 8 * 1024 * 1024 });
  const fixture = (action: string, ...args: string[]) => docker("run", "--rm", "--no-deps", "schema", "python", "/source/scripts/check_postgres_training_transition.py", action, identifier, ...args);
  await expect.poll(async () => {
    const health = await request.get(`${api}/health`);
    return health.ok() ? "ready" : await health.text();
  }, { timeout: 60_000, message: "The restored API and proxy must be ready before a new condition" }).toBe("ready");
  const todayResponse = await request.get(`${api}/queue/window?limit=20`);
  expect(todayResponse.ok()).toBe(true);
  const today = (await todayResponse.json()).local_date as string;
  const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  type Sample = { clickEpochMs: number; submissionEpochMs: number; responseEpochMs: number; interactiveEpochMs: number;
    operationId: string; receiptObservationEpochMs: number; queueNetworkMs: number[]; queueProcessingMs: number[];
    nextInputAcceptedEpochMs?: number;
    nativeInputDispatchEpochMs?: number; nativeInputAcceptedEpochMs?: number;
    network: { label: string; serverTiming: string | undefined; sqlCount: number; transactions: number }[] };
  const samples: Sample[] = [];
  let reviewResponseEpochMs = 0;
  let operationId = "";
  let queueNetworkMs: number[] = [];
  let submissionEpochMs = 0;
  let recoveryRequests = 0;
  let network: Sample["network"] = [];
  let firstReview: { url: string; body: string; key: string } | undefined;
  const submittedEvidenceEvents = new Map<string, number>();
  page.on("request", request => {
    if (request.url().endsWith("/review")) {
      operationId = request.headers()["idempotency-key"] ?? ""; submissionEpochMs = Date.now();
      firstReview ??= { url: request.url(), body: request.postData()!, key: operationId };
      const body = request.postDataJSON();
      if (body.opening_evidence_completion) submittedEvidenceEvents.set(body.attempt_id, body.opening_evidence_completion.events.length);
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
    const snapshot = JSON.parse(fixture("seed", today));
    if (condition !== "workers-held") docker("start", "background-worker", "background-scheduler");
    if (condition === "lines-held") await page.route("**/api/repertoire/lines", () => new Promise(() => {}));
    await prepareUI(page);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", /\d+/);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    const playPreparedMove = async () => {
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", startingFen);
      const board = await boardBounds(page);
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 6.5 / 8);
      await page.mouse.click(board.x + board.width * 4.5 / 8, board.y + board.height * 4.5 / 8);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4P3/);
    };
    // Established cards create their current durable journal on first input.
    await playPreparedMove();
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
    const readClockBracket = async () => {
      const clockReads: { offsetMs: number; uncertaintyMs: number }[] = [];
      for (let index = 0; process.env.TEMPO_TRAINING_TIMINGS === "1" && index < 5; index++) {
        const clockIdentity = randomUUID();
        const sent = Date.now();
        const response = await request.get(`${api}/queue/window?limit=20`, { headers: { "Idempotency-Key": clockIdentity } });
        expect(response.ok()).toBe(true);
        await response.body();
        const received = Date.now();
        const serverStarted = Number(response.headers()["x-tempo-clock-started-ms"]);
        const serverFinished = Number(response.headers()["x-tempo-clock-finished-ms"]);
        expect(Number.isFinite(serverStarted) && Number.isFinite(serverFinished)).toBe(true);
        clockReads.push({ offsetMs: (serverStarted + serverFinished - sent - received) / 2,
          uncertaintyMs: Math.max(0, received - sent - (serverFinished - serverStarted)) / 2 + 1 });
      }
      clockReads.sort((left, right) => left.uncertaintyMs - right.uncertaintyMs);
      if (process.env.TEMPO_TRAINING_TIMINGS === "1") expect(clockReads).toHaveLength(5);
      return clockReads[0] ?? { offsetMs: 0, uncertaintyMs: 0 };
    };
    const startingClock = await readClockBracket();
    for (let index = 0; index < transitionCount; index++) {
      await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeEnabled();
      const previousEntry = await page.locator("#train").getAttribute("data-queue-entry-id");
      queueNetworkMs = []; network = []; reviewResponseEpochMs = 0;
      await page.evaluate(expectedStartingFen => {
        performance.clearMeasures("tempo.queue.processing");
        performance.clearMarks("tempo.review.confirmed");
        const startedEntry = document.querySelector("#train")?.getAttribute("data-queue-entry-id");
        Reflect.set(window, "transitionInteractiveEpochMs", 0);
        Reflect.set(window, "nativeInputDispatchEpochMs", 0);
        Reflect.set(window, "nativeInputAcceptedEpochMs", 0);
        let readinessScheduled = false;
        const captureInput = (event: PointerEvent) => {
          if (document.querySelector("#train")?.getAttribute("data-queue-entry-id") !== startedEntry &&
              event.target instanceof Element && event.target.closest(".board-frame"))
            Reflect.set(window, "nativeInputDispatchEpochMs", performance.timeOrigin + performance.now());
        };
        document.addEventListener("pointerdown", captureInput, true);
        const observer = new MutationObserver(() => {
          const nextEntryActive = document.querySelector("#train")?.getAttribute("data-queue-entry-id") !== startedEntry;
          const board = document.querySelector('.board-frame[data-input-enabled="true"]');
          if (!readinessScheduled && nextEntryActive && board?.getAttribute("data-fen") === expectedStartingFen) {
            readinessScheduled = true;
            requestAnimationFrame(() => { Reflect.set(window, "transitionInteractiveEpochMs", performance.timeOrigin + performance.now()); });
          }
          if (nextEntryActive && Reflect.get(window, "nativeInputDispatchEpochMs") && board?.getAttribute("data-fen")?.includes("4P3")) {
            Reflect.set(window, "nativeInputAcceptedEpochMs", performance.timeOrigin + performance.now());
            observer.disconnect(); document.removeEventListener("pointerdown", captureInput, true);
          }
        });
        observer.observe(document.body, { subtree: true, attributes: true, childList: true });
      }, startingFen);
      const clickEpochMs = Date.now();
      await page.getByRole("button", { name: "Correct", exact: true }).click();
      await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id", previousEntry!);
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
      // Exercise native input immediately; gathering metrics must not delay it.
      await playPreparedMove();
      const nextInputAcceptedEpochMs = Date.now();
      await expect.poll(() => page.evaluate(() => Reflect.get(window, "transitionInteractiveEpochMs"))).toBeGreaterThan(0);
      const interactiveEpochMs = await page.evaluate(() => Reflect.get(window, "transitionInteractiveEpochMs") as number);
      const nativeInput = await page.evaluate(() => ({ nativeInputDispatchEpochMs: Reflect.get(window, "nativeInputDispatchEpochMs") as number,
        nativeInputAcceptedEpochMs: Reflect.get(window, "nativeInputAcceptedEpochMs") as number }));
      expect(nativeInput.nativeInputAcceptedEpochMs).toBeGreaterThan(nativeInput.nativeInputDispatchEpochMs);
      expect(nativeInput.nativeInputDispatchEpochMs).toBeGreaterThan(0);
      const queueProcessingMs = await page.evaluate(() => performance.getEntriesByName("tempo.queue.processing").map(entry => entry.duration));
      const receiptObservationEpochMs = await page.evaluate(() => {
        const marks = performance.getEntriesByName("tempo.review.confirmed");
        return marks.length ? performance.timeOrigin + marks.at(-1)!.startTime : 0;
      });
      if (index >= warmup) samples.push({ clickEpochMs, submissionEpochMs, responseEpochMs: reviewResponseEpochMs, interactiveEpochMs, operationId,
        receiptObservationEpochMs, nextInputAcceptedEpochMs, ...nativeInput, queueNetworkMs: [...queueNetworkMs], queueProcessingMs, network: [...network] });
    }
    // Stop current UI timers/requests before service restart and fixture teardown.
    await page.close();
    const endingClock = await readClockBracket();
    const workerLog = docker("logs", "--no-color", "foreground-worker");
    const spans: CommandSpan[] = workerLog.split("\n").filter(line => line.includes("training_timing ")).map(line => JSON.parse(line.slice(line.indexOf("training_timing ") + 16)));
    const apiSpans: CommandSpan[] = docker("logs", "--no-color", "api").split("\n").filter(line => line.includes("training_timing ")).map(line => JSON.parse(line.slice(line.indexOf("training_timing ") + 16)));
    const driftMs = endingClock.offsetMs - startingClock.offsetMs;
    const clock = { offsetMs: (startingClock.offsetMs + endingClock.offsetMs) / 2,
      uncertaintyMs: Math.max(startingClock.uncertaintyMs, endingClock.uncertaintyMs) + Math.abs(driftMs) / 2,
      starting: startingClock, ending: endingClock, driftMs };
    const measured = samples.map(sample => {
      const trace = createHash("sha256").update(sample.operationId).digest("hex").slice(0, 16);
      const span = spans.find(span => span.label === "cards.review" && span.trace === trace);
      const commit = span?.connections.filter(connection => !connection.read_only && connection.committed).at(-1)?.commit_epoch_ms;
      return { ...sample, command: span, apiRequest: apiSpans.find(span => span.label === "review-request" && span.trace === trace), commitEpochMs: commit ?? null,
        commitToInteractiveMs: commit ? sample.interactiveEpochMs - (commit - clock.offsetMs) : null,
        commitToInputAcceptedMs: commit ? sample.nextInputAcceptedEpochMs! - (commit - clock.offsetMs) : null,
        nativeInputProcessingMs: sample.nativeInputAcceptedEpochMs! - sample.nativeInputDispatchEpochMs!,
        clickToInteractiveMs: sample.interactiveEpochMs - sample.clickEpochMs };
    });
    const report = { condition, revision: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
      dirtyPatchSha256: createHash("sha256").update(execFileSync("git", ["diff", "HEAD"], { encoding: "utf8" })).digest("hex"),
      fixture: { prepared: 64, moves: ["e4", "e5", "Nf3"], establishedScheduling: true, competingJobs: 3000, lockedCards: 15000, journals: 100, warmup, samples: measuredCount, snapshot },
      clock: { description: "Successful psycopg commit completion, separately from response receipt and pool cleanup. API and worker share the disposable Docker VM clock. Five HTTP exchanges at each end bracket host/VM offset after subtracting observed server duration; uncertainty includes endpoint drift. Native-input time includes test-driver scheduling.", ...clock }, measured, recoveryRequests,
      persisted: JSON.parse(fixture("verify")) };
    const output = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
    mkdirSync(output, { recursive: true }); writeFileSync(join(output, `training-transition-${condition}.json`), JSON.stringify(report, null, 2));
    await testInfo.attach("training-transition", { body: JSON.stringify(report), contentType: "application/json" });
    expect(report.persisted.reviews).toBe(transitionCount);
    expect(report.persisted.complete_evidence).toBe(transitionCount);
    const expectedEvents = [...submittedEvidenceEvents.values()].reduce((total, count) => total + count, 0);
    expect(submittedEvidenceEvents.size).toBe(transitionCount);
    expect(expectedEvents).toBeGreaterThanOrEqual(transitionCount);
    expect(report.persisted.completion_events).toBe(expectedEvents);
    if (process.env.TEMPO_TRAINING_TIMINGS === "1" && condition !== "refresh") {
      expect(measured.every(sample => sample.commitToInteractiveMs !== null && sample.commitToInteractiveMs >= 0)).toBe(true);
      const durations = measured.map(sample => sample.commitToInteractiveMs!).sort((left, right) => left - right);
      expect(durations[Math.ceil(durations.length * .95) - 1] + clock.uncertaintyMs).toBeLessThan(500);
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
      expect(JSON.parse(fixture("verify"))).toMatchObject({ reviews: transitionCount, complete_evidence: transitionCount, completion_events: expectedEvents });
    }
  } finally {
    await page.evaluate(() => (Reflect.get(window, "releaseMeasuredRecovery") as (() => void) | undefined)?.()).catch(() => undefined);
    await page.close();
    // Keep API/proxy addressing stable; no UI requests survive page.close().
    const ownedConsumers = ["background-worker", "background-scheduler", "defense-engine", "maia-worker"];
    docker("stop", ...ownedConsumers);
    try { fixture("cleanup"); }
    finally { docker("start", ...ownedConsumers); }
  }
});
}
