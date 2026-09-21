import {
  test,
  expect,
  Chess,
  api,
  blackPgn,
  nav,
  boardVisible,
  move,
  clickSquare,
} from "./product-fixtures";
test("local import respects the daily limit; Black prompts and Builder flip survive Settings and refresh", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "mine.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(blackPgn),
  });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Black", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Train");
  await expect(page.locator(".session-count strong")).toHaveText("2");
  await expect
    .poll(async () =>
      new Chess(
        (await page.locator(".board-frame").getAttribute("data-fen"))!,
      ).turn(),
    )
    .toBe("b");
  await expect(page.locator(".board-frame")).toHaveAttribute(
    "data-input-enabled",
    "true",
  );
  await boardVisible(page);
  await nav(page, "Builder");
  const selector = page.getByRole("combobox", { name: "Active repertoire" });
  await expect(selector).not.toHaveValue("");
  const selected = await selector.inputValue();
  await move(page, "e2", "e4");
  await expect
    .poll(
      async () =>
        new Chess(
          (await page.locator(".board-frame").getAttribute("data-fen"))!,
        ).get("e4")?.type,
    )
    .toBe("p");
  const fen = await page.locator(".board-frame").getAttribute("data-fen");
  await page.keyboard.press("f");
  await expect(selector).toHaveValue(selected);
  await nav(page, "Settings");
  await nav(page, "Builder");
  await expect(selector).toHaveValue(selected);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", fen!);
  await page.reload();
  await nav(page, "Builder");
  await expect(selector).toHaveValue(selected);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", fen!);
  await page.setViewportSize({ width: 390, height: 844 });
  await boardVisible(page);
});

test("Black Train prompt remains playable with a fully visible narrow board", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "black.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(blackPgn),
  });
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Black", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Train");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(async () =>
      new Chess(
        (await page.locator(".board-frame").getAttribute("data-fen"))!,
      ).turn(),
    )
    .toBe("b");
  await expect(page.locator(".board-frame")).toHaveAttribute(
    "data-input-enabled",
    "true",
  );
  await boardVisible(page);
  const box = await page.locator(".board-frame").boundingBox();
  expect(box!.width).toBeGreaterThan(200);
  await move(page, "e7", "e5");
  await expect
    .poll(
      async () =>
        new Chess(
          (await page.locator(".board-frame").getAttribute("data-fen"))!,
        ).get("e5")?.type,
    )
    .toBe("p");
});

test("Builder source comparison is immediately reachable beside the board", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("tempo-stockfish-on", "true"),
  );
  await page.goto("/");
  await nav(page, "Builder");
  const comparison = page.getByRole("table", {
    name: "Move source comparison",
  });
  await expect(comparison).toBeVisible();
  for (const name of ["Stockfish", "Maia", "Lichess", "Masters"])
    await expect(
      comparison.getByRole("columnheader", { name, exact: true }),
    ).toBeVisible();
  await expect(comparison.locator("tbody tr button").first()).toBeVisible({
    timeout: 45_000,
  });
  const header = comparison.getByRole("columnheader", {
    name: "Stockfish",
    exact: true,
  });
  await header.getByRole("button").focus();
  await page.keyboard.press("Enter");
  await expect(header).toHaveAttribute("aria-sort", "ascending");
  await page.keyboard.press("Enter");
  await expect(header).toHaveAttribute("aria-sort", "descending");
  await expect(
    page
      .getByRole("navigation", { name: "Primary navigation" })
      .getByRole("button", { name: "Builder", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "Builder", exact: true })).toBeVisible();
  await boardVisible(page);
});

test("Builder right-click annotation saves the exact clicked square", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "annotation.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from('[Event "QGD"]\n\n1. d4 d5 2. c4 e6 *'),
  });
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Builder");
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Active repertoire" }),
  ).not.toHaveValue("");

  const fen = await page
    .locator(".board-frame")
    .first()
    .getAttribute("data-fen");
  const repertoireId = (await (await request.get(`${api}/repertoires`)).json())
    .repertoires[0].id;

  await page.getByRole("textbox", { name: "Position comment" }).fill("probe");
  await page.getByRole("button", { name: "Save note" }).click();
  await expect
    .poll(async () => {
      const body = await (
        await request.get(
          `${api}/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen!)}`,
        )
      ).json();
      return body.annotations?.[0]?.comment ?? "";
    })
    .toBe("probe");

  await clickSquare(page, "a4", "right");
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await page.getByRole("button", { name: "Save note" }).click();

  await expect
    .poll(async () => {
      const body = await (
        await request.get(
          `${api}/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen!)}`,
        )
      ).json();
      const note = body.annotations?.[0];
      return (
        note?.squares?.map((item: { square: string }) => item.square) ?? []
      );
    })
    .toContain("a4");
  await expect
    .poll(async () => {
      const body = await (
        await request.get(
          `${api}/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen!)}`,
        )
      ).json();
      const note = body.annotations?.[0];
      return (
        note?.squares?.map((item: { square: string }) => item.square) ?? []
      );
    })
    .not.toContain("a3");
});
