import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { openingDecisionManifestSchema } from "../../app/domain/opening-evidence";
const manifest = openingDecisionManifestSchema.parse(JSON.parse(readFileSync(new URL("../fixtures/opening-evidence-manifest.json", import.meta.url), "utf8")));
import type { OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";
import type { PreparedTraining } from "../../app/lib/offline-training";
import { prepareVisualUI } from "./visual-fixtures";

const today = new Date();
const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
const projection = { state: "ready", generation: 1, updated_at: null, refresh_pending: 0, last_error: null };
const card = { id: manifest.card_id, revision: 3, queue_entry_id: 101,
  start_fen: manifest.decisions[0].fen, moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
  content_type: "opening", repertoire_name: "Shadow opening", repertoire_source: "PGN",
  trained_color: "white", has_study_review: true, scheduling_mode: "normal",
  opening_decision_manifest: manifest, opening_evidence_study_timezone: "America/New_York" };
async function prepareQueue(page: Page) {
  const payload = { local_date: localDate, count: 1, cards: [card], projection };
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: payload }));
  await page.route("**/api/queue/prepared?**", route => route.fulfill({ json: { ...payload, prepared_at: new Date().toISOString() } }));
  await page.route("**/api/repertoire/lines", route => route.fulfill({ json: { lines: [] } }));
}

for (const compactFails of [false, true]) {
test(compactFails
  ? "AS-16 offline compact quota failure blocks advancement until durable retry"
  : "AS-16 offline evidence quota saves a compact phone review and retains its journal after sync", async ({ page, context }) => {
  await context.addInitScript((failCompact) => {
    Object.defineProperty(navigator, "standalone", { value: true, configurable: true });
    Object.defineProperty(navigator, "userAgent", { value: "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15", configurable: true });
    const settings = window as unknown as { offlineEvidenceQuotaMode: string };
    settings.offlineEvidenceQuotaMode = failCompact && sessionStorage.getItem("offline-evidence-capacity-restored") !== "true" ? "both" : "full";
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value, key) {
      if (this.name === "training" && value.attempts?.length &&
        (settings.offlineEvidenceQuotaMode === "both" || JSON.stringify(value).includes("openingEvidenceCompletion")))
        throw new DOMException("Prepared review exceeds quota", "QuotaExceededError");
      return key === undefined ? put.call(this, value) : put.call(this, value, key);
    };
  }, compactFails);
  await prepareQueue(page);
  await page.route("**/api/queue/prepared?**", route => route.fulfill({ json: {
    local_date: localDate, count: 1, cards: [{ ...card, first_correct_at: undefined }], projection, prepared_at: new Date().toISOString() } }));
  const readPhone = () => page.evaluate(() => new Promise<PreparedTraining>(resolve => {
    const request = indexedDB.open("tempo-offline-training", 2);
    request.onsuccess = () => {
      const database = request.result;
      const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      read.onsuccess = () => { database.close(); resolve(read.result); };
    };
  }));
  await page.goto("/");
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect.poll(async () => Boolean(await readPhone())).toBe(true);
  await page.route("**/api/**", route => route.abort("internetdisconnected"));
  await page.reload(); await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  const attemptId = (await savedAttempts(page))[0].attempt_id;
  await move(page, "f1", "b5");
  if (compactFails) {
    await expect(page.getByRole("button", { name: "Retry save", exact: true })).toBeVisible();
    expect((await readPhone()).attempts).toEqual([]);
    expect((await readPhone()).cards[0].queue_entry_id).toBe(101);
    await page.evaluate(() => {
      sessionStorage.setItem("offline-evidence-capacity-restored", "true");
      (window as unknown as { offlineEvidenceQuotaMode: string }).offlineEvidenceQuotaMode = "full";
    });
    await page.getByRole("button", { name: "Retry save", exact: true }).click();
  }
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", card.start_fen);
  const recorded = await readPhone();
  expect(recorded.attempts).toHaveLength(1);
  expect(recorded.attempts[0]).toMatchObject({ attemptId, localEntryId: 101, outcome: "correct", expectedRevision: 3,
    openingEvidenceFallbackReason: "local_storage_quota" });
  expect(recorded.attempts[0].openingEvidenceCompletion).toBeUndefined();
  expect(recorded.cards[0].parent_local_entry_id).toBe(101);
  await expect.poll(async () => (await savedAttempts(page)).find(attempt => attempt.attempt_id === attemptId)?.delivery_state).toBe("retained");
  const retainedEvents = (await savedEvents(page) as { attempt_id: string }[]).filter(event => event.attempt_id === attemptId);
  expect(retainedEvents.length).toBeGreaterThanOrEqual(3);
  const reviews: Record<string, unknown>[] = [], keys: string[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON()); keys.push(route.request().headers()["idempotency-key"]);
    await route.fulfill({ json: { persisted: true, review_id: 801, requeue_entry_id: 102 } });
  });
  await page.unroute("**/api/**"); await page.reload();
  await expect.poll(() => reviews.length).toBe(1);
  expect(keys).toEqual([`phone-reconcile:${attemptId}:aggregate-only`]);
  expect(reviews[0]).not.toHaveProperty("opening_evidence_completion");
  expect(reviews[0]).toMatchObject({ attempt_id: attemptId, outcome: "correct", recorded_at: recorded.attempts[0].completedAt });
  await page.reload();
  await expect(page.getByText("Shadow opening", { exact: true })).toBeVisible();
  expect(reviews).toHaveLength(1);
  expect((await savedAttempts(page)).find(attempt => attempt.attempt_id === attemptId)?.delivery_state).toBe("retained");
  expect((await savedEvents(page) as { attempt_id: string }[]).filter(event => event.attempt_id === attemptId)).toEqual(retainedEvents);
});
}
async function square(page: Page, square: string) {
  const bounds = (await page.locator(".cg-wrap").boundingBox())!;
  await page.mouse.click(bounds.x + (square.charCodeAt(0) - 97 + 0.5) * bounds.width / 8,
    bounds.y + (8 - Number(square[1]) + 0.5) * bounds.height / 8);
}
async function move(page: Page, from: string, to: string) {
  await expect(page.getByText("Shadow opening", { exact: true })).toBeVisible();
  await square(page, from); await square(page, to); }
async function savedEvents(page: Page) {
  return page.evaluate(() => new Promise<unknown[]>((resolve, reject) => {
    const request = indexedDB.open("tempo-offline-training", 2);
    request.onsuccess = () => {
      const database = request.result;
      const read = database.transaction("opening_events").objectStore("opening_events").getAll();
      read.onsuccess = () => { database.close(); resolve(read.result); };
      read.onerror = () => { database.close(); reject(read.error); };
    };
    request.onerror = () => reject(request.error);
  }));
}

async function savedAttempts(page: Page) {
  return page.evaluate(() => new Promise<Record<string, unknown>[]>((resolve, reject) => {
    const request = indexedDB.open("tempo-offline-training", 2);
    request.onsuccess = () => {
      const database = request.result;
      const read = database.transaction("opening_attempts").objectStore("opening_attempts").getAll();
      read.onsuccess = () => { database.close(); resolve(read.result); };
      read.onerror = () => { database.close(); reject(read.error); };
    };
    request.onerror = () => reject(request.error);
  }));
}

test("AS-15 recovered evidence waits for foreground queue readiness and an idle opportunity", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  await page.goto("/");
  const initialCheckpointFailed = page.waitForEvent("requestfailed", {
    predicate: request => request.url().endsWith("/api/opening-evidence/checkpoints"),
  });
  await move(page, "e2", "e4");
  await expect.poll(async () => (await savedEvents(page)).length).toBeGreaterThan(0);
  // Commit precedes automatic delivery. Settle that delivery before replacing its route.
  await initialCheckpointFailed;
  const originalAttempt = (await savedAttempts(page))[0];
  const original = originalAttempt.attempt_id;
  const frozenDelivery = originalAttempt.delivery as { checkpoint: OpeningEvidenceCheckpoint; operationKey: string };
  await page.addInitScript(() => {
    const callbacks = new Map<number, IdleRequestCallback>(); let sequence = 0;
    window.requestIdleCallback = callback => { callbacks.set(++sequence, callback); return sequence; };
    window.cancelIdleCallback = id => { callbacks.delete(id); };
    Object.assign(window, { evidenceIdleCallbacks: callbacks });
  });
  let releaseQueue!: () => void;
  let queueRequested!: () => void;
  const queueStarted = new Promise<void>(resolve => { queueRequested = resolve; });
  await page.route("**/api/queue/window?**", async route => {
    queueRequested(); await new Promise<void>(resolve => { releaseQueue = resolve; });
    await route.fulfill({ json: { local_date: localDate, count: 1, cards: [card], projection } });
  });
  await page.unroute("**/api/opening-evidence/checkpoints");
  const recovered: OpeningEvidenceCheckpoint[] = [];
  const recoveryKeys: string[] = [];
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    recovered.push(checkpoint);
    recoveryKeys.push(route.request().headers()["idempotency-key"]);
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.terminal?.final_sequence ?? 1 } });
  });
  await page.reload({ waitUntil: "domcontentloaded" }); await queueStarted;
  expect((await savedAttempts(page)).some(attempt => attempt.attempt_id === original)).toBe(true);
  expect(recovered).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { evidenceIdleCallbacks: Map<number, unknown> }).evidenceIdleCallbacks.size)).toBe(0);
  releaseQueue();
  await expect(page.getByText("Shadow opening", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as unknown as { evidenceIdleCallbacks: Map<number, unknown> }).evidenceIdleCallbacks.size)).toBe(1);
  await page.evaluate(() => {
    const callbacks = (window as unknown as { evidenceIdleCallbacks: Map<number, IdleRequestCallback> }).evidenceIdleCallbacks;
    for (const callback of callbacks.values()) callback({ didTimeout: false, timeRemaining: () => 50 });
    callbacks.clear();
  });
  // The ambiguous original envelope is retried unchanged in this slice.
  await expect.poll(() => recovered.length).toBe(1);
  expect(recovered[0]).toEqual(frozenDelivery.checkpoint);
  expect(recoveryKeys[0]).toBe(frozenDelivery.operationKey);
  await expect.poll(async () => (await savedAttempts(page)).find(attempt => attempt.attempt_id === original)?.delivery_state).toBe("pending");
  await expect.poll(() => page.evaluate(() => (window as unknown as { evidenceIdleCallbacks: Map<number, unknown> }).evidenceIdleCallbacks.size)).toBe(1);
  // Its newly sealed partial terminal needs a separate idle opportunity.
  await page.evaluate(() => {
    const callbacks = (window as unknown as { evidenceIdleCallbacks: Map<number, IdleRequestCallback> }).evidenceIdleCallbacks;
    for (const callback of callbacks.values()) callback({ didTimeout: false, timeRemaining: () => 50 });
    callbacks.clear();
  });
  await expect.poll(() => recovered.some(checkpoint => checkpoint.attempt_id === original && checkpoint.terminal?.state === "partial")).toBe(true);
  expect(recovered).toHaveLength(2);
});

test("AS-15 a real tab lease releases stranded evidence into a later idle slice", async ({ page: owner, context }) => {
  await prepareVisualUI(owner); await prepareQueue(owner);
  // This tab owns active board work; only the second tab runs recovery in this fixture.
  await owner.addInitScript(() => { window.requestIdleCallback = () => 1; window.cancelIdleCallback = () => undefined; });
  await owner.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  await owner.goto("/");
  const failed = owner.waitForEvent("requestfailed", { predicate: request => request.url().endsWith("/api/opening-evidence/checkpoints") });
  await move(owner, "e2", "e4"); await failed;
  const original = (await savedAttempts(owner))[0];
  const frozen = original.delivery as { checkpoint: OpeningEvidenceCheckpoint; operationKey: string };
  const recovering = await context.newPage();
  await prepareVisualUI(recovering); await prepareQueue(recovering);
  await recovering.addInitScript(() => {
    const callbacks = new Map<number, IdleRequestCallback>(); let sequence = 0;
    window.requestIdleCallback = callback => { callbacks.set(++sequence, callback); return sequence; };
    window.cancelIdleCallback = id => { callbacks.delete(id); };
    Object.assign(window, { evidenceIdleCallbacks: callbacks });
  });
  const checkpoints: OpeningEvidenceCheckpoint[] = [], keys: string[] = [];
  await recovering.route("**/api/opening-evidence/checkpoints", async route => {
    expect(route.request().headers()["x-tempo-work-class"]).toBe("background");
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint; checkpoints.push(checkpoint);
    keys.push(route.request().headers()["idempotency-key"]);
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.terminal?.final_sequence ?? 1 } });
  });
  // Seed later orphan journals with the same observed presentation; the original remains owned by the live tab.
  await owner.evaluate(() => new Promise<void>((resolve, reject) => {
    const request = indexedDB.open("tempo-offline-training", 2);
    request.onsuccess = () => {
      const database = request.result, transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
      const attempts = transaction.objectStore("opening_attempts"), events = transaction.objectStore("opening_events");
      const all = attempts.getAll(); all.onsuccess = () => {
        const attempt = all.result[0]; const observed = events.index("attempt_id").getAll(attempt.attempt_id);
        observed.onsuccess = () => { for (const id of ["zz-lease-B", "zz-lease-C"]) {
          attempts.put({ ...attempt, attempt_id: id, owner_session_id: "closed-tab", delivery: undefined, delivery_state: "pending",
            terminal: { state: "partial", final_sequence: attempt.final_sequence, ended_at: new Date().toISOString() } });
          for (const event of observed.result) events.put({ ...event, attempt_id: id });
        } };
      };
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onerror = () => { database.close(); reject(transaction.error); };
    };
  }));
  await recovering.goto("/"); await expect(recovering.getByText("Shadow opening", { exact: true })).toBeVisible();
  const idleCount = () => recovering.evaluate(() => (window as unknown as { evidenceIdleCallbacks: Map<number, unknown> }).evidenceIdleCallbacks.size);
  const idle = async () => {
    await expect.poll(idleCount).toBe(1);
    await recovering.evaluate(() => {
      const callbacks = (window as unknown as { evidenceIdleCallbacks: Map<number, IdleRequestCallback> }).evidenceIdleCallbacks;
      const [id, callback] = [...callbacks][0]; callbacks.delete(id); callback({ didTimeout: false, timeRemaining: () => 50 });
    });
  };
  await idle(); await expect.poll(idleCount).toBe(1); expect(checkpoints).toEqual([]);
  await idle(); await expect.poll(() => checkpoints.map(checkpoint => checkpoint.attempt_id)).toEqual(["zz-lease-B"]);
  await idle(); await expect.poll(() => checkpoints.map(checkpoint => checkpoint.attempt_id)).toEqual(["zz-lease-B", "zz-lease-C"]);
  await expect.poll(idleCount).toBe(0); expect((await savedAttempts(recovering)).find(attempt => attempt.attempt_id === original.attempt_id)).toEqual(original);
  await owner.close(); // Real browser lock release, without reconnect/reload or direct recovery invocation.
  await idle(); await expect.poll(() => checkpoints.length).toBe(3);
  expect(checkpoints[2]).toEqual(frozen.checkpoint); expect(keys[2]).toBe(frozen.operationKey);
  await idle(); await expect.poll(() => checkpoints.length).toBe(4);
  expect(checkpoints[3]).toMatchObject({ attempt_id: original.attempt_id, terminal: { state: "partial" } });
  await expect.poll(async () => (await savedAttempts(recovering)).some(attempt => attempt.attempt_id === original.attempt_id)).toBe(false);
  await expect.poll(idleCount).toBe(0);
});

test("AS-16 restarted opening board records guided arrows and retains the prior partial attempt", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  await page.route("**/api/queue/entries/101/fail", route => route.fulfill({ json: { persisted: true } }));
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(async () => (await savedEvents(page)).length).toBeGreaterThan(0);
  const original = (await savedAttempts(page))[0].attempt_id;
  await page.getByRole("button", { name: /Restart/ }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", card.start_fen);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-hint", "true");
  await expect.poll(async () => (await savedAttempts(page)).find(attempt => attempt.attempt_id === original)?.terminal)
    .toMatchObject({ state: "partial" });
  await expect.poll(async () => (await savedEvents(page) as { attempt_id: string; assistance?: string }[])
    .filter(event => event.attempt_id !== original).map(event => event.assistance)).toContain("guided");
  expect((await savedEvents(page) as { attempt_id: string; assistance?: string }[])
    .some(event => event.attempt_id !== original && event.assistance === "revealed")).toBe(false);
});

test("AS-16 local review quota saves the aggregate and retains evidence through a late checkpoint receipt", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.addInitScript(() => {
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "tempo-pending-training-reviews-v1" && value.includes("openingEvidenceCompletion"))
        throw new DOMException("Evidence review exceeds quota", "QuotaExceededError");
      return setItem.call(this, key, value);
    };
  });
  let releaseCheckpoint!: () => void;
  let checkpointFinished = false;
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    await new Promise<void>(resolve => { releaseCheckpoint = resolve; });
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.at(-1)?.sequence ?? 0 } });
    checkpointFinished = true;
  });
  const reviews: { attempt_id: string; opening_evidence_completion?: unknown }[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    const review = route.request().postDataJSON();
    expect(review.opening_evidence_completion).toBeUndefined();
    expect(route.request().headers()["idempotency-key"]).toBe(`review-attempt:${review.attempt_id}:aggregate-only`);
    const durable = await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]"));
    expect(durable[0]).toMatchObject({ attemptId: review.attempt_id, evidenceFallbackReason: "local_storage_quota" });
    reviews.push(review); await route.fulfill({ json: { persisted: true } });
  });
  const nextCard = { ...card, id: "shadow-next", queue_entry_id: 102, repertoire_name: "Next opening", opening_decision_manifest: undefined };
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    local_date: localDate, count: reviews.length ? 1 : 2, cards: reviews.length ? [nextCard] : [card, nextCard], projection } }));
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(() => Boolean(releaseCheckpoint)).toBe(true);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "b5");
  await expect.poll(() => reviews.length).toBe(1);
  await expect(page.getByText("Next opening", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]").length)).toBe(0);
  await expect.poll(async () => (await savedAttempts(page)).find(attempt => attempt.attempt_id === reviews[0].attempt_id))
    .toMatchObject({ delivery_state: "retained", retention_reason: "local_storage_quota", terminal: { state: "complete" } });
  releaseCheckpoint(); await expect.poll(() => checkpointFinished).toBe(true);
  await expect.poll(async () => (await savedEvents(page) as { attempt_id: string }[])
    .filter(event => event.attempt_id === reviews[0].attempt_id).length).toBe(3);
  await page.reload();
  expect((await savedAttempts(page)).find(attempt => attempt.attempt_id === reviews[0].attempt_id)?.delivery_state).toBe("retained");
  expect((await savedEvents(page) as { attempt_id: string }[]).filter(event => event.attempt_id === reviews[0].attempt_id)).toHaveLength(3);
});

test("AS-08 deferred evidence persistence leaves rendered moves and aggregate review responsive", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  let releaseCheckpoint: (() => void) | undefined;
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    await new Promise<void>(resolve => { releaseCheckpoint = resolve; });
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.length } });
  });
  const reviews: { attempt_id: string; opening_evidence_completion: OpeningEvidenceCheckpoint }[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON()); await route.fulfill({ json: { persisted: true } });
  });
  await page.goto("/");
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await expect(page.locator("cg-board piece.white.pawn")).toHaveCount(8);
  expect(await page.locator("cg-board piece.white.pawn").evaluateAll(pawns => {
    const board = document.querySelector("cg-board")!.getBoundingClientRect();
    return pawns.some(pawn => {
      const piece = pawn.getBoundingClientRect();
      return Math.abs(piece.x - board.x - board.width * 4 / 8) < 2 &&
        Math.abs(piece.y - board.y - board.height * 4 / 8) < 2;
    });
  })).toBe(true);
  await expect.poll(() => Boolean(releaseCheckpoint)).toBe(true);
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "b5");
  await expect.poll(() => reviews.length).toBe(1);
  const completion = reviews[0].opening_evidence_completion;
  expect(completion.attempt_id).toBe(reviews[0].attempt_id);
  expect(completion.study_timezone).toBe("America/New_York");
  expect(completion.events.map(event => event.response_uci)).toEqual(["e2e4", "g1f3", "f1b5"]);
  expect(completion.terminal).toMatchObject({ state: "complete", final_sequence: 3 });
  releaseCheckpoint?.();
});

test("AS-15 ambiguous checkpoint retries frozen events and delivery key before newer work", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  const sends: { key: string; body: OpeningEvidenceCheckpoint }[] = [];
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    sends.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    if (sends.length === 1) { await route.abort("failed"); return; }
    const checkpoint = sends.at(-1)!.body;
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.at(-1)?.sequence } });
  });
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(() => sends.length).toBe(1);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await move(page, "g1", "f3");
  await expect.poll(() => sends.length).toBe(3);
  expect(sends[1]).toEqual(sends[0]);
  expect(sends[2].body.attempt_id).toBe(sends[0].body.attempt_id);
  expect(sends[2].body.events.map(event => event.sequence)).toEqual([2]);
});

test("AS-09 actual teaching arrows manual Again and revealed correction retain first response context", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: {
    local_date: localDate, count: 1, cards: [{ ...card, has_study_review: false }], projection } }));
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  const reviews: { opening_evidence_completion: OpeningEvidenceCheckpoint }[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON()); await route.fulfill({ json: { persisted: true } });
  });
  await page.route("**/api/queue/entries/101/fail", route => route.fulfill({ json: { persisted: true } }));
  await page.goto("/");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-hint", "true");
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await page.getByRole("button", { name: "Show move" }).click();
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "c4");
  await expect(page.getByText(/Again recorded|saving guided/).first()).toBeVisible();
  await move(page, "f1", "b5");
  await expect.poll(() => reviews.length).toBe(1);
  const events = reviews[0].opening_evidence_completion.events;
  expect(events.find(event => event.decision_index === 0)).toMatchObject({ kind: "assistance", assistance: "teaching" });
  expect(events.find(event => event.decision_index === 1 && event.kind === "manual_failure")?.response_uci).toBeNull();
  expect(events.find(event => event.decision_index === 2 && event.kind === "first_response")).toMatchObject({ response_uci: "f1c4", disposition: "wrong" });
  expect(events.some(event => event.decision_index === 2 && event.kind === "reveal")).toBe(true);
  expect(events.some(event => event.decision_index === 2 && event.kind === "correction")).toBe(true);
});

test("AS-15 crash recovery seals committed work as partial and starts a new board identity", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(async () => (await savedEvents(page)).length).toBe(1);
  const events = await savedEvents(page) as { attempt_id: string; observed_at: string }[];
  await page.unroute("**/api/opening-evidence/checkpoints");
  const recovered: OpeningEvidenceCheckpoint[] = [];
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    recovered.push(checkpoint);
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: 1 } });
  });
  await page.reload();
  await expect.poll(() => recovered.some(checkpoint => checkpoint.terminal?.state === "partial")).toBe(true);
  expect(recovered[0].events[0].observed_at).toBe(events[0].observed_at);
  const partial = recovered.find(checkpoint => checkpoint.terminal)!;
  expect(partial.attempt_id).toBe(events[0].attempt_id);
  expect(partial.terminal?.final_sequence).toBe(1);
  await move(page, "e2", "e4");
  await expect.poll(() => recovered.some(checkpoint => checkpoint.attempt_id !== partial.attempt_id)).toBe(true);
});

test("AS-15 orphaned completion without an aggregate outbox retains partial work instead of inventing a review", async ({ page }) => {
  await prepareVisualUI(page); await prepareQueue(page);
  await page.route("**/api/opening-evidence/checkpoints", route => route.abort("failed"));
  const verificationWorkClasses: (string | undefined)[] = [];
  await page.route("**/api/opening-evidence/attempts/*", route => {
    verificationWorkClasses.push(route.request().headers()["x-tempo-work-class"]);
    return route.fulfill({ status: 404, json: { detail: "Not persisted" } });
  });
  await page.goto("/"); await move(page, "e2", "e4");
  await expect.poll(async () => (await savedEvents(page)).length).toBe(1);
  const events = await savedEvents(page) as { attempt_id: string; observed_at: string }[];
  await page.evaluate(({ attemptId, observedAt }) => new Promise<void>((resolve, reject) => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onsuccess = () => {
      const database = open.result;
      const transaction = database.transaction("opening_attempts", "readwrite");
      const store = transaction.objectStore("opening_attempts");
      const read = store.get(attemptId);
      read.onsuccess = () => store.put({ ...read.result,
        terminal: { state: "complete", final_sequence: 1, ended_at: observedAt } });
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onerror = () => { database.close(); reject(transaction.error); };
    };
  }), { attemptId: events[0].attempt_id, observedAt: events[0].observed_at });
  const recovered: OpeningEvidenceCheckpoint[] = [];
  await page.unroute("**/api/opening-evidence/checkpoints");
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    recovered.push(checkpoint);
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: 1 } });
  });
  let aggregateReviews = 0;
  await page.route("**/api/cards/shadow-card/review", route => { aggregateReviews += 1; return route.abort("failed"); });
  await page.reload();
  await expect.poll(() => recovered.some(checkpoint => checkpoint.terminal?.state === "partial")).toBe(true);
  expect(recovered.find(checkpoint => checkpoint.terminal)?.terminal?.ended_at).toBe(events[0].observed_at);
  expect(aggregateReviews).toBe(0);
  expect(verificationWorkClasses).toEqual(["background"]);
});

for (const rejectParentEvidence of [false, true]) {
test(rejectParentEvidence
  ? "AS-16 offline parent fallback retains rejected journal after queue refresh and reconciles repeat evidence"
  : "AS-16 offline repeats retain independent attempts and reconcile their parent before completion", async ({ page, context }) => {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", { value: true, configurable: true });
    Object.defineProperty(navigator, "userAgent", { value: "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15", configurable: true });
  });
  await prepareQueue(page);
  // A first study pass has a legitimate reinforcement repeat in the existing phone queue.
  const firstStudy = { ...card, first_correct_at: undefined };
  await page.route("**/api/queue/prepared?**", route => route.fulfill({ json: {
    local_date: localDate, count: 1, cards: [firstStudy], projection, prepared_at: new Date().toISOString() } }));
  await page.goto("/");
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect.poll(() => page.evaluate(() => new Promise<boolean>(resolve => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onsuccess = () => {
      const database = open.result;
      const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      read.onsuccess = () => { database.close(); resolve(Boolean(read.result)); };
    };
  }))).toBe(true);
  await page.route("**/api/**", route => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("Shadow opening", { exact: true })).toBeVisible();
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4P3.* w /);
  await move(page, "g1", "f3");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /5N2.* w /);
  await move(page, "f1", "b5");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", card.start_fen);
  // Grade the genuine repeat before reconnecting; an unencountered decision gets no invented observation.
  await page.getByRole("button", { name: "Correct", exact: true }).click();
  await expect(page.locator(".session-count strong")).toHaveText("0");
  const saved = await page.evaluate(() => new Promise<{ attempts: { attemptId: string; completedAt: string; openingEvidenceCompletion: OpeningEvidenceCheckpoint }[] }>(resolve => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onsuccess = () => {
      const database = open.result;
      const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      read.onsuccess = () => { database.close(); resolve(read.result); };
    };
  }));
  expect(saved.attempts).toHaveLength(2);
  expect(saved.attempts[0].attemptId).not.toBe(saved.attempts[1].attemptId);
  expect(saved.attempts[1].openingEvidenceCompletion.parent_attempt_id).toBe(saved.attempts[0].attemptId);
  expect(saved.attempts[0].openingEvidenceCompletion.events.filter(event => event.kind === "first_response")).toHaveLength(3);
  expect(saved.attempts[1].openingEvidenceCompletion.events).toEqual([]);
  const reviews: { attempt_id: string; queue_entry_id: number; recorded_at: string; opening_evidence_completion?: OpeningEvidenceCheckpoint }[] = [];
  const deliveryKeys: string[] = [];
  await page.route("**/api/cards/shadow-card/review", async route => {
    reviews.push(route.request().postDataJSON());
    deliveryKeys.push(route.request().headers()["idempotency-key"]);
    if (rejectParentEvidence && reviews.length === 1) {
      await route.fulfill({ status: 409, json: { detail: { code: "opening_evidence_conflict",
        message: "Parent evidence was rejected", aggregate_review_allowed: true } } });
      return;
    }
    const savedReviewCount = reviews.length - Number(rejectParentEvidence);
    await route.fulfill({ json: { persisted: true, review_id: 800 + savedReviewCount,
      requeue_entry_id: savedReviewCount === 1 ? 102 : null } });
  });
  await page.route("**/api/opening-evidence/checkpoints", async route => {
    const checkpoint = route.request().postDataJSON() as OpeningEvidenceCheckpoint;
    await route.fulfill({ json: { persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: checkpoint.events.map(event => event.sequence), contiguous_sequence: checkpoint.events.at(-1)?.sequence ?? 0 } });
  });
  await page.unroute("**/api/**"); await page.reload();
  await expect.poll(() => reviews.length).toBe(rejectParentEvidence ? 3 : 2);
  const acceptedReviews = rejectParentEvidence ? reviews.slice(1) : reviews;
  expect(acceptedReviews.map(review => review.queue_entry_id)).toEqual([101, 102]);
  expect(acceptedReviews.map(review => review.attempt_id)).toEqual(saved.attempts.map(attempt => attempt.attemptId));
  expect(acceptedReviews[1].opening_evidence_completion?.queue_entry_id).toBe(102);
  expect(reviews[0].recorded_at).toBe(saved.attempts[0].completedAt);
  expect(reviews[0].opening_evidence_completion).toEqual(saved.attempts[0].openingEvidenceCompletion);
  if (rejectParentEvidence) {
    expect(acceptedReviews[0].opening_evidence_completion).toBeUndefined();
    expect(deliveryKeys[1]).toBe(`${deliveryKeys[0]}:aggregate-only`);
    await expect.poll(() => page.evaluate(() => new Promise<boolean>(resolve => {
      const open = indexedDB.open("tempo-offline-training", 2);
      open.onsuccess = () => {
        const database = open.result;
        const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
        read.onsuccess = () => {
          database.close();
          resolve(Boolean(read.result.attempts.find((attempt: { openingEvidenceRejected?: string; serverAcknowledged?: boolean }) =>
            attempt.serverAcknowledged && attempt.openingEvidenceRejected === "Parent evidence was rejected")));
        };
      };
    }))).toBe(true);
    await page.reload();
    await expect.poll(() => page.evaluate(() => new Promise<boolean>(resolve => {
      const open = indexedDB.open("tempo-offline-training", 2);
      open.onsuccess = () => {
        const database = open.result;
        const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
        read.onsuccess = () => {
          database.close();
          resolve(Boolean(read.result.attempts.find((attempt: { openingEvidenceRejected?: string }) =>
            attempt.openingEvidenceRejected === "Parent evidence was rejected")));
        };
      };
    }))).toBe(true);
  }
});
}
