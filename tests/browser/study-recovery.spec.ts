import { test, expect } from "./observability";
import { move } from "./native-board";
import { Chess } from "chess.js";
import { readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

type Card = { id: string; queue_entry_id: number; start_fen: string; moves: string[]; trained_color: "white" | "black" };
type Sample = {
  requestId: string; attemptId: string; cardId: string; entryId: number;
  submittedMs: number; persistedMs?: number; interactiveMs?: number;
  nextEntryId?: number; nextCardId?: string; nextFen?: string; receipt?: Record<string, unknown>;
};
type Trace = {
  cards: Record<string, Card>; samples: Sample[];
  preparations: Array<{ operationId: string; startedMs: number; status?: number }>;
  failures: Array<{ path: string; name: string; atMs: number }>;
  responses: Array<{ path: string; status: number; value: unknown; atMs: number }>;
};

test("packaged recovery records thirty authoritative playable transitions", async ({ page }, testInfo) => {
  const scenarioPath = process.env.TEMPO_STUDY_RECOVERY_SCENARIO;
  test.skip(!scenarioPath, "Requires the source-bound owned recovery assembly");
  if (!scenarioPath) throw new Error("Owned recovery scenario required");
  // Thirty real UI reviews plus controlled provider setup, not a longer product deadline.
  test.setTimeout(10 * 60_000);
  const scenario = JSON.parse(await readFile(scenarioPath, "utf8")) as { version: number; study_only: boolean };
  expect(scenario.version).toBe(1);
  const directory = dirname(scenarioPath);
  const errors: string[] = [];
  const requestFailures: Array<{ url: string; error: string | null }> = [];
  page.on("requestfailed", request => requestFailures.push({ url: request.url(), error: request.failure()?.errorText ?? null }));
  const activeBoard = page.locator('[data-view="train"] .persistent-board-shell[data-board-owner="train"] .board-frame:visible, #train .board-frame:visible').first();
  page.on("pageerror", error => errors.push(error.message));
  async function marker(value: Record<string, unknown>) {
    const path = join(directory, "recovery-browser-ready.json");
    await writeFile(path + ".next", JSON.stringify(value));
    await rename(path + ".next", path);
  }
  async function released(index: number) {
    let releasedValue: { index: number; background?: boolean } | undefined;
    await expect.poll(async () => {
      try {
        const value = JSON.parse(await readFile(join(directory, "recovery-provider-ready.json"), "utf8"));
        if (value.stage === "abort") throw new Error("Owned recovery fixture aborted");
        if (value.stage === "continue") releasedValue = value;
        return value.stage === "continue" ? value.index : -1;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return -1;
        throw error;
      }
    }, { timeout: 45_000 }).toBe(index);
    return releasedValue!;
  }

  await page.addInitScript(() => {
    const trace: Trace = { cards: {}, samples: [], preparations: [], failures: [], responses: [] };
    (window as unknown as { __tempoRecovery: Trace }).__tempoRecovery = trace;
    localStorage.setItem("tempo-sound", "false");
    const original = window.fetch.bind(window);
    const operations = new Map<string, Sample>();
    function saved(sample: Sample, value: unknown) {
      const receipt = value as Record<string, unknown> | undefined;
      if (receipt?.persisted !== true || receipt.attempt_id !== sample.attemptId)
        throw new Error("Recovery measurement did not receive the exact persisted attempt receipt");
      if (sample.persistedMs === undefined) {
        sample.persistedMs = performance.now();
        sample.receipt = receipt;
      }
    }
    window.fetch = async (input: RequestInfo | URL, options?: RequestInit) => {
      const request = input instanceof Request ? input : undefined;
      const url = new URL(request?.url ?? String(input), location.href);
      const method = (options?.method ?? request?.method ?? "GET").toUpperCase();
      const headers = new Headers(options?.headers ?? request?.headers);
      let review: Sample | undefined;
      if (method === "POST" && /^\/api\/cards\/[^/]+\/review$/.test(url.pathname)) {
        const body = JSON.parse(typeof options?.body === "string" ? options.body : "null") as Record<string, unknown>;
        if (!body || typeof body.attempt_id !== "string" || typeof body.queue_entry_id !== "number")
          throw new Error("Recovery measurement requires the actual immutable review request");
        review = { requestId: headers.get("Idempotency-Key") ?? "", attemptId: body.attempt_id,
          cardId: decodeURIComponent(url.pathname.split("/")[3]), entryId: body.queue_entry_id,
          submittedMs: performance.now() };
        trace.samples.push(review);
      }
      const preparation = method === "POST" && url.pathname === "/api/queue/prepare-next"
        ? { operationId: headers.get("Idempotency-Key") ?? "", startedMs: performance.now(), status: undefined as number | undefined } : undefined;
      if (preparation) trace.preparations.push(preparation);
      try {
        const response = await original(input, options);
        if (preparation) preparation.status = response.status;
        if (review || preparation || !response.ok || url.pathname === "/api/queue/window" || url.pathname.startsWith("/api/operations/")) {
          const value = await response.clone().json() as Record<string, unknown>;
          if (!response.ok || value.state === "blocked") trace.responses.push({ path: url.pathname, status: response.status, value, atMs: performance.now() });
          if (review) {
            if (response.status === 202 && typeof value.operation_id === "string") operations.set(value.operation_id, review);
            else if (response.ok) saved(review, value);
          } else if (url.pathname === "/api/queue/window" && response.ok && Array.isArray(value.cards)) {
            for (const card of value.cards as Card[]) trace.cards[String(card.queue_entry_id)] = card;
          } else if (url.pathname.startsWith("/api/operations/") && value.state === "complete") {
            const identity = decodeURIComponent(url.pathname.slice("/api/operations/".length));
            const sample = operations.get(identity);
            if (sample) saved(sample, value.response);
          }
        }
        return response;
      } catch (error) {
        trace.failures.push({ path: url.pathname, name: (error as Error).name, atMs: performance.now() });
        throw error;
      }
    };
    let queued = false;
    const inspect = () => {
      queued = false;
      const sample = trace.samples.find(item => item.persistedMs !== undefined && item.interactiveMs === undefined);
      if (!sample) return;
      const section = document.querySelector<HTMLElement>("#train[data-queue-entry-id]");
      const board = section?.querySelector<HTMLElement>(".board-frame[data-input-enabled='true']")
        ?? section?.closest('[data-view="train"]')?.querySelector<HTMLElement>('.persistent-board-shell[data-board-owner="train"][data-unavailable="false"] .board-frame[data-input-enabled="true"]');
      const next = Number(section?.dataset.queueEntryId);
      const issued = trace.cards[String(next)];
      if (!section || !board || !issued || !board.getClientRects().length || board.closest("[inert]") || !next || next === sample.entryId) return;
      const fen = board.dataset.fen;
      requestAnimationFrame(() => {
        if (section.isConnected && board.isConnected && section.dataset.queueEntryId === String(next)
            && board.dataset.inputEnabled === "true" && board.dataset.fen === fen && !board.closest("[inert]")) {
          sample.nextEntryId = next;
          sample.nextCardId = issued.id;
          sample.nextFen = fen;
          sample.interactiveMs = performance.now();
        }
      });
    };
    const changed = () => { if (!queued) { queued = true; requestAnimationFrame(inspect); } };
    new MutationObserver(changed).observe(document, { subtree: true, childList: true, attributes: true,
      attributeFilter: ["data-queue-entry-id", "data-input-enabled", "data-fen", "inert"] });
  });

  let result: Record<string, unknown> = {};
  try {
    await page.setViewportSize({ width: 1280, height: 844 });
    await page.goto("/");
    await expect(page.locator("#train")).toBeVisible();
    await expect(activeBoard).toHaveAttribute("data-input-enabled", "true");
    const settings = await page.evaluate(async () => (await fetch("/api/settings")).json());
    expect(settings.study_only_mode).toBe(scenario.study_only);
    for (let index = 0; index < 30; index++) {
      const entry = Number(await page.locator("#train").getAttribute("data-queue-entry-id"));
      const card = await page.evaluate(id => (window as unknown as { __tempoRecovery: Trace }).__tempoRecovery.cards[String(id)], entry);
      expect(card, "Every measured visible attempt came from an actual queue response").toBeDefined();
      await marker({ stage: "before-review", index, entry_id: entry, card_id: card.id });
      const instruction = await released(index);
      if (instruction.background) {
        // A real visibility change releases the normal application activity
        // lease; no control endpoint or admission implementation is replaced.
        const away = await page.context().newPage();
        await away.goto("about:blank");
        await away.bringToFront();
        await marker({ stage: "foreground-away", index, entry_id: entry });
        await released(index + 200);
        await page.bringToFront();
        await away.close();
      }
      await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(entry));
      const position = new Chess(card.start_fen);
      for (const uci of card.moves) {
        if ((position.turn() === "w") === (card.trained_color === "white")) {
          await expect(activeBoard).toHaveAttribute("data-input-enabled", "true");
          await expect.poll(async () => (await activeBoard.getAttribute("data-fen"))?.split(" ").slice(0, 4).join(" "))
            .toBe(position.fen().split(" ").slice(0, 4).join(" "));
          await move(page, uci.slice(0, 2), uci.slice(2, 4));
        }
        position.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
      }
      await page.waitForFunction(count => {
        const trace = (window as unknown as { __tempoRecovery: Trace }).__tempoRecovery;
        return trace.samples.length === count && trace.samples[count - 1].interactiveMs !== undefined;
      }, index + 1, { timeout: 15_000 });
      const sample = await page.evaluate(i => (window as unknown as { __tempoRecovery: Trace }).__tempoRecovery.samples[i], index);
      expect(sample.requestId).toBe(`review-attempt:${sample.attemptId}`);
      await marker({ stage: "transition", index, sample });
      await released(index + 100);
    }
    const trace = await page.evaluate(() => (window as unknown as { __tempoRecovery: Trace }).__tempoRecovery);
    const samples = trace.samples.map(sample => ({ ...sample,
      saveMs: sample.persistedMs! - sample.submittedMs,
      nextMs: sample.interactiveMs! - sample.persistedMs!, totalMs: sample.interactiveMs! - sample.submittedMs }));
    const sorted = samples.map(sample => sample.nextMs).sort((a, b) => a - b);
    const preparationTransitions = samples.filter(sample => trace.preparations.some(preparation =>
      preparation.startedMs >= sample.persistedMs! && preparation.startedMs <= sample.interactiveMs!)).length;
    result = { version: 1, study_only: scenario.study_only, samples, preparations: trace.preparations,
      failures: trace.failures, requestFailures, responses: trace.responses, p95Ms: sorted[Math.ceil(.95 * sorted.length) - 1], maxMs: sorted.at(-1),
      percentile_method: "nearest rank ceil(0.95*n)",
      receipt_boundary: "performance.now after the exact persisted receipt clone is parsed, before returning the response to the application",
      preparationTransitions, pageErrors: errors };
    await writeFile(join(directory, "recovery-measurements.json"), JSON.stringify(result, null, 2));
    expect(samples).toHaveLength(30);
    expect(new Set(samples.map(sample => sample.attemptId)).size).toBe(30);
    expect(preparationTransitions).toBeGreaterThanOrEqual(3);
    expect(result.p95Ms).toBeLessThanOrEqual(2_000);
    expect(result.maxMs).toBeLessThan(5_000);
    expect(samples.every(sample => sample.saveMs < 15_000)).toBe(true);
    const acquisition = /\/api\/(?:cards\/[^/]+\/review|queue\/(?:window|prepare-next)|operations\/)/;
    expect(trace.failures.filter(item => acquisition.test(item.path))).toEqual([]);
    expect(requestFailures.filter(item => acquisition.test(new URL(item.url).pathname))).toEqual([]);
    expect(trace.responses.filter(item => acquisition.test(item.path)
      && ([408, 504].includes(item.status) || /timeout|timed.out/i.test(JSON.stringify(item.value))))).toEqual([]);
    expect(errors).toEqual([]);
    await marker({ stage: "complete", result });
  } finally {
    const trace = await page.evaluate(() => (window as unknown as { __tempoRecovery?: Trace }).__tempoRecovery).catch(() => undefined);
    await writeFile(join(directory, "recovery-browser-trace.json"), JSON.stringify({ result, trace, errors, requestFailures }, null, 2));
    await testInfo.attach("recovery-measurements", { body: JSON.stringify({ result, trace, errors, requestFailures }), contentType: "application/json" });
  }
});
