import { test, expect, api, nav } from "./product-fixtures";

test("paste SAN from a repertoire gap previews its destination and saves the continuation", async ({ page, request }) => {
  const imported = await request.post(`${api}/imports/pgn`, {
    multipart: {
      file: {
        name: "paste-gap-workflow.pgn",
        mimeType: "application/x-chess-pgn",
        buffer: Buffer.from('[Event "Paste gap"]\n\n1. e4 e5 2. Nf3 *'),
      },
      trained_color: "white",
      initial_depth: "2",
    },
  });
  expect(imported.ok()).toBeTruthy();
  const repertoireId = (await imported.json()).repertoire_id as string;
  const competingImport = await request.post(`${api}/imports/pgn`, {
    multipart: {
      file: { name: "another-e4-route.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Another e4 route"]\n\n1. e4 e5 2. Nc3 *') },
      trained_color: "white",
      initial_depth: "2",
    },
  });
  expect(competingImport.ok()).toBeTruthy();
  const gapFen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
  let coverageReads = 0;
  await page.route(`**/api/repertoires/${repertoireId}/coverage`, (route) => { coverageReads += 1; return route.fulfill({ json: {
    run_id: null, status: "complete", required_branches: 1, covered_branches: 0,
    probability_coverage: 0, is_complete: false, unknown_nodes: 0, last_error: null,
  } }); });
  await page.route(`**/api/repertoires/${repertoireId}/coverage/gaps`, (route) => route.fulfill({ json: {
    gaps: [{ gap_id: "browser-gap:c7c5", node_id: "browser-gap", fen: gapFen,
      fen_key: gapFen.split(" ").slice(0, 4).join(" "), move_uci: "c7c5",
      probability: null, source_state: "unknown", explorer_games: 0, trained_color: "white" }],
  } }));
  await page.goto("/");
  await nav(page, "Repertoire");
  const repertoire = page.locator(".repertoire-card").filter({ hasText: "paste-gap-workflow" });
  await expect(repertoire).toBeVisible();
  await repertoire.getByRole("button", { name: "Check coverage" }).click();
  await repertoire.getByRole("button", { name: "Paste line for gap" }).click();
  const dialog = page.getByRole("dialog", { name: "Paste analysis" });
  await dialog.getByRole("textbox", { name: "SAN or PGN" }).fill("1... c5 2. Nf3");
  await dialog.getByRole("button", { name: "Preview lines" }).click();
  await expect(dialog.getByLabel("Pasted line preview")).toContainText("c5 Nf3");
  const destination = dialog.getByRole("combobox", { name: "Destination for line 1" });
  await expect(destination).toHaveValue("");
  await destination.selectOption(repertoireId);
  await dialog.getByRole("button", { name: "Save selected lines" }).click();
  await expect(dialog).not.toBeVisible();
  await expect.poll(async () => {
    const lines = (await (await request.get(`${api}/repertoire/lines`)).json()).lines as { repertoire_id: string; start_fen: string; moves: string[] }[];
    return lines.some((line) => line.repertoire_id === repertoireId && line.start_fen === gapFen && line.moves.join(" ") === "c7c5 g1f3");
  }).toBe(true);
  await expect.poll(() => coverageReads).toBeGreaterThan(1);
});

test("paste analysis reports a preview service failure without claiming a save", async ({ page }) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "Paste analysis", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Paste analysis" });
  await dialog.getByRole("textbox", { name: "SAN or PGN" }).fill("1. e4 e5 2. Nf3");
  await page.route("**/api/repertoire/paste/preview", (route) => route.fulfill({
    status: 503, json: { detail: "Analysis service unavailable" },
  }));
  await dialog.getByRole("button", { name: "Preview lines" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Analysis service unavailable");
  await expect(dialog.getByRole("button", { name: "Save selected lines" })).toBeDisabled();
});
