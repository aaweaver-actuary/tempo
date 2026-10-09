import { test, expect, api, nav } from "./product-fixtures";

for (const width of [390, 1280]) test(`PD-82 PostgreSQL prefix diagnostics stay read-only and show unknown evidence ${width}`, async ({ page, request }) => {
  const settings = await (await request.get(`${api}/settings`)).json();
  await request.put(`${api}/settings`, { data: { ...settings, initial_depth: 3 } });
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
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Insights"); await nav(page, "Repertoire");
  await expect.poll(async () => (await request.get(`${api}/repertoires/${repertoireId}/prefix-diagnostics`)).status(), { timeout: 30_000 }).toBe(200);
  await expect.poll(async () => {
    const queue = await (await request.get(`${api}/queue/today`)).json();
    return queue.cards.some((card: { repertoire_id: string }) => card.repertoire_id === repertoireId);
  }, { timeout: 30_000 }).toBe(true);
  const before = await (await request.get(`${api}/queue/today`)).json();
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
  const [initialDetailResponse] = await Promise.all([
    page.waitForResponse(response => response.url().includes("/prefix-diagnostics/") && response.request().method() === "GET", { timeout: 5_000 }),
    inspectButton.click(),
  ]);
  if (initialDetailResponse.status() === 503) {
    // A bounded diagnostic may yield; preserve the real error and retry once
    // through the same user action. Other errors and persistent failures fail.
    expect(initialDetailResponse.headers()["retry-after"]).toBe("1");
    const temporaryMessage = "Prefix difficulty is temporarily unavailable. Retry after study work settles.";
    expect(await initialDetailResponse.json()).toEqual({ detail: temporaryMessage });
    await expect(page.getByRole("alert")).toContainText(temporaryMessage);
    await expect(page.getByText("Unknown", { exact: true })).toHaveCount(0);
    await inspectButton.click();
  } else {
    expect(initialDetailResponse.status()).toBe(200);
  }
  await expect(page.getByText("Unknown", { exact: true })).toHaveCount(3);
  await expect(page.getByText("No observations for this decision.", { exact: true })).toHaveCount(3);
  await expect(page.getByText(/Latest 0 of up to 100 attempts/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: test.info().outputPath(`prefix-diagnostics-${width}.png`), fullPage: true });
  expect(await page.getByRole("button", { name: /apply|shorten|recommend/i }).count()).toBe(0);
  const after = await (await request.get(`${api}/queue/today`)).json();
  expect(after.cards).toEqual(before.cards);
  await page.getByRole("button", { name: "← Repertoires" }).click();
  await expect(card).toBeVisible();
});
