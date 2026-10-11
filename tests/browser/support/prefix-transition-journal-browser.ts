import { buildSync } from "esbuild";
import { expect, type Page } from "@playwright/test";

export type JournalHarness = typeof import("./prefix-transition-journal-harness");
declare global { interface Window { prefixJournal: JournalHarness; journalClearing?: Promise<void>; journalReservation?: Promise<unknown>; journalStorageEvents: (string | null)[]; } }
// Build authored test support in memory; never commit generated third-party JS.
export const journalBundle = buildSync({ entryPoints: ["tests/browser/support/prefix-transition-journal-harness.tsx"], bundle: true,
  format: "esm", platform: "browser", write: false, define: { __TEMPO_DEMO__: "false", __TEMPO_API_URL__: '""', "process.env.NODE_ENV": '"production"' },
}).outputFiles[0].text;
export const journalKey = "tempo-pending-prefix-transition-v1:rep";
export async function openJournalHarness(page: Page) {
  await page.route("**/prefix-journal-harness", route => route.fulfill({ contentType: "text/html", body: '<!doctype html><div id="audit-root"></div>' }));
  await page.route("**/prefix-journal-test.js", route => route.fulfill({ contentType: "text/javascript", body: journalBundle }));
  await page.goto("/prefix-journal-harness");
  await page.evaluate(async () => {
    Object.defineProperty(window, "Worker", { value: undefined, configurable: true });
    const path = "/prefix-journal-test.js";
    window.prefixJournal = await import(path);
  });
}
export async function reserveJournal(page: Page) {
  return page.evaluate(() => window.prefixJournal.beginPrefixTransition("rep", window.prefixJournal.request, window.prefixJournal.summary));
}
export async function persistedJournal(page: Page) {
  return page.evaluate(key => { const stored = localStorage.getItem(key); return stored ? JSON.parse(stored) as import("../../../app/lib/prefix-transition-command").PrefixTransitionOperation : null; }, journalKey);
}
export async function verifyNativePrefixLocks(page: Page) {
  await openJournalHarness(page);
  expect(await page.evaluate(() => typeof navigator.locks?.request)).toBe("function");
  const operation = await reserveJournal(page);
  expect((await persistedJournal(page))?.operationId).toBe(operation.operationId);
}
