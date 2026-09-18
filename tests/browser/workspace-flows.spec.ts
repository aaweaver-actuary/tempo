import {
  test,
  expect,
  api,
  pgn,
  nav,
  boardVisible,
  move,
} from "./product-fixtures";
test("sample deletion uses repertoire identity and does not delete its same-filename sibling", async ({
  page,
  request,
}) => {
  for (const color of ["white", "black"])
    await request.post(`${api}/imports/pgn`, {
      multipart: {
        file: {
          name: "Tempo examples.pgn",
          mimeType: "application/x-chess-pgn",
          buffer: Buffer.from(pgn),
        },
        trained_color: color,
        initial_depth: "2",
      },
    });
  await page.goto("/");
  await nav(page, "Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(2);
  page.on("dialog", (dialog) => dialog.accept());
  await page
    .locator(".repertoire-card")
    .first()
    .getByRole("button", { name: "Delete", exact: true })
    .click();
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
  await nav(page, "Builder");
  await nav(page, "Repertoire");
  await page.reload();
  await nav(page, "Repertoire");
  await expect(page.locator(".repertoire-card")).toHaveCount(1);
});

test("Builder exact transposition saves the played route and does not prompt for a covered route", async ({
  page,
  request,
}) => {
  await request.post(`${api}/imports/pgn`, {
    multipart: {
      file: {
        name: "transposition.pgn",
        mimeType: "application/x-chess-pgn",
        buffer: Buffer.from('[Event "Blitz"]\n\n1. d4 d5 2. Nf3 Nf6 *'),
      },
      initial_depth: "2",
    },
  });
  await page.goto("/");
  await nav(page, "Builder");
  await move(page, "g1", "f3");
  await move(page, "d7", "d5");
  await move(page, "d2", "d4");
  await expect(
    page.getByRole("button", { name: "Add as branch" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add as branch" }).click();
  await page.getByRole("button", { name: "Save branch" }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`${api}/repertoire/lines`)).json()).lines
          .length,
    )
    .toBe(2);
  await expect(page.getByRole("button", { name: "Add as branch" })).toHaveCount(
    0,
  );
  await page.reload();
  await nav(page, "Builder");
  await expect(page.getByRole("button", { name: "Add as branch" })).toHaveCount(
    0,
  );
  await boardVisible(page);
});

test("Edit card opens Builder line-removal context and deletes the selected branch", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await nav(page, "Repertoire");
  await page.getByRole("button", { name: "＋ Import PGN" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "branches.pgn",
    mimeType: "application/x-chess-pgn",
    buffer: Buffer.from(
      '[Event "QGD"]\n\n1. d4 d5 2. c4 e6 *\n\n[Event "Nimzo"]\n\n1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 *',
    ),
  });
  await page
    .getByRole("button", { name: "Import repertoire", exact: true })
    .click();
  await page.getByRole("button", { name: "View imported repertoire" }).click();
  await nav(page, "Train");
  await expect(
    page.getByRole("button", { name: /Edit card/i }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Edit card/i })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Open Builder to remove line" })
    .click();
  await expect(
    page
      .getByRole("navigation", { name: "Primary navigation" })
      .getByRole("button", { name: "Builder", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await move(page, "d2", "d4");
  await move(page, "g8", "f6");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete line from here" }).click();
  await expect(page.getByText(/Deleted \d+ line/)).toBeVisible();
  await expect
    .poll(async () => {
      const lines = (
        await (await request.get(`${api}/repertoire/lines`)).json()
      ).lines;
      return lines.some(
        (line: { moves: string[] }) =>
          line.moves?.[0] === "d2d4" && line.moves?.[1] === "g8f6",
      );
    })
    .toBe(false);
});
