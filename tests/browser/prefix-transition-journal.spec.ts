import { test, expect, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { openJournalHarness, reserveJournal, persistedJournal, journalBundle, journalKey } from "./support/prefix-transition-journal-browser";
const fixture = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/transition.json", "utf8"));
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(accept => { resolve = accept; }); return { promise, resolve }; }
function complete(operationId: string) {
  return { operation_id: operationId, state: "complete", transition: { state: "complete", graph_generation: 2, queue_date: fixture.result.queue_date, recovery: null },
    response: { ...fixture.result, operation_id: operationId } };
}
test("audit187_real_web_locks_serialize_same_origin_reservations_and_storage_events", async ({ context, page }) => {
  const secondTab = await context.newPage(); await openJournalHarness(page); await openJournalHarness(secondTab);
  for (const tab of [page, secondTab]) await tab.evaluate(key => {
    window.journalStorageEvents = [];
    window.addEventListener("storage", event => { if (event.key === key) window.journalStorageEvents.push(event.newValue); });
  }, journalKey);
  const operations = await Promise.all([reserveJournal(page), reserveJournal(secondTab)]);
  expect(new Set(operations.map(operation => operation.operationId)).size).toBe(1);
  expect((await persistedJournal(page))?.operationId).toBe(operations[0].operationId);
  await expect.poll(async () => (await Promise.all([page, secondTab].map(tab => tab.evaluate(() => window.journalStorageEvents.length)))).reduce((total, count) => total + count, 0)).toBe(1);
});
test("audit187_real_no_locks_check_write_race_must_keep_one_operation", async ({ context, page }) => {
  const secondTab = await context.newPage(); let deliveries = 0;
  for (const tab of [page, secondTab]) {
    await openJournalHarness(tab); await tab.route("**/api/**", route => { deliveries++; return route.fulfill({ json: {} }); });
    await tab.evaluate(() => Object.defineProperty(navigator, "locks", { value: undefined, configurable: true }));
  }
  const refusals = await Promise.all([page, secondTab].map(tab => tab.evaluate(async () => {
    let allocations = 0; const originalRandomUUID = crypto.randomUUID.bind(crypto);
    crypto.randomUUID = () => { allocations++; return originalRandomUUID() as `${string}-${string}-${string}-${string}-${string}`; };
    try { await window.prefixJournal.beginPrefixTransition("rep", window.prefixJournal.request, window.prefixJournal.summary); return { allocations, refused: false, message: "" }; }
    catch (failure) { return { allocations, refused: true, message: (failure as Error).message }; }
  })));
  for (const refusal of refusals) { expect(refusal.refused).toBe(true); expect(refusal.allocations).toBe(0); expect(refusal.message).toMatch(/Web Locks.*HTTPS or localhost/); }
  expect(deliveries).toBe(0); expect(await persistedJournal(page)).toBeNull();
});
for (const olderState of ["pending", "unknown", "blocked", "failed", "transport_error"])
  test(`audit187_real_tabs_published_state_and_ui_survive_older_${olderState}`, async ({ context, page }, testInfo) => {
    const secondTab = await context.newPage(); await openJournalHarness(page); await openJournalHarness(secondTab);
    const operation = await reserveJournal(page); const firstRequest = deferred<Route>(); let deliveries = 0;
    await page.route("**/api/operations/**", route => { firstRequest.resolve(route); });
    await page.route("**/prefix-transition/apply", route => { deliveries++; return route.fulfill({ status: 202, json: {} }); });
    const oldRecovery = page.evaluate(() => window.prefixJournal.recoverPrefixTransition("rep", { signal: new AbortController().signal }));
    const heldResponse = await firstRequest.promise;
    await secondTab.route("**/api/operations/**", route => route.fulfill({ json: complete(operation.operationId) }));
    await secondTab.evaluate(() => window.prefixJournal.mountPanel());
    await expect(secondTab.getByRole("heading", { name: "Prefix transition published", exact: true })).toBeVisible();
    if (olderState === "transport_error") await heldResponse.abort("failed");
    else await heldResponse.fulfill({ json: { operation_id: operation.operationId, state: olderState, error: olderState === "failed" ? { code: "staging_failed" } : undefined } });
    expect((await oldRecovery)?.state).toBe("published"); const current = await persistedJournal(secondTab);
    expect(current?.request).toEqual(operation.request); expect(current?.summary).toEqual(operation.summary);
    expect(current?.state).toBe("published"); expect(current?.result?.operation_id).toBe(operation.operationId); expect(deliveries).toBe(0);
    await expect(secondTab.getByRole("heading", { name: "Prefix transition published", exact: true })).toBeVisible();
    await testInfo.attach("publication-schedule", { contentType: "application/json", body: Buffer.from(JSON.stringify({ olderState, current, deliveries })) });
  });
for (const replaceCleared of [false, true])
  test(`audit187_real_clear_between_check_and_write_cannot_resurrect_journal_replace_${replaceCleared}`, async ({ context, page }) => {
    const secondTab = await context.newPage(); await openJournalHarness(page); await openJournalHarness(secondTab);
    const operation = await reserveJournal(page); const firstRequest = deferred<Route>();
    await page.route("**/api/operations/**", route => { firstRequest.resolve(route); });
    const oldRecovery = page.evaluate(async () => {
      try { return await window.prefixJournal.recoverPrefixTransition("rep", { signal: new AbortController().signal }); }
      catch (failure) { return { rejected: (failure as Error).message }; }
    }); const held = await firstRequest.promise;
    await secondTab.route("**/api/operations/**", route => route.fulfill({ json: complete(operation.operationId) }));
    await secondTab.evaluate(() => window.prefixJournal.recoverPrefixTransition("rep", { signal: new AbortController().signal }));
    await secondTab.evaluate(id => window.prefixJournal.clearResolvedPrefixTransition("rep", id), operation.operationId);
    const newer = replaceCleared ? await secondTab.evaluate(() => window.prefixJournal.beginPrefixTransition("rep", { ...window.prefixJournal.request, plan_id: "fresh-approved-plan" }, window.prefixJournal.summary)) : null;
    await held.fulfill({ json: { operation_id: operation.operationId, state: "blocked" } });
    expect(await oldRecovery).toHaveProperty("rejected");
    if (newer) expect((await persistedJournal(secondTab))?.operationId).toBe(newer.operationId);
    else expect(await persistedJournal(secondTab)).toBeNull();
  });
for (const replaceCleared of [false, true])
  test(`audit187_real_publication_write_excludes_clear_and_reservation_${replaceCleared}`, async ({ context, page }) => {
    const secondTab = await context.newPage(); await openJournalHarness(page); await openJournalHarness(secondTab);
    const operation = await reserveJournal(page); await page.route("**/api/operations/**", route => route.fulfill({ json: complete(operation.operationId) }));
    const session = await context.newCDPSession(page); await session.send("Debugger.enable");
    const lineNumber = journalBundle.split("\n").findIndex(line => line.includes("localStorage.setItem(storageKey(proposed.repertoireId)"));
    expect(lineNumber).toBeGreaterThan(0); const paused = deferred<void>(); session.once("Debugger.paused", () => paused.resolve());
    await session.send("Debugger.setBreakpointByUrl", { urlRegex: "prefix-journal-test\\.js$", lineNumber });
    const publishing = page.evaluate(() => window.prefixJournal.recoverPrefixTransition("rep", { signal: new AbortController().signal }));
    try {
      await paused.promise;
      await secondTab.evaluate(({ operationId, replace }) => {
        window.journalClearing = window.prefixJournal.clearResolvedPrefixTransition("rep", operationId);
        window.journalReservation = window.journalClearing.then(() => replace ? window.prefixJournal.beginPrefixTransition("rep", { ...window.prefixJournal.request, plan_id: "fresh-approved-plan" }, window.prefixJournal.summary) : null);
      }, { operationId: operation.operationId, replace: replaceCleared });
      await expect.poll(() => secondTab.evaluate(async () => (await navigator.locks.query()).pending?.length ?? 0)).toBe(1);
      expect((await persistedJournal(secondTab))?.state).toBe("pending");
      await session.send("Debugger.resume"); expect((await publishing)?.state).toBe("published");
      await secondTab.evaluate(() => window.journalReservation);
      const current = await persistedJournal(secondTab);
      if (replaceCleared) { expect(current?.operationId).not.toBe(operation.operationId); expect(current?.request.plan_id).toBe("fresh-approved-plan"); }
      else expect(current).toBeNull();
    } finally { await session.send("Debugger.disable"); await session.detach(); }
  });
test("audit187_real_known_journal_deletion_keeps_recovery_notice_and_guard", async ({ page }) => {
  await openJournalHarness(page); const operation = await reserveJournal(page);
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{ id: "rep", name: "Combined Black", source_name: "audit.pgn", line_count: 2, card_count: 5, due_count: 0, trained_color: "black" }] } }));
  await page.evaluate(() => window.prefixJournal.mountRepertoire());
  await expect(page.getByRole("button", { name: "Resume prefix transition" })).toBeVisible();
  await page.evaluate(key => localStorage.removeItem(key), journalKey);
  await expect(page.getByText(/browser recovery record is missing/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume prefix transition" })).toBeVisible();
  expect(await page.evaluate(() => window.prefixJournal.readPrefixStructuralAdmission({ repertoireIds: ["rep"], scopeComplete: true }).paused)).toBe(true);
  expect(await page.evaluate(() => window.prefixJournal.readPrefixTransition("rep")?.operationId)).toBe(operation.operationId);
});
test("audit187_real_page_reload_with_retained_journal_recovers_same_identity", async ({ page }) => {
  await openJournalHarness(page); const operation = await reserveJournal(page); await page.reload();
  await page.evaluate(async () => { const path = "/prefix-journal-test.js"; window.prefixJournal = await import(path); });
  await page.route("**/api/operations/**", route => route.fulfill({ json: complete(operation.operationId) }));
  const result = await page.evaluate(() => window.prefixJournal.recoverPrefixTransition("rep", { signal: new AbortController().signal }));
  expect(result?.operationId).toBe(operation.operationId); expect(result?.state).toBe("published");
});
test("audit187_real_page_reload_after_total_journal_loss_has_no_rediscovery_contract", async ({ page }) => {
  await openJournalHarness(page); await reserveJournal(page); await page.evaluate(key => localStorage.removeItem(key), journalKey); await page.reload();
  await page.evaluate(async () => { const path = "/prefix-journal-test.js"; window.prefixJournal = await import(path); });
  expect(await page.evaluate(() => window.prefixJournal.readPrefixTransition("rep"))).toBeNull();
  expect(await page.evaluate(() => window.prefixJournal.knownPrefixTransitions())).toEqual([]);
  // This qualifies the documented limitation, never an unconditional reload guarantee.
});
