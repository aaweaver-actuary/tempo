import { test, expect, api, nav } from "./product-fixtures";

for (const width of [390, 1280]) test(`AS-01/19 real PostgreSQL segmentation preview preserves practice ${width}`, async ({ page, request }) => {
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: `segmentation-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
      '[Event "Open"]\n\n1. e4 e5 2. Nf3 *\n\n[Event "Sicilian"]\n\n1. e4 c5 2. Nf3 *\n\n[Event "French"]\n\n1. e4 e6 2. Nf3 *') },
    trained_color: "white", initial_depth: "2",
  } });
  expect(imported.ok()).toBeTruthy();
  const { repertoire_id: repertoireId } = await imported.json();
  await expect.poll(async () => (await (await request.get(`${api}/repertoires/${repertoireId}/segmentation`)).json()).state,
    { timeout: 30_000 }).toBe("ready");
  const before = await (await request.get(`${api}/queue/today`)).json();
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/"); await nav(page, "Repertoire");
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `segmentation-${width}`, exact: true }) });
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Recommended segmentation" }).click();
  await expect(page.getByText("Preview only", { exact: true })).toBeVisible();
  await expect(page.getByText(/6 → 4 tested decisions/)).toBeVisible();
  await page.getByRole("button", { name: "Preview shared opening" }).click();
  await expect(page.locator(".segmentation-positions .board-frame")).toHaveCount(8);
  expect(await page.getByRole("button", { name: /^Apply/ }).count()).toBe(0);
  await page.getByRole("button", { name: "Keep current presentation" }).click();
  await expect(page.getByText("No new recommendations with sufficient structural savings.")).toBeVisible();
  const after = await (await request.get(`${api}/queue/today`)).json();
  expect(after.cards).toEqual(before.cards);
  await page.reload(); await nav(page, "Repertoire");
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Recommended segmentation" }).click();
  await expect(page.getByText("No new recommendations with sufficient structural savings.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
