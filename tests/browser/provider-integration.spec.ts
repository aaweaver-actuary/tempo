import { test, expect, api, nav, boardVisible } from "./product-fixtures";


test("real packaged tactics and standard chess sounds are readable and preloaded before opening Tactics", async ({
  page,
  request,
}) => {
  const catalog = await request.get(
    "/data/tactics-packs/hangingPiece-easy-01.json",
  );
  expect(catalog.ok()).toBeTruthy();
  const deck = (await catalog.json()).filter(
    (record: { DeckId: string }) => record.DeckId === "hangingPiece-easy-01",
  );
  expect(deck).toHaveLength(25);
  for (const path of ["Move", "Capture"])
    expect(
      (await request.get(`/sounds/standard/${path}.mp3`)).ok(),
    ).toBeTruthy();
  let requests = 0;
  const progress = await (await request.get(`${api}/tactics/progress`)).json();
  const discovered = new Set(
    progress["hangingPiece-easy-01"]?.discoveredIds ??
      progress["hangingPiece-easy-01"]?.cleanIds ??
      [],
  );
  const expectedPuzzle = deck.find(
    (record: { PuzzleId: string }) =>
      !discovered.has(`lichess-${record.PuzzleId}`),
  ).DeckPosition;
  page.on("request", (request) => {
    if (request.url().includes("/data/tactics-packs/hangingPiece-easy-01.json"))
      requests++;
  });
  await page.goto("/");
  await expect.poll(() => requests).toBe(1);
  await nav(page, "Tactics");
  await expect(page.locator(".board-frame")).toBeVisible();
  await expect(page.getByText(`Puzzle ${expectedPuzzle} of 25`)).toBeVisible();
  expect(requests).toBe(1);
  await boardVisible(page);
});
