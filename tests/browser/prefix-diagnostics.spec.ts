import { test, expect, api, nav } from "./product-fixtures";

for (const width of [390, 1280]) test(`PD-82 PostgreSQL prefix diagnostics stay read-only and show unknown evidence ${width}`, async ({ page, request }) => {
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: `diagnostics-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Opening"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *') },
    trained_color: "white", initial_depth: "3",
  } });
  expect(imported.ok()).toBeTruthy();
  const { repertoire_id: repertoireId } = await imported.json();
  await expect.poll(async () => (await request.get(`${api}/repertoires/${repertoireId}/prefix-diagnostics`)).status(), { timeout: 30_000 }).toBe(200);
  const before = await (await request.get(`${api}/queue/today`)).json();
  const diagnosticReads: string[] = [];
  page.on("request", resource => {
    if (resource.url().includes("/prefix-diagnostics")) {
      diagnosticReads.push(resource.url());
      expect(resource.method()).toBe("GET");
      expect(resource.headers()["x-tempo-work-class"]).toBe("background");
    }
  });
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/"); await nav(page, "Repertoire");
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `diagnostics-${width}`, exact: true }) });
  await expect(card).toBeVisible();
  expect(diagnosticReads).toHaveLength(0);
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Prefix difficulty", exact: true }).click();
  await page.getByRole("button", { name: /^Inspect prefix/ }).click();
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
