import { test, expect, api, nav } from "./product-fixtures";

for (const { width, forcedDatabaseRefusals, forcedForegroundDeferrals, title } of [
  ...[390, 1280].map(width => ({ width, forcedDatabaseRefusals: 0, forcedForegroundDeferrals: 0,
    title: `PD-82 PostgreSQL prefix diagnostics stay read-only and show unknown evidence ${width}` })),
  { width: 1280, forcedDatabaseRefusals: 2, forcedForegroundDeferrals: 0,
    title: "PD-82 repeated database refusals require explicit read-only inspection retries" },
  { width: 1280, forcedDatabaseRefusals: 1, forcedForegroundDeferrals: 1,
    title: "PD-82 foreground deferral followed by database refusal still requires explicit retry" },
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
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Prefix difficulty", exact: true }).click();
  const inspectButton = page.getByRole("button", { name: /^Inspect prefix/ });
  await expect(inspectButton).toBeVisible();
  await expect(inspectButton).toBeEnabled();
  let injectedDatabaseRefusals = 0;
  let injectedForegroundDeferrals = 0;
  if (forcedDatabaseRefusals) await page.route("**/prefix-diagnostics/*", async route => {
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
  const [initialDetailResponse] = await Promise.all([
    page.waitForResponse(response => response.url().includes("/prefix-diagnostics/") && response.request().method() === "GET", { timeout: 5_000 }),
    inspectButton.click(),
  ]);
  // Repeated database refusals still require explicit user retries. Share the
  // existing five-second evidence budget rather than extending any deadline.
  const inspectionDeadline = Date.now() + 5_000;
  const remainingInspectionMilliseconds = () => {
    const remaining = inspectionDeadline - Date.now();
    expect(remaining, "Prefix inspection succeeds within its existing five-second budget").toBeGreaterThan(0);
    return remaining;
  };
  let detailResponse = initialDetailResponse;
  while (detailResponse.status() === 503) {
    expect(detailResponse.headers()["retry-after"]).toBe("1");
    const body = await detailResponse.json();
    if (body.detail === "Waiting for foreground activity") {
      // The same inspection retries admission automatically after release.
      // A database refusal remains a visible error with explicit user retry.
      expect(body).toEqual({ detail: "Waiting for foreground activity" });
      await expect(page.getByRole("status")).toContainText("Loading evidence");
      await expect(page.getByRole("alert")).toHaveCount(0);
      // Continue observing the automatic foreground retry: its next response
      // may be a database refusal that requires an explicit click.
      detailResponse = await page.waitForResponse(response => response.url() === initialDetailResponse.url()
        && response.request().method() === "GET", { timeout: remainingInspectionMilliseconds() });
    } else {
      const temporaryMessage = "Prefix difficulty is temporarily unavailable. Retry after study work settles.";
      expect([temporaryMessage, "Waiting for a database section"]).toContain(body.detail);
      expect(body).toEqual({ detail: body.detail });
      await expect(page.getByRole("alert")).toContainText(body.detail, { timeout: remainingInspectionMilliseconds() });
      await expect(page.getByText("Unknown", { exact: true })).toHaveCount(0);
      [detailResponse] = await Promise.all([
        page.waitForResponse(response => response.url() === initialDetailResponse.url()
          && response.request().method() === "GET", { timeout: remainingInspectionMilliseconds() }),
        inspectButton.click({ timeout: remainingInspectionMilliseconds() }),
      ]);
    }
  }
  if (detailResponse.status() !== 503) expect(detailResponse.status()).toBe(200);
  if (forcedDatabaseRefusals) expect(injectedDatabaseRefusals).toBe(forcedDatabaseRefusals);
  expect(injectedForegroundDeferrals).toBe(forcedForegroundDeferrals);
  await expect(page.getByText("Unknown", { exact: true })).toHaveCount(3, { timeout: remainingInspectionMilliseconds() });
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
