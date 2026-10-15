import { build } from "esbuild";
import type { Page } from "@playwright/test";
import type { IntentStore, IntentEnvelope } from "@tempo/browser-intents";
import { test, expect, prepareControlledUI, openingCard } from "./fixtures";
import { move } from "../browser/native-board";
import { nativeStudyRecords } from "../browser/native-study-storage";
import { validateReview } from "./contracts";

type NativeWindow = Window & { intentFixture: typeof import("@tempo/browser-intents"); nativeIntents: IntentStore };
type RecoveryTraceWindow = NativeWindow & { recoveryOwnership: {
  events: Array<{ event: string; signal: number }>; restore: () => void;
} };
const bundle = build({ stdin: { contents: 'export * from "@tempo/browser-intents";', resolveDir: process.cwd() }, bundle: true,
  write: false, format: "iife", globalName: "intentFixture", platform: "browser" }).then(result => result.outputFiles[0].text);
async function nativeStore(page: Page, workspace = "native-regression") {
  await page.addScriptTag({ content: await bundle });
  await page.evaluate(async workspace => { const native = window as unknown as NativeWindow; native.nativeIntents = await native.intentFixture.openIntentStore({ workspace }); }, workspace);
}
const command = (key: string): IntentEnvelope => ({ key, kind: "review", attemptId: key, requestId: `request:${key}`,
  endpoint: "/review", createdAt: "2026-09-18T16:00:00Z", revision: 1, payload: { outcome: "again", guided: true } });

test("issue221 native two-tab writes preserve both orders and competing claims", async ({ page, context }) => {
  await prepareControlledUI(page, []); await nativeStore(page);
  const second = await context.newPage(); await prepareControlledUI(second, []); await nativeStore(second);
  for (const order of [[0, 1], [1, 0]]) {
    const pages = [page, second]; const prefix = order.join("");
    // Both documents reach the barrier before either starts its transaction.
    await Promise.all(pages.map(current => current.evaluate(() => Promise.resolve("ready"))));
    for (const index of order) await pages[index].evaluate(async envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), command(`${prefix}:${index}`));
    // Independent transactions also start concurrently after the same barrier.
    await Promise.all(pages.map((current, index) => current.evaluate(async envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), command(`${prefix}:concurrent:${index}`))));
    const retained = await second.evaluate(async () => (await (window as unknown as NativeWindow).nativeIntents.list()).map(record => record.key));
    expect(retained).toEqual(expect.arrayContaining([`${prefix}:0`, `${prefix}:1`, `${prefix}:concurrent:0`, `${prefix}:concurrent:1`]));
    expect(retained.filter(key => key === `${prefix}:0` || key === `${prefix}:1`)).toEqual(order.map(index => `${prefix}:${index}`));
  }
  const claims = await Promise.all([page, second].map((current, index) => current.evaluate(async owner =>
    (window as unknown as NativeWindow).nativeIntents.claim("01:0", owner, Date.now(), 30_000), String(index))));
  expect(claims.filter(Boolean)).toHaveLength(1);
  await second.close();
});

test("issue221 native intent survives true reload with original payload and settlement", async ({ page }) => {
  await prepareControlledUI(page, []); await nativeStore(page);
  const original = command("reload"); await page.evaluate(envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), original);
  await page.reload(); await nativeStore(page);
  expect(await page.evaluate(() => (window as unknown as NativeWindow).nativeIntents.get("reload"))).toMatchObject(original);
  await page.evaluate(async () => { const store = (window as unknown as NativeWindow).nativeIntents;
    await store.update("reload", current => ({ ...current, state: "committed", receipt: { persisted: true } })); });
  await page.reload(); await nativeStore(page);
  await page.evaluate(envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), original);
  expect(await page.evaluate(() => (window as unknown as NativeWindow).nativeIntents.get("reload"))).toMatchObject({ state: "committed", receipt: { persisted: true } });
});

test("issue221 legacy migration requires closed-tab acknowledgement and retains sources", async ({ page, context }) => {
  const api = await prepareControlledUI(page, [], false, false);
  api.register({ method: "GET", path: "/api/operations/review-attempt%3Alegacy-native", query: {} }, route => route.fulfill({ json: { state: "queued" } }));
  await page.addInitScript(() => {
    if (!localStorage.getItem("legacy-native-seeded")) {
      localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([{ backendId: "legacy-card", queueEntryId: 701,
        attemptId: "legacy-native", outcome: "again", guided: true, completedAt: "2026-09-18T15:00:00Z" }]));
      localStorage.setItem("legacy-native-seeded", "true");
    }
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Older tabs are closed — recover study work" })).toBeVisible();
  await page.getByRole("button", { name: "Older tabs are closed — recover study work" }).click();
  await expect(page.getByRole("button", { name: "Older tabs are closed — recover study work" })).toHaveCount(0);
  const source = await page.evaluate(() => localStorage.getItem("tempo-pending-training-reviews-v1"));
  await page.reload();
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-training-reviews-v1"))).toBe(source);
  const older = await context.newPage(); await prepareControlledUI(older, [], false, false); await older.goto("/");
  await older.evaluate(() => localStorage.setItem("tempo-pending-teaching-states-v1", JSON.stringify([{ cardId: "old-writer", revision: 1, ply: 0 }])));
  await expect(page.getByRole("button", { name: "Older tabs are closed — recover study work" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-training-reviews-v1"))).toBe(source);
  await older.close();
});

test("issue221 denied native storage cannot report saved success", async ({ page }) => {
  await prepareControlledUI(page, [], false);
  await page.addInitScript(() => { indexedDB.open = () => { throw new DOMException("Storage denied", "SecurityError"); }; });
  await page.goto("/"); await expect(page.getByRole("alert")).toContainText("Could not open durable study storage");
  await expect(page.locator(".board-frame")).toHaveCount(0);
});

test("issue221 native quota failure aborts the intent and permits recovery", async ({ page, context, baseURL }) => {
  await prepareControlledUI(page, [], false);
  const protocol = await context.newCDPSession(page);
  // Impose the quota before IndexedDB can reserve space under the ordinary quota.
  await protocol.send("Storage.overrideQuotaForOrigin", { origin: baseURL!, quotaSize: 1_000_000 });
  await page.goto("/"); await nativeStore(page);
  try {
    const result = await page.evaluate(async envelope => {
      const store = (window as unknown as NativeWindow).nativeIntents;
      try { await store.put({ ...envelope, payload: { answer: Array.from({ length: 64 }, () => btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(65_536))))).join("") } }); return "unexpected success"; }
      catch (error) {
        if (!String(error).includes("QuotaExceededError")) throw error;
        return (await store.get(envelope.key)) ? "unexpected committed record" : "transaction aborted";
      }
    }, command("quota"));
    expect(result).toBe("transaction aborted");
  } finally { await protocol.send("Storage.overrideQuotaForOrigin", { origin: baseURL! }); await protocol.detach(); }
  await page.evaluate(envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), command("quota"));
  expect(await page.evaluate(() => (window as unknown as NativeWindow).nativeIntents.get("quota"))).toMatchObject(command("quota"));
});

test("issue186 committed review next-load retry and reload never resubmit the grade", async ({ page }) => {
  const api = await prepareControlledUI(page, [openingCard("committed", "Original card", "e2e4", 901)], false);
  let committed = false, posts = 0, failedLoads = 0;
  const next = openingCard("successor", "Recovered successor", "d2d4", 902);
  api.register({ method: "GET", path: "/api/cards/successor/teaching", query: {} }, route => route.fulfill({ json: { states: [] } }));
  api.register({ method: "POST", path: "/api/cards/committed/review", query: {}, validate: request => validateReview(request, "committed", 901) }, route => {
    posts++; committed = true; return route.fulfill({ json: { persisted: true } });
  });
  for (const query of ([{ limit: "20" }, { limit: "20", include_opening_evidence: "true" }] as Record<string, string>[])) api.register({ method: "GET", path: "/api/queue/window", query }, route => {
    if (!committed) return route.fulfill({ json: { cards: [openingCard("committed", "Original card", "e2e4", 901)], count: 1 } });
    if (failedLoads++ < 2) return route.abort("connectionfailed");
    return route.fulfill({ json: { cards: [next], count: 1 } });
  });
  await page.goto("/"); await move(page, "e2", "e4");
  await page.getByRole("button", { name: "Retry loading the queue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Recovered successor", exact: true })).toBeVisible();
  expect(posts).toBe(1); expect(failedLoads).toBe(3);
  await page.reload(); await expect(page.getByRole("heading", { name: "Recovered successor", exact: true })).toBeVisible();
  expect(posts).toBe(1);
  expect(api.traffic.filter(request => /\/api\/(games\/sync|analysis|providers)/.test(request.path))).toEqual([]);
});

test("issue219 wrong tactic survives true reload and guided completion stays Again", async ({ page }) => {
  const tactic = { ...openingCard("wrong-tactic", "Durable tactic", "e2e4", 941), content_type: "tactic" };
  const api = await prepareControlledUI(page, [tactic], false);
  let allowFailure = false;
  api.register({ method: "POST", path: "/api/queue/entries/941/fail", query: {}, validate: request => {
    expect(request.body).toMatchObject({ card_id: "wrong-tactic", expected_revision: 1 });
    expect(request.headers["idempotency-key"]).toBeTruthy();
  } }, route => allowFailure ? route.fulfill({ json: { persisted: true } }) : route.abort("connectionfailed"));
  const reviews: Record<string, unknown>[] = [];
  api.register({ method: "POST", path: "/api/cards/wrong-tactic/review", query: {}, validate: request => {
    const body = request.body as Record<string, unknown>;
    expect(body).toMatchObject({ outcome: "again", guided: true, queue_entry_id: 941 });
    expect(request.headers["idempotency-key"]).toBe(`review-attempt:${body.attempt_id}`);
    reviews.push(body);
  } }, route => route.fulfill({ json: { persisted: true } }));
  let operationId = "";
  api.register({ method: "GET", path: () => `/api/operations/${encodeURIComponent(operationId)}`, query: {} }, route => route.fulfill({ status: 404 }));
  await page.goto("/"); await move(page, "d2", "d4");
  await expect.poll(async () => (await nativeStudyRecords(page)).filter(record => record.kind === "failure").length).toBe(1);
  operationId = (await nativeStudyRecords(page)).find(record => record.kind === "failure")!.requestId;
  await page.reload();
  await expect(page.getByText(/Guided attempt resumed/).first()).toBeVisible();
  expect(reviews).toEqual([]);
  allowFailure = true;
  await move(page, "e2", "e4");
  await expect.poll(() => reviews.length).toBe(1);
  await expect.poll(async () => (await nativeStudyRecords(page)).find(record => record.kind === "review")?.state).toBe("committed");
  expect(reviews[0]).toMatchObject({ outcome: "again", guided: true });
});

test("issue219 specialized lost response reload recovers after the issued head advances", async ({ page }) => {
  const exercise = { ...openingCard("study-card", "Connected exercise", "e2e4", 951), moves: [],
    content_type: "study_exercise", study_id: "native-study", study_exercise_id: "native-exercise", revision: 3 };
  const successor = openingCard("after-study", "Issued after study", "d2d4", 952);
  const api = await prepareControlledUI(page, [exercise], false);
  let answer: Record<string, unknown> | undefined, posts = 0, receiptReads = 0;
  let releaseLostResponse!: () => void;
  const lostResponse = new Promise<void>(resolve => { releaseLostResponse = resolve; });
  api.register({ method: "GET", path: "/api/studies/native-study/exercises/native-exercise/present", query: {} }, route => route.fulfill({ json: {
    id: "native-exercise", revision: 3, type: "choice", prompt: "Choose the durable answer", hint: "", fen: exercise.start_fen,
    options: [{ id: "a", text: "Preserve the original" }, { id: "b", text: "Replace it" }],
  } }));
  api.register({ method: "POST", path: "/api/studies/native-study/exercises/native-exercise/attempts", query: {}, validate: request => {
    answer = request.body as Record<string, unknown>; posts++;
    expect(answer).toMatchObject({ revision: 3, context: "review", card_id: "study-card", queue_entry_id: 951,
      answer: { type: "choice", option_ids: ["a"], displayed_order: ["a", "b"] } });
    expect(request.headers["idempotency-key"]).toBe(answer.attempt_id);
  } }, async route => { await lostResponse; await route.abort("connectionfailed").catch(() => undefined); });
  api.register({ method: "GET", path: () => `/api/operations/${encodeURIComponent(String(answer?.attempt_id))}`, query: {} }, route => {
    receiptReads++; return route.fulfill({ json: { state: "complete", response: { attempt_id: answer?.attempt_id,
      assessment: { outcome: "correct", feedback: "Original result confirmed" }, rating: "correct" } } });
  });
  api.register({ method: "GET", path: "/api/cards/after-study/teaching", query: {} }, route => route.fulfill({ json: { states: [] } }));
  for (const query of ([{ limit: "20" }, { limit: "20", include_opening_evidence: "true" }] as Record<string, string>[])) api.register({ method: "GET", path: "/api/queue/window", query }, route =>
    route.fulfill({ json: { cards: answer ? [successor] : [exercise], count: 1 } }));
  await page.goto("/"); await page.getByLabel("Preserve the original").check();
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect.poll(() => posts).toBe(1);
  const frozen = structuredClone(answer);
  await page.reload();
  releaseLostResponse();
  await expect(page.getByRole("heading", { name: "Issued after study", exact: true })).toBeVisible();
  expect(posts).toBe(1); expect(receiptReads).toBe(1);
  expect((await nativeStudyRecords(page)).find(record => record.kind === "study-command")).toMatchObject({ state: "committed", payload: { body: frozen } });
  expect(api.traffic.filter(request => /\/api\/(games\/sync|analysis|providers)/.test(request.path))).toEqual([]);
});

test("pr285 recovery ownership replacement queue load survives cancelled receipt recovery", async ({ page }, testInfo) => {
  const successor = openingCard("ownership-successor", "Recovery replacement successor", "d2d4", 995);
  const api = await prepareControlledUI(page, [], false);
  const original: IntentEnvelope = { key: "study:ownership:exercise:review:3:original", kind: "study-command", requestId: "ownership-original",
    attemptId: "ownership-attempt", endpoint: "/api/studies/ownership/exercises/exercise/attempts", revision: 3,
    createdAt: "2026-09-18T15:00:00Z", payload: { scope: "study:ownership:exercise:review:3",
      headers: { "Content-Type": "application/json", "Idempotency-Key": "ownership-original" }, body: { attempt_id: "ownership-attempt", revision: 3,
        context: "review", card_id: "original-card", queue_entry_id: 994,
        answer: { type: "choice", option_ids: ["a"], displayed_order: ["a", "b"] } } } };
  let releaseReceipt!: () => void, receiptStarted!: () => void;
  const held = new Promise<void>(resolve => { releaseReceipt = resolve; });
  const started = new Promise<void>(resolve => { receiptStarted = resolve; });
  let issuedSuccessor = false, receiptReads = 0, posts = 0;
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  api.register({ method: "GET", path: "/api/operations/ownership-original", query: {} }, async route => {
    receiptReads++; receiptStarted(); await held;
    await route.fulfill({ json: { operation_id: original.requestId, state: "complete", response: { attempt_id: original.attemptId,
      assessment: { outcome: "correct", feedback: "Original result confirmed" }, rating: "correct" } } });
  });
  api.register({ method: "POST", path: original.endpoint, query: {}, validate: () => { posts++; throw new Error("Original command was reposted"); } }, () => undefined);
  api.register({ method: "GET", path: "/api/cards/ownership-successor/teaching", query: {} }, route => route.fulfill({ json: { states: [] } }));
  for (const query of ([{ limit: "20" }, { limit: "20", include_opening_evidence: "true" }] as Record<string, string>[]))
    api.register({ method: "GET", path: "/api/queue/window", query }, route => route.fulfill({ json: { cards: issuedSuccessor ? [successor] : [], count: Number(issuedSuccessor) } }));
  await page.goto("/"); await expect(page.getByText("No cards due. Your next session will appear here.", { exact: true })).toBeVisible();
  await nativeStore(page, `${new URL(page.url()).origin}:study`);
  await page.evaluate(async envelope => { await (window as unknown as NativeWindow).nativeIntents.put(envelope, { started: true }); }, original);
  await page.evaluate(() => {
    const events: Array<{ event: string; signal: number }> = [], identities = new WeakMap<AbortSignal, number>();
    let next = 0;
    const identity = (signal: AbortSignal) => { if (!identities.has(signal)) identities.set(signal, ++next); return identities.get(signal)!; };
    const add = AbortSignal.prototype.addEventListener, abort = AbortController.prototype.abort;
    // Pass-through browser witnesses observe the real queue controllers and stage waiters.
    AbortSignal.prototype.addEventListener = function (this: AbortSignal, type: string,
      listener: EventListenerOrEventListenerObject | null, options?: boolean | AddEventListenerOptions) {
      if (type === "abort") events.push({ event: "waiter", signal: identity(this) });
      return Reflect.apply(add, this, [type, listener, options]);
    };
    AbortController.prototype.abort = function (reason) { events.push({ event: "abort", signal: identity(this.signal) }); return abort.call(this, reason); };
    (window as unknown as RecoveryTraceWindow).recoveryOwnership = { events, restore() {
      AbortSignal.prototype.addEventListener = add; AbortController.prototype.abort = abort;
    } };
    window.dispatchEvent(new Event("online"));
  });
  try {
    await started; issuedSuccessor = true;
    await page.evaluate(() => {
      (window as unknown as RecoveryTraceWindow).recoveryOwnership.events.push({ event: "B-start", signal: 0 });
      window.dispatchEvent(new Event("online"));
    });
    await expect.poll(() => page.evaluate(() => {
      const events = (window as unknown as RecoveryTraceWindow).recoveryOwnership.events;
      const replacement = events.slice(events.findIndex(event => event.event === "B-start") + 1);
      const cancelled = replacement.find(event => event.event === "abort");
      return Boolean(cancelled && replacement.some(event => event.event === "waiter" && event.signal !== cancelled.signal &&
        replacement.filter(candidate => candidate.event === "waiter" && candidate.signal === event.signal).length >= 2));
    })).toBe(true);
    // The second queue's initialization and recovery waiters exist while the receipt is still held.
    const ordering = await page.evaluate(() => (window as unknown as RecoveryTraceWindow).recoveryOwnership.events);
    await testInfo.attach("recovery-overlap-ordering", { body: JSON.stringify({ ordering, release: "after B recovery waiter" }, null, 2), contentType: "application/json" });
    releaseReceipt();
    await expect(page.getByRole("heading", { name: "Recovery replacement successor", exact: true })).toBeVisible();
    await expect(page.getByText(/The local queue could not be loaded/)).toHaveCount(0);
    await expect.poll(async () => (await nativeStudyRecords(page)).find(record => record.requestId === original.requestId)?.state).toBe("committed");
    expect((await nativeStudyRecords(page)).filter(record => record.requestId === original.requestId)).toEqual([
      expect.objectContaining({ ...original, state: "committed" }),
    ]);
    expect(receiptReads).toBe(1); expect(posts).toBe(0); expect(errors).toEqual([]);
    expect(api.traffic.filter(request => /\/api\/(games\/sync|analysis|providers)/.test(request.path))).toEqual([]);
  } finally {
    releaseReceipt();
    await page.evaluate(() => { const native = window as unknown as RecoveryTraceWindow; native.recoveryOwnership.restore(); native.nativeIntents.close(); });
  }
});

test("issue219 two tabs retain competing outcomes on one issued head", async ({ page, context }) => {
  const card = openingCard("competing", "One issued head", "e2e4", 961);
  const api = await prepareControlledUI(page, [card], false);
  let releaseFirst!: () => void;
  const firstResponse = new Promise<void>(resolve => { releaseFirst = resolve; });
  let posted = 0, reconciled = 0;
  api.register({ method: "POST", path: "/api/queue/entries/961/fail", query: {}, validate: request => {
    expect(request.body).toMatchObject({ card_id: "competing", expected_revision: 1 });
  } }, route => route.fulfill({ json: { persisted: true } }));
  api.register({ method: "POST", path: "/api/cards/competing/review", query: {}, validate: request => {
    const body = request.body as Record<string, unknown>;
    expect(body).toMatchObject({ queue_entry_id: 961 });
    expect(request.headers["idempotency-key"]).toBe(`review-attempt:${body.attempt_id}`);
    api.register({ method: "GET", path: `/api/operations/${encodeURIComponent(`review-attempt:${body.attempt_id}`)}`, query: {} }, route => route.fulfill({ status: 404 }));
  } }, async route => {
    if (++posted === 1) { await firstResponse; await route.fulfill({ json: { persisted: true } }); }
    else await route.fulfill({ status: 409, json: { detail: "This issued head already has a result.", retryable: false } });
  });
  api.register({ method: "POST", path: "/api/cards/competing/review/reconcile", query: {}, validate: request => {
    expect(request.body).toMatchObject({ queue_entry_id: 961, outcome: "correct", guided: false });
    expect(request.headers["idempotency-key"]).toMatch(/^review-reconcile:.+:1$/);
    reconciled++;
  } }, route => route.fulfill({ json: { persisted: false, conflict: { code: "competing_review", message: "Another tab saved Again on this issued head.", retryable: false } } }));
  const second = await context.newPage(); await prepareControlledUI(second, [card], false);
  // prepareControlledUI shares the context registry; restore our authoritative queue handler last.
  for (const query of ([{ limit: "20" }, { limit: "20", include_opening_evidence: "true" }] as Record<string, string>[])) api.register({ method: "GET", path: "/api/queue/window", query }, route =>
    route.fulfill({ json: { cards: posted ? [] : [card], count: posted ? 0 : 1 } }));
  await Promise.all([page.goto("/"), second.goto("/")]);
  await page.getByRole("button", { name: "Show move" }).click();
  await move(page, "e2", "e4");
  await expect.poll(async () => (await nativeStudyRecords(page)).filter(record => record.kind === "review").length).toBe(1);
  await move(second, "e2", "e4");
  await expect.poll(async () => (await nativeStudyRecords(page)).filter(record => record.kind === "review").length).toBe(2);
  releaseFirst();
  await expect.poll(async () => (await nativeStudyRecords(page)).filter(record => record.kind === "review").map(record => record.state).sort()).toEqual(["committed", "conflicted"]);
  const reviews = (await nativeStudyRecords(page)).filter(record => record.kind === "review");
  expect(reviews.find(record => record.state === "committed")?.payload).toMatchObject({ outcome: "again", guided: true });
  expect(reviews.find(record => record.state === "conflicted")?.payload).toMatchObject({ outcome: "correct", guided: false });
  expect(posted).toBe(2); expect(reconciled).toBe(1);
  await second.reload();
  expect((await nativeStudyRecords(second)).filter(record => record.kind === "review").map(record => record.state).sort()).toEqual(["committed", "conflicted"]);
  await second.close();
});

test("issue219 defense recognition survives reload with its immutable assessment", async ({ page }) => {
  const originalFen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1";
  const card = { ...openingCard("defense-native", "Defensive decision", "a2a3", 971), content_type: "defense",
    source_ref: "native-candidate", start_fen: originalFen, revision: 2 };
  const api = await prepareControlledUI(page, [card], false);
  let recognition: Record<string, unknown> | undefined, posts = 0, reads = 0;
  const response = () => ({ status: "ready_for_move", recognition_correct: true, feedback: { knight_route: [], sound_moves: [],
    refutation_uci: [], control_explanation: "The original assessment is saved. Choose a defense." } });
  api.register({ method: "GET", path: "/api/defense-exercises/native-candidate", query: {} }, route => route.fulfill({ json: {
    candidate_id: "native-candidate", card_id: "defense-native", exercise_revision: 2, rubric_version: 3,
    recognition_required: true, proposed_move_uci: "a2a3", proposed_move_san: "a3", preview_fen: "4k3/8/8/8/1n6/P7/8/R3K3 b Q - 0 1",
  } }));
  api.register({ method: "POST", path: "/api/defense-exercises/native-candidate/recognition", query: {}, validate: request => {
    recognition = request.body as Record<string, unknown>; posts++;
    expect(recognition).toMatchObject({ exercise_revision: 2, rubric_version: 3, queue_entry_id: 971, hinted: false,
      dangerous_piece_square: "b4", destination_square: "c2", king_square: "e1", major_square: "a1", consequence: "checking_fork" });
    expect(request.headers["idempotency-key"]).toBe(`defense-recognition:${recognition.attempt_id}`);
  } }, route => route.abort("connectionfailed"));
  api.register({ method: "GET", path: () => `/api/operations/${encodeURIComponent(`defense-recognition:${recognition?.attempt_id}`)}`, query: {} }, route => {
    reads++; return route.fulfill({ json: { state: "complete", response: response() } });
  });
  await page.goto("/");
  for (const [label, square] of [["Dangerous piece", "b4"], ["Destination", "c2"], ["Your king", "e1"], ["Threatened piece", "a1"]]) {
    await page.getByLabel(`${label} square`, { exact: true }).fill(square);
    await page.getByLabel(`${label} square`, { exact: true }).press("Enter");
  }
  await page.getByRole("combobox", { name: "Consequence", exact: true }).selectOption("checking_fork");
  await page.getByRole("button", { name: "Submit assessment", exact: true }).click();
  await expect.poll(() => posts).toBe(1);
  await expect(page.getByRole("alert").filter({ hasText: /fetch|network/i }).first()).toBeVisible();
  const frozen = structuredClone(recognition);
  await page.reload();
  await expect(page.getByRole("button", { name: "Continue to defense", exact: true })).toBeVisible();
  expect(posts).toBe(1); expect(reads).toBe(1);
  expect((await nativeStudyRecords(page)).find(record => record.kind === "defense-command")).toMatchObject({ state: "committed", payload: { body: frozen } });
  api.register({ method: "POST", path: "/api/defense-exercises/native-candidate/attempt", query: {}, validate: request => {
    expect(request.body).toMatchObject({ exercise_revision: 2, queue_entry_id: 971, move_uci: "a2a4", recognition_attempt_id: recognition?.attempt_id });
  } }, route => route.fulfill({ json: { status: "needs_analysis" } }));
  await page.getByRole("button", { name: "Continue to defense", exact: true }).click();
  await move(page, "a2", "a4");
  await expect(page.getByText("Analyzing this legal defense. Your study result has not been recorded yet.", { exact: true })).toBeVisible();
  expect(await nativeStudyRecords(page)).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "defense-command", state: "pending",
    payload: expect.objectContaining({ body: expect.objectContaining({ move_uci: "a2a4", recognition_attempt_id: recognition?.attempt_id }) }) })]));
  expect(api.traffic.filter(request => /\/api\/(games\/sync|analysis|providers)/.test(request.path))).toEqual([]);
});

test("pr285 rejected native answer permits corrected identity after true reload", async ({ page }) => {
  const card = { ...openingCard("repair-study", "Repair study", "e2e4", 981), content_type: "study_exercise", study_id: "repair", study_exercise_id: "exercise", revision: 1, moves: [] };
  const api = await prepareControlledUI(page, [card], false);
  const requests: Array<Record<string, unknown>> = [];
  api.register({ method: "GET", path: "/api/studies/repair/exercises/exercise/present", query: {} }, route => route.fulfill({ json: { id: "exercise", revision: 1, type: "move_line", mode: "single", prompt: "Submit exactly one move", hint: "", fen: card.start_fen } }));
  api.register({ method: "POST", path: "/api/studies/repair/exercises/exercise/attempts", query: {}, validate: request => {
    const body = request.body as Record<string, unknown>; requests.push(body); expect(request.headers["idempotency-key"]).toBe(body.attempt_id);
  } }, route => {
    const body = requests.at(-1)!;
    return route.fulfill({ status: requests.length === 1 ? 422 : 200, json: requests.length === 1 ? { detail: "Submit one move" } : { attempt_id: body.attempt_id, assessment: { outcome: "correct", feedback: "Corrected native answer saved" }, rating: "correct" } });
  });
  api.register({ method: "GET", path: () => `/api/studies/repair/exercises/exercise/attempts/${requests.at(-1)?.attempt_id}/feedback`, query: {} }, route => route.fulfill({ json: { specification: { type: "move_line", mode: "single", grading_policy: "reference", prompt: "Submit exactly one move", accepted_lines: [["e2e4"]], explanation: "Develop" } } }));
  await page.goto("/"); await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Submit one move" })).toBeVisible();
  await expect.poll(async () => (await nativeStudyRecords(page)).find(record => record.kind === "study-command")?.state).toBe("discarded");
  await page.reload(); await expect(page.getByRole("alert").filter({ hasText: "Submit one move" })).toBeVisible();
  await page.getByLabel("Coordinates or UCI move").fill("e2e4"); await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(page.getByText("Corrected native answer saved", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2); expect(requests[0].attempt_id).not.toBe(requests[1].attempt_id);
  expect(requests[0].answer).toEqual({ type: "move_line", moves: [] }); expect(requests[1].answer).toEqual({ type: "move_line", moves: ["e2e4"] });
  const records = (await nativeStudyRecords(page)).filter(record => record.kind === "study-command");
  expect(records).toHaveLength(2);
  expect(records.find(record => record.requestId === requests[0].attempt_id)).toMatchObject({ state: "discarded", payload: { body: requests[0] } });
  expect(records.find(record => record.requestId === requests[1].attempt_id)).toMatchObject({ state: "committed", payload: { body: requests[1] } });
  expect(api.traffic.filter(request => /\/api\/(games\/sync|analysis|providers)/.test(request.path))).toEqual([]);
});

test("pr285 native storage retry reopens after a later transaction failure", async ({ page }) => {
  await prepareControlledUI(page, [openingCard("retry-storage", "Retry storage card", "e2e4", 982)]);
  await expect(page.locator(".board-frame")).toBeVisible();
  await nativeStore(page, await page.evaluate(() => `${location.origin}:study`));
  await page.evaluate(() => {
    const original = IDBIndex.prototype.getAll;
    Object.assign(window, { restoreIntentReads: () => { IDBIndex.prototype.getAll = original; } });
    IDBIndex.prototype.getAll = function (...args) { if (this.objectStore.name === "intents") throw new DOMException("refresh stopped", "AbortError"); return original.apply(this, args); };
  });
  await page.evaluate(envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), { ...command("retry-preserved"), kind: "fixture" });
  await expect(page.getByRole("alert").filter({ hasText: "refresh stopped" }).first()).toBeVisible(); await expect(page.locator(".board-frame")).toHaveCount(0);
  await page.evaluate(() => (window as unknown as { restoreIntentReads(): void }).restoreIntentReads());
  await page.getByRole("button", { name: "Retry study storage" }).click();
  await expect(page.locator(".board-frame")).toBeVisible();
  expect((await nativeStudyRecords(page)).find(record => record.key === "retry-preserved")).toMatchObject({ state: "pending", kind: "fixture" });
});

test("pr285 native legacy reimport recovers sequence two without rollback", async ({ page }) => {
  const api = await prepareControlledUI(page, [], false);
  const legacy = { backendId: "legacy-repair", queueEntryId: 983, attemptId: "legacy-repair", outcome: "again", guided: true,
    completedAt: "2026-09-18T16:00:00Z", state: "conflicted", reconciliationSequence: 1, conflict: { code: "stale", message: "Changed", retryable: false } };
  await page.addInitScript(legacy => { if (!localStorage.getItem("repair-legacy-seeded")) { localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([legacy])); localStorage.setItem("repair-legacy-seeded", "true"); } }, legacy);
  let reads = 0;
  api.register({ method: "GET", path: "/api/operations/review-reconcile%3Alegacy-repair%3A2", query: {} }, route => { reads++; return route.fulfill({ json: { state: "complete", response: { persisted: true } } }); });
  await page.goto("/");
  await expect.poll(async () => (await nativeStudyRecords(page)).find(record => record.key === "review:legacy-repair")?.state).toBe("conflicted");
  await nativeStore(page, await page.evaluate(() => `${location.origin}:study`));
  await page.evaluate(async () => { await (window as unknown as NativeWindow).nativeIntents.update("review:legacy-repair", current => ({ ...current, state: "pending", data: { ...current.data as object, state: "reconciling", reconciliationSequence: 2 } })); });
  await page.reload();
  await expect.poll(async () => (await nativeStudyRecords(page)).find(record => record.key === "review:legacy-repair")?.state).toBe("committed");
  expect(reads).toBeGreaterThan(0);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1")!)[0].reconciliationSequence)).toBe(1);
});

test("pr285 native practice reset excludes both records across tabs and reload", async ({ page, context }) => {
  await prepareControlledUI(page, []); await nativeStore(page);
  const second = await context.newPage(); await prepareControlledUI(second, []); await nativeStore(second);
  for (const id of ["practice-answer", "practice-assessment"]) await page.evaluate(async envelope => {
    const store = (window as unknown as NativeWindow).nativeIntents; await store.put(envelope); await store.update(envelope.key, current => ({ ...current, state: "committed", receipt: { persisted: true } }));
  }, command(id));
  const value = await page.evaluate(async () => (await (window as unknown as NativeWindow).nativeIntents.boundary("practice", () => true, 0)).value);
  await second.reload(); await nativeStore(second);
  expect(await second.evaluate(async () => {
    const snapshot = await (window as unknown as NativeWindow).nativeIntents.boundary("practice", () => true);
    return { value: snapshot.value, retained: snapshot.records.length, active: snapshot.records.filter(record => record.sequence > snapshot.value).length };
  })).toEqual({ value, retained: 2, active: 0 });
  expect(await second.evaluate(async envelope => { try { await (window as unknown as NativeWindow).nativeIntents.put(envelope, undefined, { boundary: { key: "practice", value: 0 } }); return "unsafe insertion"; } catch { return "stale generation blocked"; } }, command("stale-assessment"))).toBe("stale generation blocked");
  await second.close();
});

test("pr285 native teaching notification carries committed seen and first clean facts", async ({ page, context }) => {
  await prepareControlledUI(page, []); await nativeStore(page);
  const second = await context.newPage(); await prepareControlledUI(second, []); await nativeStore(second);
  await second.evaluate(() => {
    const native = window as unknown as NativeWindow;
    Object.assign(window, { incomingTeaching: [] });
    native.nativeIntents.subscribe(change => { if (change.teachingKeys) void Promise.all(change.teachingKeys.map(key => native.nativeIntents.teachingFact(key))).then(facts => (window as unknown as { incomingTeaching: unknown[] }).incomingTeaching.push(...facts)); });
  });
  for (const fact of [{ key: "native:1:0", cardId: "native", revision: 1, ply: 0, kind: "seen" as const }, { key: "first-clean:native", cardId: "native", revision: 1, kind: "first-clean" as const }])
    await page.evaluate(fact => (window as unknown as NativeWindow).nativeIntents.addTeachingFact(fact), fact);
  await expect.poll(() => second.evaluate(() => (window as unknown as { incomingTeaching: unknown[] }).incomingTeaching.length)).toBe(2);
  expect(await second.evaluate(async () => (await (window as unknown as NativeWindow).nativeIntents.teachingFacts()).map(fact => fact.kind).sort())).toEqual(["first-clean", "seen"]);
  await second.close();
});
