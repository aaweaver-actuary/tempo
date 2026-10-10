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

test("delayed startup audio never replays earlier board moves and the next move sounds normally", async ({ page }) => {
  let releaseSoundRequests: (() => void) | undefined;
  const soundRequestsReleased = new Promise<void>((resolve) => { releaseSoundRequests = resolve; });
  const requestedSoundPaths = new Set<string>();
  await page.route("**/sounds/standard/*", async (route) => {
    requestedSoundPaths.add(new URL(route.request().url()).pathname);
    await soundRequestsReleased;
    await route.continue();
  });
  await page.addInitScript(() => {
    const sounds: HTMLAudioElement[] = [];
    const events: string[] = [];
    Object.assign(window, { __tempoSounds: sounds, __tempoSoundEvents: events });
    Object.defineProperty(window, "Audio", {
      configurable: true,
      value: function (source: string) {
        const sound = document.createElement("audio");
        sound.src = source;
        sound.addEventListener("playing", () => events.push("playing"));
        const play = sound.play.bind(sound);
        sound.play = () => {
          events.push("play requested");
          return play();
        };
        sounds.push(sound);
        return sound;
      },
    });
  });

  try {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect.poll(() => requestedSoundPaths.size).toBe(3);
    await nav(page, "Builder");
    await move(page, "e2", "e4");
    await move(page, "e7", "e5");
    expect(await page.evaluate(() => (window as unknown as { __tempoSoundEvents: string[] }).__tempoSoundEvents)).toEqual([]);

    releaseSoundRequests?.();
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __tempoSounds: HTMLAudioElement[] }).__tempoSounds.every((sound) => sound.readyState >= 2),
    )).toBe(true);
    await page.waitForTimeout(350);
    expect(await page.evaluate(() => (window as unknown as { __tempoSoundEvents: string[] }).__tempoSoundEvents)).toEqual([]);

    await move(page, "g1", "f3");
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __tempoSoundEvents: string[] }).__tempoSoundEvents.filter((event) => event === "playing").length,
    )).toBe(1);
    expect(await page.evaluate(() => (window as unknown as { __tempoSoundEvents: string[] }).__tempoSoundEvents)).toEqual([
      "play requested", "playing",
    ]);
  } finally {
    releaseSoundRequests?.();
  }
});

test("checked king gets a persistent translucent red cue that clears when the position changes", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const audioSources: string[] = [];
    Object.assign(window, { __tempoAudioSources: audioSources });
    Object.defineProperty(window, "Audio", {
      configurable: true,
      value: class {
        volume = 1;
        currentTime = 0;
        readyState = 2;
        preload = "none";
        constructor(public src: string) {}
        load() {}
        pause() {}
        play() {
          audioSources.push(this.src);
          return Promise.resolve();
        }
      },
    });
  });
  await page.goto("/");
  await nav(page, "Builder");
  const board = page.locator(".board-frame");
  const expectedPosition = new Chess();

  for (const [from, to] of [
    ["e2", "e4"],
    ["e7", "e5"],
    ["d1", "h5"],
    ["b8", "c6"],
    ["f1", "c4"],
    ["g8", "f6"],
    ["h5", "f7"],
  ]) {
    expectedPosition.move({ from, to, promotion: "q" });
    await move(page, from, to);
    await expect(board).toHaveAttribute("data-fen", expectedPosition.fen());
  }

  const checkedSquare = board.locator(".cg-wrap cg-board square.check:visible");
  const checkedKing = board.locator(".cg-wrap piece.king.black");
  await expect(checkedSquare).toHaveCount(1);
  const [squareBounds, kingBounds] = await Promise.all([
    checkedSquare.boundingBox(),
    checkedKing.boundingBox(),
  ]);
  expect(squareBounds).not.toBeNull();
  expect(kingBounds).not.toBeNull();
  expect(
    Math.abs(
      squareBounds!.x + squareBounds!.width / 2 -
        (kingBounds!.x + kingBounds!.width / 2),
    ),
  ).toBeLessThan(1);
  expect(
    Math.abs(
      squareBounds!.y + squareBounds!.height / 2 -
        (kingBounds!.y + kingBounds!.height / 2),
    ),
  ).toBeLessThan(1);
  expect(await checkedSquare.evaluate((square) => getComputedStyle(square).backgroundImage)).toContain("radial-gradient");
  const playedSoundSources = await page.evaluate(
    () => (window as unknown as { __tempoAudioSources: string[] }).__tempoAudioSources,
  );
  expect(playedSoundSources.some((source) => source.includes("Check.wav"))).toBe(true);
  expect(playedSoundSources.some((source) => source.includes("Capture.mp3"))).toBe(false);

  await page.keyboard.press("f");
  await expect(board).toHaveAttribute("data-orientation", "black");
  await expect(checkedSquare).toHaveCount(1);
  const [flippedSquareBounds, flippedKingBounds] = await Promise.all([
    checkedSquare.boundingBox(),
    checkedKing.boundingBox(),
  ]);
  expect(flippedSquareBounds).not.toBeNull();
  expect(flippedKingBounds).not.toBeNull();
  expect(
    Math.abs(
      flippedSquareBounds!.x + flippedSquareBounds!.width / 2 -
        (flippedKingBounds!.x + flippedKingBounds!.width / 2),
    ),
  ).toBeLessThan(1);
  expect(
    Math.abs(
      flippedSquareBounds!.y + flippedSquareBounds!.height / 2 -
        (flippedKingBounds!.y + flippedKingBounds!.height / 2),
    ),
  ).toBeLessThan(1);

  await page
    .locator(".shared-board-toolbar .board-tools")
    .getByRole("button", { name: /Back/ })
    .click();
  expectedPosition.undo();
  await expect(board).toHaveAttribute("data-fen", expectedPosition.fen());
  await expect(checkedSquare).toHaveCount(0);
});

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
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible({ timeout: 30_000 });
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
  request,
}) => {
  // Prior fixture deletion schedules queue publication. Let that empty
  // generation settle before the UI import's own publication begins.
  await expect.poll(async () => {
    const response = await request.get(`${api}/queue/today`, { headers: { "X-Tempo-Work-Class": "background" } });
    if (response.status() === 503 && response.headers()["retry-after"]) return null;
    expect(response.ok()).toBe(true);
    const queue = await response.json();
    return { state: queue.projection.state, pending: queue.projection.refresh_pending, count: queue.cards.length };
  }, { timeout: 30_000, message: "Previous fixture publication settles empty before Black UI import" })
    .toEqual({ state: "ready", pending: 0, count: 0 });
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
  await expect(page.getByText(/2 cards are in today’s queue/)).toBeVisible({ timeout: 30_000 });
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
  const prompt = new Chess(
    (await page.locator(".board-frame").getAttribute("data-fen"))!,
  );
  const response = prompt.get("d4")
    ? { from: "d7" as const, to: "d5" as const }
    : { from: "e7" as const, to: "e5" as const };
  await move(page, response.from, response.to);
  await expect
    .poll(
      async () =>
        new Chess(
          (await page.locator(".board-frame").getAttribute("data-fen"))!,
        ).get(response.to)?.type,
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
