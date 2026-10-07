import { test, expect, api, nav } from "./product-fixtures";
import { prefixSourceSchema, prefixComparisonSchema } from "../../app/domain/prefix-comparison";

for (const width of [390, 1280]) test(`issue78_phone_and_desktop_comparison_remain_usable ${width}`, async ({ page, request }, testInfo) => {
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: `prefix-comparison-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
      '[Event "Caro advance"]\n\n1. e4 c6 2. d4 d5 3. e5 c5 *\n\n[Event "Caro knight"]\n\n1. e4 c6 2. d4 d5 3. Nf3 Bg4 *\n\n[Event "QGD"]\n\n1. d4 d5 2. c4 e6 3. Nf3 Nf6 *\n\n[Event "Transposed incoming"]\n\n1. d4 d5 2. e4 c6 3. e5 c5 *') },
    trained_color: "black", initial_depth: "3",
  } });
  expect(imported.ok()).toBeTruthy();
  const { repertoire_id: repertoireId } = await imported.json();
  await expect.poll(async () => {
    const response = await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`);
    if (response.ok()) return (await response.json()).graph_generation;
    // Import publication is asynchronous; wait for its authoritative graph, not a wall-clock delay.
    expect(["evaluation_busy", "graph_not_ready"]).toContain((await response.json()).detail.code);
    return 0;
  }, { timeout: 30_000 }).toBeGreaterThan(0);
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/"); await nav(page, "Repertoire");
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `prefix-comparison-${width}`, exact: true }) });
  await card.locator("details.card-menu summary").click();
  const sourceRead = page.waitForResponse(response => response.url().endsWith(`/repertoires/${repertoireId}/prefix-evaluation/source`));
  const trigger = card.getByRole("menuitem", { name: "Compare prefix depths", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: /Compare prefix depths/ });
  let sourceResponse = await sourceRead;
  await expect.poll(async () => {
    if (sourceResponse.ok()) return true;
    expect((await sourceResponse.json()).detail.code).toBe("evaluation_busy");
    expect(sourceResponse.headers()["retry-after"]).toBe("1");
    await expect(dialog.getByText("Study work is active", { exact: true })).toBeVisible();
    const retryResponse = page.waitForResponse(response => response.url().endsWith(`/repertoires/${repertoireId}/prefix-evaluation/source`));
    await dialog.getByRole("button", { name: "Refresh source", exact: true }).click();
    sourceResponse = await retryResponse;
    return sourceResponse.ok();
  }, { timeout: 15_000, intervals: [1000] }).toBe(true);
  const source = prefixSourceSchema.parse(await sourceResponse.json());
  await expect(dialog.getByText("Selected source lines: 0")).toBeVisible();
  const observedRequests: string[] = [];
  page.on("request", incoming => { if (incoming.url().includes("/api/") && incoming.method() !== "GET" && !incoming.url().endsWith("/system/browser-activity")) observedRequests.push(incoming.method() + " " + incoming.url()); });
  const startingPosition = dialog.getByLabel("Starting position and trained color");
  await startingPosition.selectOption(JSON.stringify([source.lines[0].start_fen, "black"]));
  await dialog.getByRole("combobox", { name: "Move 1", exact: true }).selectOption("e2e4");
  await dialog.getByRole("combobox", { name: "Move 2", exact: true }).selectOption("c7c6");
  await expect(dialog.getByText("Selected source lines: 2")).toBeVisible();
  const unselected = source.lines.filter(line => line.moves[0] === "d2d4");
  for (const line of unselected) await expect(dialog.getByRole("checkbox", { name: new RegExp(line.id) })).not.toBeChecked();
  const queueBefore = await (await request.get(`${api}/queue/today`)).json();
  await dialog.getByLabel("Candidate learner-decision depths").fill("2, 4");
  const evaluations: ReturnType<typeof prefixComparisonSchema.parse>[] = [];
  page.on("response", async response => {
    if (response.url().endsWith(`/repertoires/${repertoireId}/prefix-evaluation/evaluate`) && response.ok())
      evaluations.push(prefixComparisonSchema.parse(await response.json()));
  });
  await expect.poll(async () => {
    evaluations.length = 0;
    await dialog.getByRole("button", { name: "Compare depths", exact: true }).click();
    await expect.poll(async () => (await dialog.getByRole("region", { name: "Candidate depth 4", exact: true }).count())
      + (await dialog.getByRole("alert").count())).toBe(1);
    if (await dialog.getByRole("alert").count()) {
      await expect(dialog.getByRole("alert")).toContainText("Study work is active");
      return false;
    }
    return true;
  }, { timeout: 15_000, intervals: [1000] }).toBe(true);
  await expect(dialog.getByRole("region", { name: "Candidate depth 4", exact: true })).toBeVisible();
  await expect.poll(() => evaluations.length).toBe(2);
  for (const result of evaluations) {
    expect(result.selected_line_ids).toEqual(source.lines.filter(line => line.moves[0] === "e2e4").map(line => line.id).sort());
    for (const line of unselected) {
      const steps = (kind: "current" | "proposed") => result.whole_repertoire[kind].steps.filter(step => step.line_id === line.id);
      expect(steps("proposed")).toEqual(steps("current"));
    }
  }
  expect(observedRequests.every(incoming => incoming.startsWith("POST ") && incoming.endsWith("/prefix-evaluation/evaluate"))).toBe(true);
  expect((await (await request.get(`${api}/queue/today`)).json()).cards).toEqual(queueBefore.cards);
  expect(prefixSourceSchema.parse(await (await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`)).json())).toEqual(source);
  await expect(dialog.getByRole("button", { name: /apply|save|recommend/i })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await dialog.getByRole("region", { name: "Candidate depth 2", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath(`prefix-comparison-${width}.png`) });
  await testInfo.attach(`prefix-comparison-${width}`, { path: testInfo.outputPath(`prefix-comparison-${width}.png`), contentType: "image/png" });
  const wholeScope = dialog.getByRole("region", { name: "Candidate depth 2", exact: true }).getByRole("region", { name: "Whole repertoire", exact: true });
  await wholeScope.scrollIntoViewIfNeeded();
  await expect(wholeScope.getByRole("row", { name: /Distinct cards/ })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath(`prefix-comparison-whole-${width}.png`) });
  await testInfo.attach(`prefix-comparison-whole-${width}`, { path: testInfo.outputPath(`prefix-comparison-whole-${width}.png`), contentType: "image/png" });
  await dialog.getByLabel("Candidate learner-decision depths").focus();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(card.locator("details.card-menu summary")).toBeFocused();
});
