import { test, expect, api, nav } from "./product-fixtures";
import type { Page, Response } from "@playwright/test";

// Install before opening diagnostics: automatic retries may finish while a UI
// assertion is in flight. Recorded responses remain available to the observer.
function observeDiagnostics(page: Page, repertoireId: string) {
  const responses: { response: Response; receivedAt: number }[] = [];
  const diagnosticPath = `/api/repertoires/${repertoireId}/prefix-diagnostics`;
  const recordResponse = (response: Response) => {
    if (new URL(response.url()).pathname.startsWith(diagnosticPath) && response.request().method() === "GET")
      responses.push({ response, receivedAt: Date.now() });
  };
  page.on("response", recordResponse);
  return {
    dispose: () => page.off("response", recordResponse),
    async read(kind: "list" | "detail", trigger: () => Promise<unknown>, replayBufferedResponses = false) {
      let responseCursor = responses.length;
      const requestDeadline = Date.now() + 15_000;
      const remaining = () => {
        const milliseconds = requestDeadline - Date.now();
        expect(milliseconds, `${kind} diagnostics admission stays inside its existing 15-second request deadline`).toBeGreaterThan(0);
        return milliseconds;
      };
      const matches = (response: Response) => kind === "list"
        ? new URL(response.url()).pathname === diagnosticPath
        : new URL(response.url()).pathname.startsWith(`${diagnosticPath}/`);
      const retryButton = page.getByRole("button", { name: kind === "list" ? "Refresh prefixes" : /^Inspect prefix/ });
      await trigger();
      if (replayBufferedResponses) {
        // Deliberately let both the automatic deferral and following database
        // refusal arrive before consuming them. No wall-clock sleep is needed.
        await expect.poll(() => responses.slice(responseCursor).filter(item => matches(item.response)).length,
          { timeout: remaining() }).toBeGreaterThanOrEqual(2);
      }
      for (;;) {
        await expect.poll(() => responses.slice(responseCursor).some(item => matches(item.response)),
          { timeout: remaining(), message: `${kind} diagnostics produces a recorded response` }).toBe(true);
        const responseIndex = responses.findIndex((item, index) => index >= responseCursor && matches(item.response));
        const { response, receivedAt } = responses[responseIndex];
        responseCursor = responseIndex + 1;
        if (response.status() !== 503) {
          expect(response.status()).toBe(200);
          return receivedAt;
        }
        expect(response.headers()["retry-after"]).toBe("1");
        const body = await response.json();
        if (body.detail === "Waiting for foreground activity") {
          expect(body).toEqual({ detail: "Waiting for foreground activity" });
          // Observe current loading state, or a retry already in the journal.
          // Do not demand an obsolete loading state after the retry completes.
          await expect.poll(async () => responses.slice(responseCursor).some(item => matches(item.response))
            || (await page.getByRole("status").isVisible()
              && await page.getByRole("alert").count() === 0 && await retryButton.isDisabled()),
          { timeout: remaining() }).toBe(true);
        } else {
          expect(["Waiting for a database section", "Prefix difficulty is temporarily unavailable. Retry after study work settles."]).toContain(body.detail);
          expect(body).toEqual({ detail: body.detail });
          await expect(page.getByRole("alert")).toContainText(body.detail, { timeout: remaining() });
          await expect(page.getByText("Unknown", { exact: true })).toHaveCount(0);
          await expect(retryButton).toBeEnabled({ timeout: remaining() });
          // A database refusal cannot silently schedule another read.
          expect(responses.slice(responseCursor).filter(item => matches(item.response))).toHaveLength(0);
          await retryButton.click({ timeout: remaining() });
        }
      }
    },
  };
}

const scenarios: { width: number; forcedDatabaseRefusals: number; forcedForegroundDeferrals: number;
  title: string; deferralPhase?: "list" | "detail"; replayBufferedResponses?: boolean }[] = [
  ...[390, 1280].map(width => ({ width, forcedDatabaseRefusals: 0, forcedForegroundDeferrals: 0,
    title: `PD-82 PostgreSQL prefix diagnostics stay read-only and show unknown evidence ${width}` })),
  { width: 1280, forcedDatabaseRefusals: 2, forcedForegroundDeferrals: 0,
    title: "PD-82 repeated database refusals require explicit read-only inspection retries" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 1,
    title: "PD-82 foreground deferral followed by database refusal still requires explicit retry" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 6,
    title: "PD-240 repeated foreground detail deferrals outlast five seconds then require explicit database retry" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 6, deferralPhase: "list",
    title: "PD-240 repeated foreground list deferrals outlast five seconds then require explicit refresh" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 1, replayBufferedResponses: true,
    title: "PD-240 diagnostics consumes automatic retry responses recorded before UI assertions" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 1, deferralPhase: "list", replayBufferedResponses: true,
    title: "PD-240 list consumes automatic retry responses recorded before UI assertions" },
];
for (const { width, forcedDatabaseRefusals, forcedForegroundDeferrals, title, deferralPhase = "detail", replayBufferedResponses = false } of scenarios) test(title, async ({ page, request }) => {
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
  const journal = observeDiagnostics(page, repertoireId);
  try {
    const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `diagnostics-${width}`, exact: true }) });
    await expect(card).toBeVisible();
    expect(diagnosticReads).toHaveLength(0);
    let injectedDatabaseRefusals = 0;
    let injectedForegroundDeferrals = 0;
    if (forcedDatabaseRefusals) await page.route("**/prefix-diagnostics**", async route => {
      const isDetail = new URL(route.request().url()).pathname.includes("/prefix-diagnostics/");
      if (isDetail !== (deferralPhase === "detail")) return route.continue();
      if (injectedForegroundDeferrals < forcedForegroundDeferrals) {
        injectedForegroundDeferrals += 1;
        await route.fulfill({ status: 503, headers: { "Retry-After": "1" },
          contentType: "application/json", body: JSON.stringify({ detail: "Waiting for foreground activity" }) });
      } else if (injectedDatabaseRefusals < forcedDatabaseRefusals) {
        injectedDatabaseRefusals += 1;
        await route.fulfill({ status: 503, headers: { "Retry-After": "1" },
          contentType: "application/json", body: JSON.stringify({ detail: "Waiting for a database section" }) });
      } else await route.continue();
    });
    await card.locator("details.card-menu summary").click();
    const listingReceivedAt = await journal.read("list", () =>
      card.getByRole("menuitem", { name: "Prefix difficulty", exact: true }).click(),
      replayBufferedResponses && deferralPhase === "list");
    const inspectButton = page.getByRole("button", { name: /^Inspect prefix/ });
    const remainingRenderMilliseconds = (receivedAt: number) => {
      const milliseconds = receivedAt + 5_000 - Date.now();
      expect(milliseconds, "Admitted diagnostics evidence renders within five seconds").toBeGreaterThan(0);
      return milliseconds;
    };
    await expect(inspectButton).toBeVisible({ timeout: remainingRenderMilliseconds(listingReceivedAt) });
    await expect(inspectButton).toBeEnabled({ timeout: remainingRenderMilliseconds(listingReceivedAt) });
    const detailReceivedAt = await journal.read("detail", () => inspectButton.click(),
      replayBufferedResponses && deferralPhase === "detail");
    if (forcedDatabaseRefusals) expect(injectedDatabaseRefusals).toBe(forcedDatabaseRefusals);
    expect(injectedForegroundDeferrals).toBe(forcedForegroundDeferrals);
    await expect(page.getByText("Unknown", { exact: true })).toHaveCount(3, { timeout: remainingRenderMilliseconds(detailReceivedAt) });
    await expect(page.getByText("No observations for this decision.", { exact: true })).toHaveCount(3);
    await expect(page.getByText(/Latest 0 of up to 100 attempts/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`prefix-diagnostics-${width}.png`), fullPage: true });
    expect(await page.getByRole("button", { name: /apply|shorten|recommend/i }).count()).toBe(0);
    const after = await (await request.get(`${api}/queue/today`)).json();
    expect(after.cards).toEqual(settledQueueCards);
    await page.getByRole("button", { name: "← Repertoires" }).click();
    await expect(card).toBeVisible();
  } finally { journal.dispose(); }
});
