import { test, expect, api, nav } from "./product-fixtures";
import type { Page, Request, Response } from "@playwright/test";

/** Observe every response before clicking, including retries during UI assertions. */
async function readPrefixesWithExplicitRetries(page: Page, matchesPathname: (pathname: string) => boolean,
  startRead: () => Promise<unknown>, retryRead: () => Promise<unknown>, label: string,
  maximumDatabaseRefusals: number, isDetail = false) {
  const responses: Response[] = [];
  const timeline: { at: string; event: string; status?: number; detail?: string }[] = [];
  let wakeResponseWaiter: (() => void) | undefined;
  const observeResponse = (response: Response) => {
    if (response.request().method() !== "GET" || !matchesPathname(new URL(response.url()).pathname)) return;
    responses.push(response);
    timeline.push({ at: new Date().toISOString(), event: "response", status: response.status() });
    wakeResponseWaiter?.();
  };
  const observeActivity = (request: Request) => {
    if (new URL(request.url()).pathname === "/api/system/browser-activity")
      timeline.push({ at: new Date().toISOString(), event: "browser activity marker" });
  };
  page.on("request", observeActivity);
  page.on("response", observeResponse);
  // This is the component's existing per-attempt AbortSignal timeout. A user
  // retry creates a new request; automatic foreground waits retain its budget.
  let attemptDeadline = Date.now() + 15_000;
  const remainingAttemptMilliseconds = () => {
    const remaining = attemptDeadline - Date.now();
    expect(remaining, `${label} request settles within its 15-second product deadline`).toBeGreaterThan(0);
    return remaining;
  };
  async function nextResponse() {
    if (!responses.length) await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        wakeResponseWaiter = undefined;
        reject(new Error(`${label} has no response before its request deadline`));
      }, remainingAttemptMilliseconds());
      wakeResponseWaiter = () => {
        clearTimeout(timer);
        wakeResponseWaiter = undefined;
        resolve();
      };
    });
    return responses.shift()!;
  }
  try {
    timeline.push({ at: new Date().toISOString(), event: "initial click" });
    await startRead();
    let databaseRefusals = 0;
    for (;;) {
      const response = await nextResponse();
      if (response.status() !== 503) {
        expect(response.status()).toBe(200);
        return;
      }
      expect(response.headers()["retry-after"]).toBe("1");
      const body = await response.json() as { detail: string };
      timeline.push({ at: new Date().toISOString(), event: "refusal", detail: body.detail });
      if (body.detail === "Waiting for foreground activity") {
        await expect(page.getByRole("status")).toContainText("Loading evidence", { timeout: remainingAttemptMilliseconds() });
        await expect(page.getByRole("alert")).toHaveCount(0);
        continue;
      }
      expect(["Waiting for a database section",
        "Prefix difficulty is temporarily unavailable. Retry after study work settles."]).toContain(body.detail);
      databaseRefusals += 1;
      expect(databaseRefusals, `${label} does not silently retry unlimited database refusals`).toBeLessThanOrEqual(maximumDatabaseRefusals);
      await expect(page.getByRole("alert")).toContainText(body.detail, { timeout: remainingAttemptMilliseconds() });
      if (isDetail) await expect(page.getByText("Unknown", { exact: true })).toHaveCount(0);
      attemptDeadline = Date.now() + 15_000;
      timeline.push({ at: new Date().toISOString(), event: "explicit retry click" });
      await retryRead();
    }
  } finally {
    page.off("response", observeResponse);
    page.off("request", observeActivity);
    await test.info().attach(`${label}-admission-timeline`, {
      body: JSON.stringify(timeline, null, 2), contentType: "application/json",
    });
  }
}

for (const { width, forcedDatabaseRefusals, forcedForegroundDeferrals, forcedListDatabaseRefusals, listRefusalAfterAdmission, title } of [
  ...[390, 1280].map(width => ({ width, forcedDatabaseRefusals: 0, forcedForegroundDeferrals: 0, forcedListDatabaseRefusals: 0, listRefusalAfterAdmission: false,
    title: `PD-82 PostgreSQL prefix diagnostics stay read-only and show unknown evidence ${width}` })),
  { width: 1280, forcedDatabaseRefusals: 2, forcedForegroundDeferrals: 0, forcedListDatabaseRefusals: 0, listRefusalAfterAdmission: false,
    title: "PD-82 repeated database refusals require explicit read-only inspection retries" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 1, forcedListDatabaseRefusals: 0, listRefusalAfterAdmission: false,
    title: "PD-82 foreground deferral followed by database refusal still requires explicit retry" },
  { width: 390, forcedDatabaseRefusals: 0, forcedForegroundDeferrals: 0, forcedListDatabaseRefusals: 1, listRefusalAfterAdmission: false,
    title: "PD-82 initial database refusal requires explicit read-only list refresh" },
  { width: 1280, forcedDatabaseRefusals: 0, forcedForegroundDeferrals: 0, forcedListDatabaseRefusals: 1, listRefusalAfterAdmission: true,
    title: "PD-82 foreground deferral before a list refusal restarts admission on explicit Refresh" },
]) test(title, async ({ page, request }) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, { data: { ...settings, initial_depth: 3 } });
  // Deleting the preceding fixture and changing settings schedule publication.
  // Establish an empty settled workspace before timing the actual UI import.
  await expect.poll(async () => {
    const response = await request.get(`${api}/queue/today`, { headers: { "X-Tempo-Work-Class": "background" } });
    if (response.status() === 503 && response.headers()["retry-after"]) return null;
    expect(response.ok()).toBe(true);
    const queue = await response.json();
    return { state: queue.projection.state, pending: queue.projection.refresh_pending, count: queue.cards.length };
  }, { timeout: 30_000, message: "Previous fixture publication settles empty before UI import" })
    .toEqual({ state: "ready", pending: 0, count: 0 });
  // Enter the empty library before importing: visiting Train after import can
  // legitimately capture teaching assistance and asynchronously admit cards.
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/"); await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: `diagnostics-${width}.pgn`, mimeType: "application/x-chess-pgn",
    buffer: Buffer.from('[Event "Opening"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *'),
  });
  const [imported] = await Promise.all([
    page.waitForResponse(response => response.url().includes("/api/imports/pgn") && response.request().method() === "POST"),
    page.getByRole("button", { name: "Import repertoire", exact: true }).click(),
  ]);
  expect(imported.ok()).toBeTruthy();
  const { repertoire_id: repertoireId } = await imported.json();
  if (forcedDatabaseRefusals) {
    // This case proves inspection retries, not the import completion dialog.
    // The API has persisted the fixture; closing stops dialog polling before
    // the existing background graph/queue checks observe publication.
    await page.getByRole("button", { name: "Close import dialog" }).click();
  } else await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Insights"); await nav(page, "Repertoire");
  await expect.poll(async () => (await request.get(`${api}/repertoires/${repertoireId}/prefix-diagnostics`, { headers: { "X-Tempo-Work-Class": "background" } })).status(), { timeout: 30_000 }).toBe(200);
  let settledQueueCards: unknown[] = [];
  await expect.poll(async () => {
    const response = await request.get(`${api}/queue/today`, { headers: { "X-Tempo-Work-Class": "background" } });
    if (response.status() === 503 && response.headers()["retry-after"]) return false;
    expect(response.ok()).toBe(true);
    const queue = await response.json();
    // Partial publication may expose a card before its admission fields settle.
    // Capture the baseline from the same ready response that proves completion.
    if (queue.projection.state !== "ready" || queue.projection.refresh_pending !== 0
      || !queue.cards.some((card: { repertoire_id: string }) => card.repertoire_id === repertoireId)) return false;
    settledQueueCards = queue.cards;
    return true;
  }, { timeout: 30_000 }).toBe(true);
  const diagnosticReads: string[] = [];
  page.on("request", resource => {
    if (resource.url().includes("/prefix-diagnostics")) {
      diagnosticReads.push(resource.url());
      expect(resource.method()).toBe("GET");
      expect(resource.headers()["x-tempo-work-class"]).toBe("background");
    }
  });
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `diagnostics-${width}`, exact: true }) });
  await expect(card).toBeVisible();
  expect(diagnosticReads).toHaveLength(0);
  let injectedListDatabaseRefusals = 0;
  if (forcedListDatabaseRefusals) await page.route("**/prefix-diagnostics?*", async route => {
    if (injectedListDatabaseRefusals < forcedListDatabaseRefusals) {
      if (listRefusalAfterAdmission) {
        // Retain the real foreground admission path before injecting one
        // database refusal. Refresh must then traverse real admission again.
        const admittedResponse = await route.fetch();
        if (admittedResponse.status() !== 200) {
          await route.fulfill({ response: admittedResponse });
          return;
        }
      }
      injectedListDatabaseRefusals++;
      await route.fulfill({ status: 503, headers: { "Retry-After": "1" },
        contentType: "application/json", body: JSON.stringify({ detail: "Waiting for a database section" }) });
    } else await route.continue();
  });
  await readPrefixesWithExplicitRetries(page, pathname => pathname === `/api/repertoires/${repertoireId}/prefix-diagnostics`,
    async () => {
      await card.locator("details.card-menu summary").click();
      await card.getByRole("menuitem", { name: "Prefix difficulty", exact: true }).click();
    },
    () => page.getByRole("button", { name: "Refresh prefixes", exact: true }).click(),
    "listing", forcedListDatabaseRefusals + 2);
  expect(injectedListDatabaseRefusals).toBe(forcedListDatabaseRefusals);
  const inspectButton = page.getByRole("button", { name: /^Inspect prefix/ });
  // Once admitted, evidence publication retains the original rendering budget.
  await expect(inspectButton).toBeVisible({ timeout: 5_000 });
  await expect(inspectButton).toBeEnabled();
  let injectedDatabaseRefusals = 0;
  let injectedForegroundDeferrals = 0;
  if (forcedDatabaseRefusals) await page.route("**/prefix-diagnostics/*", async route => {
    if (injectedForegroundDeferrals < forcedForegroundDeferrals) {
      injectedForegroundDeferrals += 1;
      await route.fulfill({ status: 503, headers: { "Retry-After": "1" },
        contentType: "application/json", body: JSON.stringify({ detail: "Waiting for foreground activity" }) });
    } else if (injectedDatabaseRefusals < forcedDatabaseRefusals) {
      if (forcedForegroundDeferrals) {
        // Once the real read is admitted, inject the database refusal. The
        // explicit inspection retry must pass through foreground admission anew.
        const admittedResponse = await route.fetch();
        if (admittedResponse.status() !== 200) {
          await route.fulfill({ response: admittedResponse });
          return;
        }
      }
      injectedDatabaseRefusals += 1;
      await route.fulfill({ status: 503, headers: { "Retry-After": "1" },
        contentType: "application/json", body: JSON.stringify({ detail: "Waiting for a database section" }) });
    } else await route.continue();
  });
  await readPrefixesWithExplicitRetries(page, pathname => pathname.startsWith(`/api/repertoires/${repertoireId}/prefix-diagnostics/`),
    () => inspectButton.click(), () => inspectButton.click(), "inspection", forcedDatabaseRefusals + 2, true);
  if (forcedDatabaseRefusals) expect(injectedDatabaseRefusals).toBe(forcedDatabaseRefusals);
  expect(injectedForegroundDeferrals).toBe(forcedForegroundDeferrals);
  await expect(page.getByText("Unknown", { exact: true })).toHaveCount(3, { timeout: 5_000 });
  await expect(page.getByText("No observations for this decision.", { exact: true })).toHaveCount(3);
  await expect(page.getByText(/Latest 0 of up to 100 attempts/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: test.info().outputPath(`prefix-diagnostics-${width}.png`), fullPage: true });
  expect(await page.getByRole("button", { name: /apply|shorten|recommend/i }).count()).toBe(0);
  const after = await (await request.get(`${api}/queue/today`)).json();
  expect(after.cards).toEqual(settledQueueCards);
  await page.getByRole("button", { name: "← Repertoires" }).click();
  await expect(card).toBeVisible();
});
