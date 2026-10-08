import type { APIRequestContext, APIResponse } from "@playwright/test";
import { test, expect, api, nav, move } from "./product-fixtures";
import { prepareVisualUI } from "./visual-fixtures";

async function confirmed(response: APIResponse, request: APIRequestContext) {
  expect(response.ok(), await response.text()).toBe(true);
  const body = await response.json();
  if (response.status() !== 202) return body;
  let receipt: { state?: string; response?: unknown } = {};
  await expect.poll(async () => {
    receipt = await (await request.get(`${api}/operations/${body.operation_id}`)).json();
    return receipt.state;
  }, { timeout: 20_000 }).toBe("complete");
  return receipt.response;
}

for (const policy of ["keep", "delete"] as const) {
  test(`repertoire confirmation ${policy} removes the source and applies its learned-card policy`, async ({ page, request }) => {
    const pgn = policy === "keep" ? '[Event "Retention proof"]\n\n1. a4 h6 2. a5 h5 3. b4 a6 *' : '[Event "Removal proof"]\n\n1. g4 a6 2. g5 a5 3. h4 b6 *';
    const imported = await confirmed(await request.post(`${api}/imports/pgn`, { multipart: {
      file: { name: `delete-${policy}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(pgn) }, trained_color: "white", initial_depth: "2",
    } }), request) as { repertoire_id: string };
    await expect.poll(async () => (await (await request.get(`${api}/queue/today`)).json()).cards.filter((card: { repertoire_id: string }) => card.repertoire_id === imported.repertoire_id).length, { timeout: 20_000 }).toBeGreaterThan(0);
    const before = (await (await request.get(`${api}/queue/today`)).json()).cards as { id: string; revision?: number; repertoire_id: string }[];
    const targetCards = before.filter(card => card.repertoire_id === imported.repertoire_id);
    await page.goto("/"); await nav(page, "Repertoire");
    const target = page.locator(".repertoire-card").filter({ hasText: `delete-${policy}` });
    await target.locator("summary").click();
    await target.getByRole("menuitem", { name: "Delete", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: /Delete/ });
    await expect(dialog.getByRole("radio", { name: "Keep learned cards" })).toBeChecked();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(target).toBeVisible();
    await target.locator("summary").click();
    await target.getByRole("menuitem", { name: "Delete", exact: true }).click();
    if (policy === "delete") await dialog.getByRole("radio", { name: "Delete learned cards" }).check();
    await dialog.getByRole("button", { name: "Delete repertoire", exact: true }).click();
    await expect.poll(async () => {
      const retry = dialog.getByRole("button", { name: "Check deletion" });
      if (await retry.isVisible() && await retry.isEnabled()) await retry.click();
      return (await (await request.get(`${api}/repertoires`)).json()).repertoires.some((item: { id: string }) => item.id === imported.repertoire_id);
    }, { timeout: 20_000 }).toBe(false);
    await page.reload(); await nav(page, "Repertoire");
    await expect(target).toHaveCount(0);
    const after = (await (await request.get(`${api}/queue/today`)).json()).cards as { id: string; repertoire_id: string }[];
    for (const card of targetCards) {
      expect(after.some(item => item.id === card.id)).toBe(policy === "keep");
      if (policy === "keep") {
        expect(after.find(item => item.id === card.id)?.repertoire_id).toBe("__retained_cards__");
        const preview = await (await request.get(`${api}/cards/${card.id}/deletion-preview`)).json();
        await confirmed(await request.delete(`${api}/cards/${card.id}?permanent=true&expected_revision=${preview.revision}`), request);
      }
    }
  });
}

test("permanently deleting the active training card clears its attempt and survives reload", async ({ page, request }) => {
  const pgn = '[Event "Individual deletion proof"]\n\n1. b3 h6 2. Bb2 a6 3. e3 *';
  const imported = await confirmed(await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "individual-delete.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from(pgn) }, trained_color: "white", initial_depth: "2",
  } }), request) as { repertoire_id: string };
  await expect.poll(async () => (await (await request.get(`${api}/queue/today`)).json()).cards.length, { timeout: 20_000 }).toBeGreaterThan(0);
  const nextPgn = '[Event "Next proof"]\n\n1. h3 a6 2. h4 b6 3. g3 *';
  const nextImported = await confirmed(await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "next-delete.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from(nextPgn) }, trained_color: "white", initial_depth: "2",
  } }), request) as { repertoire_id: string };
  try {
    await expect.poll(async () => (await (await request.get(`${api}/queue/today`)).json()).cards.length, { timeout: 20_000 }).toBeGreaterThan(1);
  } catch (failure) {
    console.log("Deletion fixture repertoire", await (await request.get(`${api}/repertoires`)).json());
    console.log("Deletion fixture integrity", await (await request.get(`${api}/repertoires/${imported.repertoire_id}/integrity`)).json());
    throw failure;
  }
  await page.goto("/"); await nav(page, "Train");
  await page.getByRole("button", { name: /Edit card/ }).click();
  const previewResponse = page.waitForResponse(response => response.url().endsWith("/deletion-preview") && response.ok());
  await page.getByRole("button", { name: "Delete card", exact: true }).click();
  const preview = await (await previewResponse).json();
  const deletedId = preview.card_id;
  const deletesFirstImport = preview.repertoires.some((item: { id: string }) => item.id === imported.repertoire_id);
  await expect(page.getByText(`Affected repertoires: ${deletesFirstImport ? "individual-delete" : "next-delete"}.`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Permanently delete card", exact: true }).click();
  await expect.poll(async () => {
    const retry = page.getByRole("dialog").getByRole("button", { name: "Check deletion", exact: true });
    if (await retry.isVisible() && await retry.isEnabled()) await retry.click();
    return (await (await request.get(`${api}/queue/today`)).json()).cards.some((card: { id: string }) => card.id === deletedId);
  }, { timeout: 20_000 }).toBe(false);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Edit card/ })).toBeVisible();
  await page.reload(); await nav(page, "Train");
  expect((await (await request.get(`${api}/queue/today`)).json()).cards.some((card: { id: string }) => card.id === deletedId)).toBe(false);
  expect((await request.post(`${api}/cards/${deletedId}/review`, { data: { outcome: "correct", expected_revision: 1 } })).status()).toBe(409);
  const reimported = await confirmed(await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: deletesFirstImport ? "individual-delete.pgn" : "next-delete.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from(deletesFirstImport ? pgn : nextPgn) }, trained_color: "white", initial_depth: "2",
  } }), request);
  expect(reimported.cards_created).toBe(0);
  await expect.poll(async () => {
    const repertoires = (await (await request.get(`${api}/repertoires`)).json()).repertoires;
    const target = repertoires.find((item: { id: string }) => item.id === (deletesFirstImport ? imported.repertoire_id : nextImported.repertoire_id));
    return target?.graph_state === "ready" && target?.graph_generation > 1;
  }, { timeout: 20_000 }).toBe(true);
  expect((await request.get(`${api}/cards/${deletedId}/deletion-preview`)).status()).toBe(404);
});

test("pending card deletion recovers after reload and reports a queue refresh failure", async ({ page }) => {
  await prepareVisualUI(page);
  await page.evaluate(() => localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify({ operationId: "recovered-delete", cardId: "visual-card", expectedRevision: 1 })));
  await page.route("**/api/operations/recovered-delete", route => route.fulfill({ json: { state: "complete", response: { deleted: true, card_id: "visual-card" } } }));
  let refreshRequests = 0;
  await page.route("**/api/queue/window?**", route => {
    refreshRequests += 1;
    return refreshRequests > 1 ? route.fulfill({ status: 503, json: { detail: "Queue unavailable after deletion" } }) : route.fallback();
  });
  await page.reload();
  await expect(page.getByText("Card deleted. Training queue refresh failed; retry loading the queue.", { exact: true }).first()).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-card-delete-v1"))).toBeNull();
});

test("pending repertoire deletion recovers its chosen policy and reports failure without false success", async ({ page }) => {
  await prepareVisualUI(page);
  await page.evaluate(() => localStorage.setItem("tempo-pending-repertoire-delete-v1", JSON.stringify({
    operationId: "recovered-repertoire-delete", repertoireId: "visual-repertoire", learnedCards: "delete",
  })));
  let failed = false;
  await page.route("**/api/operations/recovered-repertoire-delete", route => route.fulfill({ json: failed
    ? { state: "failed", error: { message: "Deletion could not be committed" } }
    : { state: "pending" },
  }));
  await page.reload(); await nav(page, "Repertoire");
  const dialog = page.getByRole("dialog", { name: /Delete/ });
  await expect(dialog.getByRole("radio", { name: "Delete learned cards" })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: "Keep learned cards" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Check deletion", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("still pending");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.reload(); await nav(page, "Repertoire");
  await expect(dialog.getByRole("radio", { name: "Delete learned cards" })).toBeChecked();
  failed = true;
  await dialog.getByRole("button", { name: "Check deletion", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Deletion could not be committed");
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-repertoire-delete-v1"))).toBeNull();
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
});

test("recovering an older deletion preserves a different active card's board and attempt", async ({ page }) => {
  await prepareVisualUI(page);
  await page.evaluate(() => localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify({
    operationId: "older-delete", cardId: "already-deleted-card", expectedRevision: 1,
  })));
  let complete = false;
  await page.route("**/api/operations/older-delete", route => route.fulfill({ json: complete
    ? { state: "complete", response: { deleted: true, card_id: "already-deleted-card" } }
    : { state: "pending" },
  }));
  await page.reload();
  await expect(page.getByRole("button", { name: "Check card deletion", exact: true })).toBeVisible();
  await move(page, "e2", "e4");
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-fen", "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2");
  const before = await board.getAttribute("data-fen");
  complete = true;
  await page.getByRole("button", { name: "Check card deletion", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check card deletion", exact: true })).toHaveCount(0);
  await expect(board).toHaveAttribute("data-fen", before!);
  await expect(page.getByText("Spanish opening", { exact: true }).first()).toBeVisible();
});
