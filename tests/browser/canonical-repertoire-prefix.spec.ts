import type { APIRequestContext, APIResponse } from "@playwright/test";
import { test, expect, api, nav } from "./product-fixtures";

async function confirm(request: APIRequestContext, response: APIResponse) {
  const body = await response.json();
  if (response.status() !== 202 || !body.operation_id) {
    expect(response.ok(), JSON.stringify(body)).toBeTruthy();
    return body;
  }
  let receipt: { state: string; response?: unknown; error?: unknown };
  await expect.poll(async () => {
    receipt = await (await request.get(`${api}/operations/${body.operation_id}`)).json();
    return receipt.state;
  }, { timeout: 30_000 }).toBe("complete");
  return receipt!.response;
}

for (const width of [390, 1280]) {
  test(`canonical Italian prefix persists without changing training and rejects Philidor additions ${width}`, async ({ page, request }, testInfo) => {
    const imported = await confirm(request, await request.post(`${api}/imports/pgn`, { multipart: {
      file: { name: `canonical-italian-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
        '[Event "Italian"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 *') },
      trained_color: "white", initial_depth: "4",
    } }));
    const repertoireId = imported.repertoire_id;
    await expect.poll(async () => (await (await request.get(`${api}/repertoires`)).json()).repertoires
      .find((repertoire: { id: string }) => repertoire.id === repertoireId)?.graph_state,
    { timeout: 30_000 }).toBe("ready");
    const linesBefore = await (await request.get(`${api}/repertoire/lines`)).json();
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/"); await nav(page, "Repertoire");
    const repertoireCard = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `canonical-italian-${width}`, exact: true }) });
    await repertoireCard.locator("details.card-menu summary").click();
    await repertoireCard.getByRole("menuitem", { name: "Canonical prefix…" }).click();
    const dialog = page.getByRole("dialog", { name: "Canonical prefix" });
    await expect(dialog.getByText("Compatible. Saving will remove the opening restriction.")).toBeVisible({ timeout: 30_000 });
    await expect(dialog.getByRole("button", { name: "Use shared opening" })).toBeVisible();
    await dialog.getByLabel("Assumed SAN moves").fill("e4 e5 Nf3 Nc6 Bc4");
    await dialog.getByRole("button", { name: "Check prefix" }).click();
    await expect(dialog.getByText("Compatible. Discoveries start after this opening.")).toBeVisible({ timeout: 30_000 });
    const scansBeforeRepeat = (await (await request.get(`${api}/system/tasks`)).json()).tasks
      .filter((task: { kind: string }) => task.kind === "canonical_prefix_preview").map((task: { id: string }) => task.id).sort();
    await dialog.getByRole("button", { name: "Check prefix" }).click();
    await expect(dialog.getByText("Compatible. Discoveries start after this opening.")).toBeVisible({ timeout: 30_000 });
    const scansAfterRepeat = (await (await request.get(`${api}/system/tasks`)).json()).tasks
      .filter((task: { kind: string }) => task.kind === "canonical_prefix_preview").map((task: { id: string }) => task.id).sort();
    expect(scansAfterRepeat).toEqual(scansBeforeRepeat);
    await expect(dialog.locator(".board-frame")).toHaveAttribute("data-fen", /2B1P3\/5N2/);
    await page.screenshot({ path: testInfo.outputPath("canonical-prefix.png") });
    await dialog.getByRole("button", { name: "Save prefix" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(repertoireCard.getByText("Discoveries start after this opening.")).toBeVisible();
    const saved = await (await request.get(`${api}/repertoires/${repertoireId}/canonical-prefix`)).json();
    expect(saved.moves_uci).toEqual(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]);
    expect(saved.revision).toBe(1);
    expect(await (await request.get(`${api}/repertoire/lines`)).json()).toEqual(linesBefore);
    const rejected = await request.post(`${api}/repertoire/branches`, { data: {
      repertoire_id: repertoireId, starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      trained_color: "white", name: "Philidor", moves: ["e2e4", "e7e5", "g1f3", "d7d6", "f1c4"],
    } });
    expect(rejected.status()).toBe(409);
    expect((await rejected.json()).detail).toContain("canonical prefix");
    await page.reload(); await nav(page, "Repertoire");
    await expect(repertoireCard.getByText("Discoveries start after this opening.")).toBeVisible();
    await repertoireCard.locator("details.card-menu summary").click();
    await repertoireCard.getByRole("menuitem", { name: "Canonical prefix…" }).click();
    await expect(dialog.getByLabel("Assumed SAN moves")).toHaveValue("1. e4 e5 2. Nf3 Nc6 3. Bc4");
    await expect(dialog.getByText("Compatible. Discoveries start after this opening.")).toBeVisible({ timeout: 30_000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await dialog.getByRole("button", { name: "Clear prefix" }).click();
    await expect(dialog.getByText("Compatible. Saving will remove the opening restriction.")).toBeVisible({ timeout: 30_000 });
    await dialog.getByRole("button", { name: "Save prefix" }).click();
    await expect(dialog).toHaveCount(0);
    expect((await (await request.get(`${api}/repertoires/${repertoireId}/canonical-prefix`)).json()).moves_uci).toEqual([]);
  });
}
