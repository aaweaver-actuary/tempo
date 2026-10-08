import { expect, type BrowserContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { openingDecisionManifestSchema, type OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";
import { prepareVisualUI } from "./visual-fixtures";

const completionSignalKey = "tempo-opening-evidence-completion-v1";
type Observation = { callbacks: Map<number, IdleRequestCallback>; signals: number; readsAfterSignal: number };

/** Native same-origin transports and IDB; only receipt/network outcomes and idle opportunities are controlled. */
export async function crossTabOpeningCompletion(owner: Page, context: BrowserContext, fallback: boolean) {
  const manifest = openingDecisionManifestSchema.parse(JSON.parse(readFileSync(new URL("../fixtures/opening-evidence-manifest.json", import.meta.url), "utf8")));
  const attemptId = `cross-tab-${fallback ? "broadcast" : "storage"}`;
  const operationKey = `opening-checkpoint:${attemptId}`;
  const checkpoint: OpeningEvidenceCheckpoint = { attempt_id: attemptId, manifest, origin_queue_entry_id: 101,
    queue_entry_id: 101, parent_attempt_id: null, source: "offline", study_timezone: "UTC", started_at: new Date().toISOString(),
    terminal: { state: "partial", final_sequence: 3, ended_at: new Date().toISOString() },
    events: manifest.decisions.map((decision, index) => ({ sequence: index + 1, decision_index: index,
      decision_id: decision.decision_id, expected_uci: decision.expected_uci, response_uci: decision.expected_uci,
      kind: "first_response", disposition: "expected", assistance: null, observed_at: new Date().toISOString() })) };
  const operationReads: string[] = [], completedReads: string[] = [], checkpointPosts: unknown[] = [], reviews: unknown[] = [];
  // One previously accepted frozen checkpoint receipt. Recovery must read it, never create another.
  const acceptedReceipts = new Map([[operationKey, { persisted: true, attempt_id: attemptId,
    received_sequences: [1, 2, 3], contiguous_sequence: 3 }]]);
  const setup = async (page: Page, completing: boolean) => {
    await prepareVisualUI(page, false);
    page.on("response", response => {
      if (response.url().includes("/api/operations/opening-checkpoint") && response.status() === 200) completedReads.push(completing ? "B" : "A");
    });
    await page.addInitScript(({ signalKey, trackedAttempt, failSignalWrite }) => {
      const observation = { callbacks: new Map<number, IdleRequestCallback>(), signals: 0, readsAfterSignal: 0 };
      let sequence = 0;
      window.requestIdleCallback = callback => { observation.callbacks.set(++sequence, callback); return sequence; };
      window.cancelIdleCallback = id => { observation.callbacks.delete(id); };
      const observe = (value: unknown) => {
        if (value && typeof value === "object" && "attemptId" in value && value.attemptId === trackedAttempt) observation.signals++;
      };
      window.addEventListener("storage", event => {
        if (event.key === signalKey && event.newValue) observe(JSON.parse(event.newValue));
      });
      const channel = new BroadcastChannel(signalKey); channel.onmessage = event => observe(event.data);
      window.addEventListener("pagehide", () => channel.close(), { once: true });
      const get = IDBObjectStore.prototype.get;
      IDBObjectStore.prototype.get = function (key) {
        if (this.name === "opening_attempts" && key === trackedAttempt && observation.signals) observation.readsAfterSignal++;
        return get.call(this, key);
      };
      if (failSignalWrite) {
        const setItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
          if (key === signalKey) throw new DOMException("Signal storage is full", "QuotaExceededError");
          return setItem.call(this, key, value);
        };
      }
      Object.assign(window, { crossTabEvidence: observation });
    }, { signalKey: completionSignalKey, trackedAttempt: attemptId, failSignalWrite: completing && fallback });
    await page.route("**/api/operations/opening-checkpoint**", async route => {
      operationReads.push(completing ? "B" : "A");
      await route.fulfill({ json: completing ? { state: "complete", response: acceptedReceipts.get(operationKey) }
        : { state: "blocked", last_error: { message: "Repair the local worker" } } });
    });
    await page.route("**/api/opening-evidence/checkpoints", async route => {
      checkpointPosts.push(route.request().postDataJSON()); await route.abort("failed");
    });
    await page.route("**/api/cards/*/review", async route => {
      reviews.push(route.request().postDataJSON()); await route.abort("failed");
    });
    await page.goto("/");
    await expect(page.locator(".board-frame")).toBeVisible();
  };
  const idleCount = (page: Page) => page.evaluate(() => (window as unknown as { crossTabEvidence: Observation }).crossTabEvidence.callbacks.size);
  const idle = async (page: Page) => {
    await expect.poll(() => idleCount(page)).toBe(1);
    await page.evaluate(() => {
      const callbacks = (window as unknown as { crossTabEvidence: Observation }).crossTabEvidence.callbacks;
      const [id, callback] = [...callbacks][0]; callbacks.delete(id); callback({ didTimeout: false, timeRemaining: () => 50 });
    });
  };
  await setup(owner, false);
  await expect.poll(() => idleCount(owner)).toBe(1);
  await owner.evaluate(completion => new Promise<void>((resolve, reject) => {
    const request = indexedDB.open("tempo-offline-training", 2);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const database = request.result;
      const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
      const { events, ...header } = completion;
      transaction.objectStore("opening_attempts").put({ ...header, owner_session_id: "closed-tab", final_sequence: events.length,
        delivery_state: "pending", delivery: { checkpoint: completion, operationKey: `opening-checkpoint:${completion.attempt_id}` } });
      for (const event of events) transaction.objectStore("opening_events").put({ ...event, attempt_id: completion.attempt_id });
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onabort = () => { database.close(); reject(transaction.error); };
    };
  }), checkpoint);
  await idle(owner);
  await owner.getByRole("button", { name: "Notifications", exact: true }).click();
  const warning = owner.locator(".notification-tray").getByText(/Opening evidence recovery is pending/);
  await expect(warning).toBeVisible(); await expect.poll(() => idleCount(owner)).toBe(0);
  const completing = await context.newPage(); await setup(completing, true);
  await idle(completing);
  // B's production receipt-confirmation transaction deletes the journal and publishes the completion.
  await expect.poll(() => owner.evaluate(() => (window as unknown as { crossTabEvidence: Observation }).crossTabEvidence.signals)).toBeGreaterThan(0);
  await expect.poll(() => idleCount(owner)).toBe(1);
  await completing.close(); // Native signal survives the sender's closure; no lease/connectivity/reload wakeup.
  await idle(owner);
  await expect(warning).toHaveCount(0);
  await expect.poll(() => owner.evaluate(() => (window as unknown as { crossTabEvidence: Observation }).crossTabEvidence.readsAfterSignal)).toBeGreaterThan(0);
  await expect.poll(() => idleCount(owner)).toBe(0);
  // Replay duplicate notifications through the real transport, without invoking any recovery function.
  const announcing = await context.newPage();
  await announcing.route("**/*", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Completion replay</title>" }));
  await announcing.goto("/");
  await announcing.evaluate(({ key, attemptId, fallback }) => {
    const signal = { attemptId, signalId: crypto.randomUUID(), sourceSessionId: "duplicate-completion" };
    if (fallback) { const channel = new BroadcastChannel(key); channel.postMessage(signal); channel.postMessage(signal); channel.close(); }
    else { localStorage.setItem(key, JSON.stringify(signal)); localStorage.setItem(key, JSON.stringify({ ...signal, signalId: crypto.randomUUID() })); }
  }, { key: completionSignalKey, attemptId, fallback });
  await expect.poll(() => idleCount(owner)).toBe(1); await idle(owner); await expect.poll(() => idleCount(owner)).toBe(0);
  await announcing.close();
  expect(completedReads).toEqual(["A", "B"]);
  expect(operationReads.filter(tab => tab === "A")).toEqual(["A"]);
  // Foreground preemption may abort a receipt GET; retrying that read creates no new durable operation.
  expect(operationReads.filter(tab => tab === "B").length).toBeGreaterThanOrEqual(1);
  expect(checkpointPosts).toEqual([]); expect(reviews).toEqual([]);
  expect(acceptedReceipts.size).toBe(1);
}
