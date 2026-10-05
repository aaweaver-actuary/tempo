import { test, expect } from "./observability";
import { readFileSync } from "node:fs";
import { navigate, noPageOverflow } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import { expectedPieces, renderedPieces, playMove } from "./keyboard-fixtures";

// API route interception must remain deterministic after the app registers its shell worker.
test.use({ serviceWorkers: "block" });

test("critical navigation, board input, split and dialogs work across browser engines", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page);
  await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const surface = await page.locator(".cg-wrap").boundingBox();
  if (!surface) throw new Error("Board has no visible surface");
  for (const [file, rank] of [
    [4, 6],
    [4, 4],
  ])
    await page.mouse.click(
      surface.x + ((file + 0.5) * surface.width) / 8,
      surface.y + ((rank + 0.5) * surface.height) / 8,
    );
  await expect(board).toHaveAttribute("data-fen", /4P3/);
  const separator = page.getByRole("separator", { name: "Board size" });
  await separator.focus();
  await page.keyboard.press("ArrowRight");
  await expect(separator).toHaveAttribute("aria-valuenow", "48");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  const note = page.getByRole("textbox", { name: "Position comment" });
  await note.fill("Remember this position.");
  await page.getByRole("tab", { name: "Compare", exact: true }).click();
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  await expect(note).toHaveValue("Remember this position.");
  await navigate(page, "Repertoire");
  await noPageOverflow(page);
  const importButton = page.getByRole("button", { name: /Import PGN/ });
  await importButton.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(importButton).toBeFocused();
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Filter source" }),
  ).toBeVisible();
  await noPageOverflow(page);
});


test("tablet menu Escape restores the visible navigation trigger", async ({page}) => {
  await page.setViewportSize({width:768,height:1024}); await prepareVisualUI(page);
  const menu=page.locator(".tablet-navigation"); await menu.click();
  await page.locator("#workspace-menu").getByRole("button",{name:"Games",exact:true}).focus();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await expect(page.locator("#workspace-menu")).toHaveCount(0);
});

test("WebKit desktop prepared queue outage preserves the position and blocks grading until Retry", async ({ page }) => {
  const startingFen = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1";
  const card = { id: "webkit-live", queue_entry_id: 8123, start_fen: startingFen,
    moves: ["d7d5", "c2c4"], content_type: "opening", repertoire_name: "WebKit live card",
    repertoire_source: "PGN", trained_color: "black" };
  let liveRequestFails = false;
  let preparedRequests = 0;
  await page.route("**/api/queue/prepared?**", (route) => {
    preparedRequests += 1;
    return route.fulfill({ status: 500, body: "Desktop must not prepare a phone queue" });
  });
  let queueRequests = 0;
  await page.route("**/api/queue/window**", (route) => {
    queueRequests += 1;
    return liveRequestFails
    ? route.abort("failed")
    : route.fulfill({ json: { cards: [card], count: 1 } });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "WebKit live card" })).toBeVisible();
  await page.evaluate(async (savedCard) => {
    await new Promise<void>((resolve, reject) => {
      const opened = indexedDB.open("tempo-offline-training", 2);
      opened.onupgradeneeded = () => opened.result.createObjectStore("training");
      opened.onerror = () => reject(opened.error);
      opened.onsuccess = () => {
        const transaction = opened.result.transaction("training", "readwrite");
        transaction.objectStore("training").put({
          localDate: new Date().toLocaleDateString("en-CA"), preparedAt: new Date().toISOString(),
          cards: [savedCard], attempts: [], nextTemporaryId: -1,
        }, "prepared-daily-queue");
        transaction.oncomplete = () => { opened.result.close(); resolve(); };
        transaction.onerror = () => reject(transaction.error);
      };
    });
  }, card);
  const originalFen = await page.locator(".board-frame").getAttribute("data-fen");
  const requestsBeforeFailure = queueRequests;
  await page.bringToFront();
  liveRequestFails = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => queueRequests).toBeGreaterThan(requestsBeforeFailure);
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", originalFen!);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "false");
  await expect(page.getByRole("button", { name: "Correct" })).toBeDisabled();
  await expect(page.getByText("Offline queue", { exact: true })).toHaveCount(0);
  liveRequestFails = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  expect(preparedRequests).toBe(0);
});

test("capture modal keeps incomplete setup draggable and restores focus across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Tactics");
  const launch = page.getByRole("button", { name: "Capture tactic", exact: true });
  await launch.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Place R", exact: true }).click();
  const board = dialog.locator(".board-frame");
  await board.scrollIntoViewIfNeeded();
  const surface = await board.locator(".cg-wrap").boundingBox();
  if (!surface) throw new Error("Capture board has no surface");
  const x = surface.x + surface.width / 16;
  await page.mouse.click(x, surface.y + 15 * surface.height / 16);
  await dialog.getByRole("button", { name: "Move pieces", exact: true }).click();
  await board.scrollIntoViewIfNeeded();
  await page.mouse.move(x, surface.y + 15 * surface.height / 16); await page.mouse.down();
  await page.mouse.move(x, surface.y + 9 * surface.height / 16, { steps: 8 }); await page.mouse.up();
  await expect(board).toHaveAttribute("data-fen", "8/8/8/8/R7/8/8/8 w - - 0 1");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0); await expect(launch).toBeFocused();
});

test("capture accepts SAN from Black's perspective across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Tactics");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog"), board = dialog.locator(".board-frame");
  await dialog.getByLabel("FEN", { exact: true }).fill("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1");
  await dialog.getByRole("button", { name: "Solution", exact: true }).click();
  await dialog.getByLabel("SAN moves").fill("1...e5");
  await dialog.getByLabel("SAN moves").press("Enter");
  await expect(board).toHaveAttribute("data-orientation", "black");
  await expect(board).toHaveAttribute("data-fen", "rnbqkbnr/pppp1ppp/8/4p3/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2");
  await expect(dialog.getByLabel("SAN moves")).toBeFocused();
  await dialog.getByLabel("SAN moves").fill("Nf3");
  await dialog.getByRole("button", { name: "Add moves", exact: true }).click();
  await expect(board).toHaveAttribute("data-fen", "rnbqkbnr/pppp1ppp/8/4p3/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 2");
  await expect(board).toHaveAttribute("data-orientation", "black");
});

test("Chess.com puzzle import starts at the solver position across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Tactics");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog"), board = dialog.locator(".board-frame");
  await dialog.getByRole("button", { name: "Paste Chess.com puzzle PGN" }).click();
  await dialog.getByLabel("Chess.com puzzle PGN").fill(readFileSync("tests/fixtures/chesscom-puzzle-rush.pgn", "utf8"));
  await dialog.getByRole("button", { name: "Load PGN", exact: true }).click();
  const startingFen = "r1b2rk1/ppq2p1p/2np1Qp1/2b5/2B1Pp2/1P6/P1PP2PP/R1B1K1NR b KQ - 0 1";
  await expect(board).toHaveAttribute("data-fen", startingFen);
  await expect(board).toHaveAttribute("data-orientation", "black");
  await board.click();
  await page.keyboard.press("End");
  await expect(board).toHaveAttribute("data-fen", "r1b2rk1/ppq2p1p/3p2p1/8/2BnPp2/1P6/P1PP2PP/R1B1K1NR w KQ - 0 3");
  await page.keyboard.press("Home");
  await expect(board).toHaveAttribute("data-fen", startingFen);
  await expect(board).toHaveAttribute("data-orientation", "black");
});

test("contextual board keys and nested popup Escape work across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  const root = await board.getAttribute("data-fen");
  await playMove(page, board, "e2", "e4");
  await expect(board).not.toHaveAttribute("data-fen", root!);
  const decision = await board.getAttribute("data-fen");
  await page.keyboard.press("ArrowLeft"); await expect(board).toHaveAttribute("data-fen", root!);
  await page.keyboard.press("End"); await expect(board).toHaveAttribute("data-fen", decision!);
  await page.keyboard.press("f"); await expect(board).toHaveAttribute("data-orientation", "black");
  await page.keyboard.press("Home"); await page.keyboard.press("r");
  await expect(board).toHaveAttribute("data-orientation", "white");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision!));
  await page.getByRole("tab", { name: "Repertoire", exact: true }).click();
  const search = page.getByRole("button", { name: /Position search/ });
  await search.click(); await page.keyboard.press("Escape"); await expect(search).toBeFocused();
  await navigate(page, "Tactics");
  const launch = page.getByRole("button", { name: "Capture tactic", exact: true });
  await launch.click();
  const capture = page.getByRole("dialog", { name: "Capture tactic" });
  const background = page.locator(".persistent-board-shell .board-frame");
  const backgroundOrientation = await background.getAttribute("data-orientation");
  const popupBoard = capture.locator(".board-frame");
  const helpButton = capture.getByRole("button", { name: "Keyboard shortcuts", exact: true });
  await popupBoard.locator("..").focus();
  await page.keyboard.press("f");
  await expect(popupBoard).toHaveAttribute("data-orientation", "black");
  await expect(background).toHaveAttribute("data-orientation", backgroundOrientation!);
  await helpButton.click();
  const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(help).toBeVisible(); await page.keyboard.press("Escape");
  await expect(help).toHaveCount(0); await expect(capture).toBeVisible(); await expect(helpButton).toBeFocused();
  await page.keyboard.press("Escape"); await expect(capture).toHaveCount(0); await expect(launch).toBeFocused();
});

test("queue retirement during a held opening drag preserves active board and accepts one drop", async ({ page }) => {
  await prepareVisualUI(page, false);
  const board = page.locator(".board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const originalFen = await board.getAttribute("data-fen");
  const surface = (await page.locator(".cg-wrap").boundingBox())!;
  const origin = { x: surface.x + 4.5 * surface.width / 8, y: surface.y + 6.5 * surface.height / 8 };
  const destination = { x: origin.x, y: surface.y + 4.5 * surface.height / 8 };
  await page.mouse.move(origin.x, origin.y); await page.mouse.down();
  await page.mouse.move(origin.x + 20, origin.y - 25, { steps: 4 });
  await expect(page.locator("piece.dragging")).toHaveCount(1);
  await page.locator("piece.dragging").evaluate(element => element.setAttribute("data-original-drag", "true"));
  let refreshed = false;
  await page.route("**/api/queue/window?**", route => {
    refreshed = true;
    return route.fulfill({ json: { count: 1, cards: [{ id: "future-after-limit", queue_entry_id: 901,
      start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      moves: ["d2d4"], content_type: "opening", repertoire_name: "Future card", repertoire_source: "PGN" }] } });
  });
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => refreshed).toBe(true);
  await expect(page.locator(".session-count strong")).toHaveText("2");
  await expect(page.locator('piece.dragging[data-original-drag="true"]')).toHaveCount(1);
  await expect(board).toHaveAttribute("data-fen", originalFen!);
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await page.mouse.move(destination.x, destination.y, { steps: 4 }); await page.mouse.up();
  await expect(board).toHaveAttribute("data-fen", /4p3\/4P3/);
  await expect(page.getByText("Spanish opening", { exact: true }).first()).toBeVisible();
});

test("phone opening identity and move input work across browser engines", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page);
  await expect(page.locator(".phone-study-heading h2")).toHaveText("Spanish opening");
  const board = page.locator(".persistent-board-shell .board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await playMove(page, board, "e2", "e4");
  await expect(board).toHaveAttribute("data-fen", / b KQkq /);
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(await board.getAttribute("data-fen") ?? ""));
  const menu = page.locator(".phone-study-actions");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: /Restart/ }).click();
  await expect(menu).not.toHaveAttribute("open", "");
  await expect(menu.locator("summary")).toBeFocused();
  await noPageOverflow(page);
});
