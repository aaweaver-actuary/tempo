import { build } from "esbuild";
import type { Page } from "@playwright/test";
import type { IntentStore, IntentEnvelope } from "@tempo/browser-intents";
import { test, expect, prepareControlledUI, openingCard } from "./fixtures";
import { move } from "../browser/native-board";
import { validateReview } from "./contracts";

type NativeWindow = Window & { intentFixture: typeof import("@tempo/browser-intents"); nativeIntents: IntentStore };
const bundle = build({ stdin: { contents: 'export * from "@tempo/browser-intents";', resolveDir: process.cwd() }, bundle: true,
  write: false, format: "iife", globalName: "intentFixture", platform: "browser" }).then(result => result.outputFiles[0].text);
async function nativeStore(page: Page) {
  await page.addScriptTag({ content: await bundle });
  await page.evaluate(async () => { const native = window as unknown as NativeWindow; native.nativeIntents = await native.intentFixture.openIntentStore({ workspace: "native-regression" }); });
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
    await Promise.all(order.map(index => pages[index].evaluate(async envelope => (window as unknown as NativeWindow).nativeIntents.put(envelope), command(`${prefix}:${index}`))));
    const retained = await second.evaluate(async () => (await (window as unknown as NativeWindow).nativeIntents.list()).map(record => record.key));
    expect(retained).toEqual(expect.arrayContaining([`${prefix}:0`, `${prefix}:1`]));
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
      catch { return (await store.get(envelope.key)) ? "unexpected committed record" : "transaction aborted"; }
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
